import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from loguru import logger

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models.entities import MasterProduct, ChannelPost, Supplier, ProductSupplierLink, BotConfig
from src.core.models.enums import SupplierStatus
from src.core.adapters.registry import AdapterRegistry
from src.core.services.pricing_service import pricing_service
from src.core.services.proof_service import (
    proof_service, mask_string, mask_username,
    format_purchase_proof_card, format_deposit_proof_card,
)
from src.core.redis import event_bus


class ChannelSyncWorker:
    """
    Zero-Touch Dynamic Stock Sync & Dual-Channel Broadcaster:
    1. Main Channel: Syncs product cards, live price adjustments, and restock alerts.
    2. Live Proofs Channel: Posts real-time masked proof cards for completed orders and deposits.
    """

    def __init__(self):
        self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None
        self.channel_id = settings.TELEGRAM_PUBLIC_CHANNEL_ID

    async def run(self):
        if not self.bot or not self.channel_id:
            logger.warning("Bot token or Channel ID not set. Channel Sync Worker idling.")
            return

        logger.info(f"Channel Sync Worker started. Main Target: {self.channel_id}")
        bot_info = await self.bot.get_me()
        bot_username = bot_info.username

        # Start live proof broadcast listener in the background
        proof_task = asyncio.create_task(self.listen_for_proof_events(bot_username))

        try:
            while True:
                # 1. Zero-Touch Dynamic Stock & Cost Passthrough from Supplier APIs
                await self.sync_upstream_supplier_stocks()
                # 2. Sync Channel Posts with live price & stock status
                await self.sync_channel_posts(bot_username)
                await asyncio.sleep(60)
        finally:
            proof_task.cancel()

    async def listen_for_proof_events(self, bot_username: str):
        """Listens on Redis for completed orders/deposits and posts masked proof cards to proof_channel_id."""
        redis = event_bus._redis
        if not redis:
            return

        pubsub = redis.pubsub()
        await pubsub.subscribe("channel:broadcast_proof")
        logger.info("Live Proofs Broadcaster subscribed to 'channel:broadcast_proof'.")

        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    try:
                        data = json.loads(msg["data"])
                        await self.post_masked_proof_card(bot_username, data)
                    except Exception as e:
                        logger.error(f"Error handling proof broadcast: {e}")
        except asyncio.CancelledError:
            await pubsub.unsubscribe("channel:broadcast_proof")
            await pubsub.close()

    async def post_masked_proof_card(self, bot_username: str, data: dict):
        """Formats and delivers a high-conversion social proof card to the dedicated proof channel."""
        force = bool(data.get("force"))
        async with AsyncSessionLocal() as session:
            if not force:
                # Prefer proof_channel_auto_post; fall back to legacy auto_post_proofs_enabled
                auto_row = (
                    await session.execute(
                        select(BotConfig).where(
                            BotConfig.key.in_(["proof_channel_auto_post", "auto_post_proofs_enabled"])
                        )
                    )
                ).scalars().all()
                by_key = {r.key: r.value for r in auto_row}
                auto_val = by_key.get("proof_channel_auto_post")
                if auto_val is None:
                    auto_val = by_key.get("auto_post_proofs_enabled")
                if auto_val is not None:
                    if isinstance(auto_val, str) and auto_val.lower() in ("false", "0", "no"):
                        return
                    if isinstance(auto_val, bool) and not auto_val:
                        return

            proof_ch_stmt = select(BotConfig).where(BotConfig.key == "proof_channel_id")
            proof_ch_row = (await session.execute(proof_ch_stmt)).scalar_one_or_none()
            proof_ch_id = proof_ch_row.value if proof_ch_row else settings.TELEGRAM_PROOF_CHANNEL_ID
            banner = await proof_service.get_banner_config(session)

        if not proof_ch_id:
            return

        event_type = data.get("type", "order_completed")
        product_id = data.get("product_id")

        if event_type == "trust_channel":
            from src.core.services.proof_service import format_trust_channel_card
            text = data.get("formatted_html") or format_trust_channel_card(
                data, stage=data.get("stage") or "delivered"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🛍️ اطلب الآن",
                        url=f"https://t.me/{bot_username}?start=buy_{product_id}" if product_id else f"https://t.me/{bot_username}",
                    )],
                ]
            )
        elif event_type == "order_completed":
            text = format_purchase_proof_card(data)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🏪 اشترِ الآن",
                        url=f"https://t.me/{bot_username}?start=buy_{product_id}" if product_id else f"https://t.me/{bot_username}",
                    )],
                    [InlineKeyboardButton(text="🛒 فتح المتجر", url=f"https://t.me/{bot_username}")],
                ]
            )
        elif event_type == "referral_gift":
            from src.core.services.proof_service import mask_username as _mask_user, mask_string as _mask_id
            masked_user = _mask_user(data.get("customer_username"), data.get("customer_first_name"))
            masked_id = _mask_id(str(data.get("order_number") or "GIFT"), prefix_len=4, suffix_len=3)
            reward = data.get("product_title") or data.get("reward_name") or "جائزة مجانية"
            text = (
                f"🎁 **جائزة مجانية تم تسليمها!**\n"
                f"🎁 Free Reward Delivered!\n\n"
                f"📌 **ID:** `{masked_id}`\n"
                f"👤 **الفائز:** `{masked_user}`\n"
                f"🏆 **الجائزة:** {reward}\n"
                f"✅ تم التسليم عبر حملة الدعوة"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🎁 اربح حساب مجاني",
                        url=f"https://t.me/{bot_username}?start=giveaway",
                    )],
                ]
            )
        elif event_type == "verified_review":
            order_num = data.get("order_number", "ORD-XXXX")
            masked_ref = mask_string(order_num, prefix_len=8, suffix_len=2)
            masked_user = mask_username(data.get("customer_username"), data.get("customer_first_name"))
            product_title = data.get("product_title", "منتج رقمي")
            product_emoji = data.get("product_emoji", "⭐")
            rating = int(data.get("rating", 5))
            comment = data.get("comment", "")
            stars_str = "⭐" * rating
            comment_str = f"\n💬 **رأي العميل:** \"{comment}\"" if comment else ""
            text = (
                f"🌟 **تقييم عميل موثق | VERIFIED BUYER REVIEW** {stars_str}\n\n"
                f"👤 **العميل:** `{masked_user}`\n"
                f"📦 **المنتج:** {product_emoji} {product_title}\n"
                f"🆔 **الطلب:** `{masked_ref}`\n"
                f"⭐ **التقييم:** {stars_str} ({rating}/5){comment_str}\n"
                f"✅ **الحالة:** تم التحقق من عملية الشراء والتسليم التلقائي"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛍️ تصفح المتجر وشراء", url=f"https://t.me/{bot_username}")],
                ]
            )
        else:  # deposit_confirmed
            text = format_deposit_proof_card(data)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 اشحن وتسوق الآن", url=f"https://t.me/{bot_username}")],
                ]
            )

        try:
            chat_target = int(proof_ch_id) if str(proof_ch_id).lstrip("-").isdigit() else proof_ch_id
            parse_mode = ParseMode.HTML if event_type == "trust_channel" else ParseMode.MARKDOWN
            use_banner = (
                banner
                and banner.get("enabled")
                and (banner.get("file_id") or banner.get("image_url"))
                and event_type in ("order_completed", "deposit_confirmed")
            )
            if use_banner:
                photo = banner.get("file_id") or banner.get("image_url")
                await self.bot.send_photo(
                    chat_id=chat_target,
                    photo=photo,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=kb,
                )
            else:
                await self.bot.send_message(
                    chat_id=chat_target,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=kb,
                )
            logger.info(f"Broadcasted live masked proof to channel {proof_ch_id}")
        except Exception as e:
            logger.warning(f"Failed to post proof card to channel {proof_ch_id}: {e}")

    async def sync_upstream_supplier_stocks(self):
        """Pulls fresh catalogs from all active suppliers to update stock & cost passthrough."""
        async with AsyncSessionLocal() as session:
            # Eager-load every supplier's links in a single batch query instead of
            # issuing one SELECT per supplier inside the loop (classic N+1).
            stmt = (
                select(Supplier)
                .where(Supplier.status == SupplierStatus.ACTIVE)
                .options(selectinload(Supplier.product_links))
            )
            res = await session.execute(stmt)
            suppliers = res.scalars().all()

            for supplier in suppliers:
                try:
                    adapter = AdapterRegistry.create_from_supplier(supplier)
                    catalog = await adapter.fetch_products()
                    catalog_map = {item.supplier_product_id: item for item in catalog}

                    links = supplier.product_links

                    updated_count = 0
                    restocked_master_ids = []
                    for link in links:
                        item = catalog_map.get(link.supplier_product_id)
                        if item:
                            was_oos = not bool(link.is_in_stock) or int(link.available_quantity or 0) == 0
                            now_in = bool(item.is_in_stock) and (
                                item.available_quantity is None or int(item.available_quantity or 0) > 0
                            )
                            has_changed = (
                                link.is_in_stock != item.is_in_stock or
                                link.last_known_cost != item.cost or
                                link.available_quantity != item.available_quantity
                            )
                            if has_changed:
                                link.is_in_stock = item.is_in_stock
                                link.last_known_cost = item.cost
                                link.available_quantity = item.available_quantity
                                link.last_synced_at = datetime.now(timezone.utc)
                                updated_count += 1
                                if was_oos and now_in and link.master_product_id:
                                    restocked_master_ids.append(link.master_product_id)
                        else:
                            # Item no longer in supplier catalog -> mark out of stock
                            if link.is_in_stock:
                                link.is_in_stock = False
                                link.available_quantity = 0
                                link.last_synced_at = datetime.now(timezone.utc)
                                updated_count += 1

                    if updated_count > 0:
                        await session.commit()
                        logger.info(f"Zero-touch stock sync: updated {updated_count} SKUs for supplier {supplier.name}")
                        if restocked_master_ids:
                            try:
                                from src.core.services.stock_alert_service import stock_alert_service
                                n = await stock_alert_service.notify_restocked_products(
                                    session, restocked_master_ids
                                )
                                if n:
                                    logger.info(f"Stock alerts fired for {n} subscribers")
                            except Exception as alert_err:
                                logger.warning(f"Stock alert notify failed: {alert_err}")
                except Exception as e:
                    logger.warning(f"Failed to fetch live catalog for supplier {supplier.name}: {e}")

    async def sync_channel_posts(self, bot_username: str):
        """Renders live product posts or edits existing ones when price or stock shifts."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(MasterProduct)
                .where(
                    and_(
                        MasterProduct.is_active == True,
                        MasterProduct.is_published_to_channel == True
                    )
                )
                .options(
                    selectinload(MasterProduct.channel_post),
                    selectinload(MasterProduct.supplier_links).selectinload(ProductSupplierLink.supplier),
                )
            )
            res = await session.execute(stmt)
            products = res.scalars().all()

            # Pre-fetch pricing rules to prevent N+1 queries
            from src.core.models.entities import PricingRule
            from src.core.models.enums import PricingScope
            rules_stmt = select(PricingRule).where(PricingRule.is_active == True).order_by(PricingRule.priority.desc())
            all_rules = (await session.execute(rules_stmt)).scalars().all()

            from src.core.services.pricing_service import pick_cheapest_sellable_link
            for p in products:
                # Ensure supplier relationship is available for ACTIVE status filter
                winner = pick_cheapest_sellable_link(list(p.supplier_links))
                if winner:
                    cost = winner.last_known_cost
                    is_in_stock = True
                else:
                    cost = Decimal("0.0")
                    is_in_stock = False

                matched_rule = next((r for r in all_rules if r.scope == PricingScope.PRODUCT and r.scope_id == p.id), None)
                if not matched_rule:
                    matched_rule = next((r for r in all_rules if r.scope == PricingScope.CATEGORY and r.scope_id == p.category_id), None)
                if not matched_rule:
                    matched_rule = next((r for r in all_rules if r.scope == PricingScope.GLOBAL), None)

                final_price, _, _ = pricing_service.calculate_price_from_cost(cost, matched_rule)

                # Keep MasterProduct cached price and stock updated
                p.cached_final_price = final_price
                p.cached_in_stock = is_in_stock
                p.cache_refreshed_at = datetime.now(timezone.utc)

                stock_qty = 0
                if winner and winner.available_quantity is not None:
                    stock_qty = int(winner.available_quantity)
                elif is_in_stock:
                    stock_qty = 1
                stock_str = "متوفر للتسليم الفوري 🟢" if is_in_stock else "نفد من المخزون مؤقتاً 🔴"
                emoji = getattr(p, "custom_emoji", "✨") or "✨"
                desc = getattr(p, "custom_description_ar", None) or p.description_ar or "تسليم آلي وفوري للحساب أو المفتاح."
                warranty = int(getattr(p, "warranty_days", 30) or 30)
                bulk_caption = pricing_service.format_bulk_tiers_caption(
                    final_price, getattr(p, "bulk_pricing_tiers", None)
                )
                buy_btn = f"🏪 اشترِ الآن — ${final_price}"

                caption = (
                    f"{emoji} **{p.title_ar}**\n\n"
                    f"{desc}\n\n"
                    f"💰 **السعر:** `${final_price}`\n"
                    f"🛡️ **الضمان:** `{warranty}` يوم\n"
                    f"📦 **المخزون:** {stock_str}\n"
                )
                if bulk_caption:
                    caption += f"\n{bulk_caption}\n"
                caption += "\n⚡ للشراء الفوري الآلي اضغط أدناه:"

                deep_link = f"https://t.me/{bot_username}?start=buy_{p.id}"
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=buy_btn[:64], url=deep_link)]
                    ]
                )

                post = p.channel_post
                if not post:
                    # New publication
                    try:
                        if p.photo_url:
                            msg = await self.bot.send_photo(
                                chat_id=self.channel_id,
                                photo=p.photo_url,
                                caption=caption,
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=kb
                            )
                        else:
                            msg = await self.bot.send_message(
                                chat_id=self.channel_id,
                                text=caption,
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=kb
                            )
                        new_post = ChannelPost(
                            master_product_id=p.id,
                            telegram_chat_id=msg.chat.id,
                            telegram_message_id=msg.message_id,
                            last_rendered_text=caption,
                            last_rendered_price=final_price,
                            last_rendered_stock_status=is_in_stock,
                            last_rendered_stock_qty=stock_qty,
                        )
                        session.add(new_post)
                        await session.commit()
                        # First publish → "متوفر الآن" restock-style card
                        if is_in_stock:
                            try:
                                restock_caption = proof_service.format_restock(
                                    title=p.title_ar,
                                    emoji=emoji,
                                    added_qty=stock_qty or 1,
                                    current_stock=stock_qty or 1,
                                    unit_price=final_price,
                                    warranty_days=warranty,
                                    bulk_caption=bulk_caption,
                                    is_new=True,
                                )
                                await self.bot.send_message(
                                    chat_id=self.channel_id,
                                    text=restock_caption,
                                    parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text=buy_btn[:64], url=deep_link)]
                                    ]),
                                )
                            except Exception as r_err:
                                logger.warning(f"Failed to send new-stock card for {p.title_ar}: {r_err}")
                        logger.info(f"Published new post for {p.title_ar} to channel.")
                    except Exception as e:
                        logger.error(f"Failed to publish {p.title_ar} to channel: {e}")
                else:
                    # Update existing post if price OR stock status changed
                    price_changed = post.last_rendered_price != final_price
                    prev_qty = getattr(post, "last_rendered_stock_qty", None)
                    stock_changed = getattr(post, "last_rendered_stock_status", True) != is_in_stock
                    qty_increased = (
                        prev_qty is not None
                        and stock_qty > int(prev_qty)
                        and is_in_stock
                    )

                    if price_changed or stock_changed or qty_increased or post.needs_update:
                        try:
                            # Restock: 0→N or stock qty increase
                            was_oos = getattr(post, "last_rendered_stock_status", True) is False
                            if (was_oos and is_in_stock) or qty_increased:
                                try:
                                    added = stock_qty - int(prev_qty or 0)
                                    if added <= 0:
                                        added = stock_qty or 1
                                    restock_caption = proof_service.format_restock(
                                        title=p.title_ar,
                                        emoji=emoji,
                                        added_qty=added,
                                        current_stock=stock_qty or added,
                                        unit_price=final_price,
                                        warranty_days=warranty,
                                        bulk_caption=bulk_caption,
                                        is_new=False,
                                    )
                                    await self.bot.send_message(
                                        chat_id=self.channel_id,
                                        text=restock_caption,
                                        parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                            [InlineKeyboardButton(text=buy_btn[:64], url=deep_link)]
                                        ]),
                                    )
                                    logger.info(f"Published restock alert for {p.title_ar}")
                                except Exception as r_err:
                                    logger.warning(f"Failed to send restock alert for {p.title_ar}: {r_err}")

                            if p.photo_url:
                                await self.bot.edit_message_caption(
                                    chat_id=post.telegram_chat_id,
                                    message_id=post.telegram_message_id,
                                    caption=caption,
                                    parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb
                                )
                            else:
                                await self.bot.edit_message_text(
                                    chat_id=post.telegram_chat_id,
                                    message_id=post.telegram_message_id,
                                    text=caption,
                                    parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb
                                )
                            post.last_rendered_price = final_price
                            post.last_rendered_stock_status = is_in_stock
                            post.last_rendered_stock_qty = stock_qty
                            post.last_rendered_text = caption
                            post.needs_update = False
                            post.last_edited_at = datetime.now(timezone.utc)
                            await session.commit()
                            logger.info(f"Updated channel message for {p.title_ar} (Price: ${final_price}, InStock: {is_in_stock})")
                            
                            # Broadcast real-time stock change event across Redis bus
                            await event_bus.publish("products:stock_update", {
                                "product_id": str(p.id),
                                "is_in_stock": is_in_stock,
                                "final_price": float(final_price),
                            })
                        except Exception as e:
                            logger.error(f"Failed to edit channel message for {p.title_ar}: {e}")

            await session.commit()


if __name__ == "__main__":
    worker = ChannelSyncWorker()
    asyncio.run(worker.run())
