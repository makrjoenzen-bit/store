import asyncio
import json
from html import escape as html_escape

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from src.config import settings, validate_production_secrets
from src.bot.fsm_storage import build_fsm_storage
from src.bot.handlers import shop, support
from src.bot.middlewares.rate_limit import AntiSpamRateLimitMiddleware
from src.bot.middlewares.force_sub import ForceSubscribeMiddleware
from src.bot.keyboards.inline import order_rating_keyboard
from src.core.redis import get_redis_client


async def listen_bot_events(bot: Bot):
    """Background listener for events published to Bot from the JIT Executor & Backend."""
    redis = get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(
        "bot:deliver_product",
        "bot:notify_refund",
        "bot:notify_cashback",
        "bot:notify_referral_commission",
        "bot:ticket_reply",
        "bot:send_message",
        "bot:checkout_reminder",
        "bot:stock_alert",
        "bot:renewal_reminder",
        "config:updates",
    )
    logger.info(
        "Bot Redis event listener started "
        "(deliver_product, notify_refund, checkout_reminder, send_message, config:updates)."
    )

    try:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                channel = msg["channel"]
                data = json.loads(msg["data"])

                if channel == "bot:deliver_product":
                    order_id = data.get("order_id")
                    tg_id = data.get("telegram_id")
                    order_num = data.get("order_number")
                    title = data.get("product_title")
                    payload = data.get("payload")
                    quantity = data.get("quantity", 1)
                    warranty = data.get("warranty_expires_at")
                    invoice_number = data.get("formal_invoice_number")

                    invoice_str = f"🧾 **رقم الفاتورة الرسمية:** `{invoice_number}`\n" if invoice_number else ""
                    warranty_str = f"🛡️ **فترة الضمان:** سارٍ حتى `{warranty}`\n" if warranty else ""
                    qty_str = f"📦 **الكمية:** {quantity} قطعة\n" if quantity and quantity > 1 else ""

                    text = (
                        f"✅ تمت العملية بنجاح\n\n"
                        f"🎉 **مبروك! تم تنفيذ وتسليم طلبك بنجاح!**\n\n"
                        f"📦 **المنتج:** {title}\n"
                        f"{qty_str}"
                        f"🔖 **رقم الطلب:** `{order_num}`\n"
                        f"{invoice_str}"
                        f"{warranty_str}\n"
                        f"🔑 **بيانات التفعيل / المفاتيح:**\n"
                        f"```\n{payload}\n```\n\n"
                        f"شكراً لتعاملك معنا! 🌟 نتمنى لك تجربة ممتعة."
                    )
                    kb = None
                    if order_id:
                        kb = order_rating_keyboard(str(order_id))
                    try:
                        from src.bot.effects import EFFECT_CELEBRATION, send_with_effect
                        await send_with_effect(
                            bot,
                            chat_id=tg_id,
                            text=text,
                            effect_id=EFFECT_CELEBRATION,
                            reply_markup=kb,
                            parse_mode=ParseMode.HTML,
                        )

                        # Bulk packaging: if quantity >= 5, package into .txt file and send via send_document
                        if quantity and quantity >= 5 and payload:
                            from aiogram.types import BufferedInputFile
                            file_bytes = payload.encode('utf-8')
                            doc_file = BufferedInputFile(file_bytes, filename=f"order_{order_num}_keys.txt")
                            await bot.send_document(
                                chat_id=tg_id,
                                document=doc_file,
                                caption=f"📁 ملف المفاتيح المجمعة للطلب #{order_num} ({quantity} قطعة)"
                            )
                    except Exception as e:
                        logger.error(f"Failed to deliver product message to {tg_id}: {e}")

                elif channel == "bot:send_message":
                    tg_id = data.get("telegram_id")
                    msg_text = data.get("text")
                    effect_id = data.get("effect_id")
                    if tg_id and msg_text:
                        try:
                            from src.bot.effects import send_with_effect
                            await send_with_effect(
                                bot,
                                chat_id=tg_id,
                                text=msg_text,
                                effect_id=effect_id,
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception as e:
                            logger.error(f"Failed to send bot message to {tg_id}: {e}")

                elif channel == "bot:stock_alert":
                    tg_id = data.get("telegram_id")
                    product_id = data.get("product_id")
                    title = data.get("product_title") or "منتج"
                    emoji = data.get("emoji") or "✨"
                    if tg_id and product_id:
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="🏪 اشترِ الآن",
                                callback_data=f"prod:{product_id}",
                            )]
                        ])
                        try:
                            await bot.send_message(
                                chat_id=tg_id,
                                text=(
                                    f"🔔 **عاد للمخزون!**\n\n"
                                    f"{emoji} **{title}** متوفر الآن — اطلبه قبل نفاده."
                                ),
                                parse_mode=ParseMode.HTML,
                                reply_markup=kb,
                            )
                        except Exception as e:
                            logger.error(f"Failed stock alert to {tg_id}: {e}")

                elif channel == "bot:renewal_reminder":
                    tg_id = data.get("telegram_id")
                    product_id = data.get("product_id")
                    text = data.get("text") or "تذكير تجديد الاشتراك"
                    if tg_id and product_id:
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="🔄 تجديد الاشتراك الآن",
                                callback_data=f"prod:{product_id}",
                            )]
                        ])
                        try:
                            await bot.send_message(
                                chat_id=tg_id, text=text,
                                parse_mode=ParseMode.HTML, reply_markup=kb,
                            )
                        except Exception as e:
                            logger.error(f"Failed renewal reminder to {tg_id}: {e}")

                elif channel == "bot:ticket_reply":
                    tg_id = data.get("telegram_id")
                    ticket_id = data.get("ticket_id")
                    ticket_short = str(ticket_id)[:8] if ticket_id else ""
                    admin_reply = data.get("reply_text")

                    text = (
                        f"📩 **رد جديد من فريق الدعم بخصوص تذكرتك #T-{ticket_short}:**\n\n"
                        f"{admin_reply}\n\n"
                        f"نتمنى أن يكون هذا الرد قد ساعدك! لأي استفسار إضافي نحن في خدمتك دائماً."
                    )
                    try:
                        await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.error(f"Failed to deliver ticket reply to {tg_id}: {e}")

                elif channel == "bot:notify_refund":
                    tg_id = data.get("telegram_id")
                    order_num = data.get("order_number")
                    amt = data.get("amount")

                    text = (
                        f"❌ تعذر إتمام العملية — تم إعادة المبلغ إلى محفظتك تلقائياً\n\n"
                        f"⚠️ **إشعار بخصوص الطلب #{order_num}:**\n\n"
                        f"نعتذر منك، تعذر استكمال الطلب من طرف المورد حالياً.\n"
                        f"💰 تم استرجاع كامل المبلغ (`${amt}`) مباشرة إلى **رصيد محفظتك** داخل البوت.\n\n"
                        f"يمكنك استخدامه في أي طلب آخر أو التواصل مع الدعم للمساعدة."
                    )
                    try:
                        await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.error(f"Failed to send refund notice to {tg_id}: {e}")

                elif channel == "bot:notify_cashback":
                    tg_id = data.get("telegram_id")
                    order_num = data.get("order_number")
                    amt = data.get("amount")
                    pct = data.get("percent")

                    text = (
                        f"🎁 **مكافأة استرداد نقدي (Cashback)!**\n\n"
                        f"تم إضافة كاش باك بنسبة **{pct}%** بقيمة **`${amt} USDT`** إلى محفظتك بنجاح!\n"
                        f"🔖 الطلب المرتبط: `#{order_num}`\n\n"
                        f"يمكنك استخدام هذا الرصيد في أي عملية شراء قادمة فوراً 💳"
                    )
                    try:
                        await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.error(f"Failed to send cashback notice to {tg_id}: {e}")

                elif channel == "bot:notify_referral_commission":
                    tg_id = data.get("telegram_id")
                    amt = data.get("amount")
                    pct = data.get("percent")
                    referee = data.get("referee_name", "أحد أصدقائك")
                    order_num = data.get("order_number")

                    text = (
                        f"🤝 **عمولة إحالة جديدة في رصيدك!**\n\n"
                        f"قام صديقك ({referee}) بإتمام طلب جديد `#{order_num}` بنجاح!\n"
                        f"💰 ربحك من العمولة ({pct}%): **`${amt} USDT`**\n"
                        f"تم إيداع المبلغ تلقائياً في رصيد محفظتك! استمر بمشاركة رابطك واكسب المزيد 🚀"
                    )
                    try:
                        await bot.send_message(chat_id=tg_id, text=text, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.error(f"Failed to send referral commission notice to {tg_id}: {e}")

                elif channel == "bot:checkout_reminder":
                    tg_id = data.get("telegram_id")
                    msg_text = data.get("text")
                    order_id = data.get("order_id") or ""
                    if tg_id and msg_text:
                        rows = []
                        if order_id:
                            rows.append([
                                InlineKeyboardButton(
                                    text="💳 إكمال الدفع الآن",
                                    callback_data=f"pay_options:{order_id}",
                                )
                            ])
                            rows.append([
                                InlineKeyboardButton(
                                    text="❌ إلغاء الطلب",
                                    callback_data=f"cancel_order:{order_id}",
                                )
                            ])
                        kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
                        try:
                            await bot.send_message(
                                chat_id=tg_id,
                                text=msg_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=kb,
                            )
                        except Exception as e:
                            logger.error(f"Failed to send checkout reminder to {tg_id}: {e}")

                elif channel == "config:updates":
                    config_type = data.get("config_type")
                    item_id = data.get("item_id")
                    try:
                        from src.core.redis import event_bus as _eb
                        await _eb.invalidate_pricing_caches()
                        await _eb.invalidate_bot_config_caches()
                    except Exception as e:
                        logger.debug(f"Config cache invalidate on event failed: {e}")
                    logger.info(
                        f"Config change received: type={config_type} item_id={item_id}. "
                        f"Caches invalidated; live DB reads on next request."
                    )
    except asyncio.CancelledError:
        await pubsub.unsubscribe()
        await pubsub.close()


async def main():
    validate_production_secrets()
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN is not set. Bot process cannot start.")
        return

    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=build_fsm_storage())

    # Register anti-spam and uncompleted order quota middleware
    dp.message.middleware(AntiSpamRateLimitMiddleware())
    dp.callback_query.middleware(AntiSpamRateLimitMiddleware())

    # Register mandatory channel subscription gate middleware
    dp.message.middleware(ForceSubscribeMiddleware())
    dp.callback_query.middleware(ForceSubscribeMiddleware())

    # Register routers (shop/support FSM handlers before free-text AI concierge)
    from src.bot.handlers import ai_concierge as ai_concierge_handler
    dp.include_router(shop.router)
    dp.include_router(support.router)
    dp.include_router(ai_concierge_handler.router)

    # Start Redis events listener
    event_task = asyncio.create_task(listen_bot_events(bot))

    logger.info("Starting Telegram Bot long-polling...")
    try:
        await dp.start_polling(bot)
    finally:
        event_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
