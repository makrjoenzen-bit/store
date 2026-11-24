"""Admin Bot — Bot settings: Sham Cash, payment rails, force-join, welcome, maintenance."""
from __future__ import annotations

from typing import Any

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.core.models.entities import BotConfig
from src.core.redis import event_bus

router = Router(name="admin_settings")


class SettingsFSM(StatesGroup):
    sham_account = State()
    sham_instructions = State()
    sham_qr = State()
    sham_recipient = State()
    proof_channel_id = State()
    welcome_ar = State()
    welcome_en = State()
    force_channel_id = State()
    force_channel_url = State()
    escalation_ids = State()
    manual_sla = State()


async def _get_cfg(session, key: str, default: Any = None) -> Any:
    row = (await session.execute(select(BotConfig).where(BotConfig.key == key))).scalar_one_or_none()
    if row is None or row.value is None:
        return default
    return row.value


async def _set_cfg(session, key: str, value: Any, reviewer: str = "admin") -> None:
    row = (await session.execute(select(BotConfig).where(BotConfig.key == key))).scalar_one_or_none()
    # Store JSON-compatible values
    if isinstance(value, (dict, list, bool, int, float)) or value is None:
        store = value
    else:
        store = str(value)
    if row:
        row.value = store
    else:
        session.add(BotConfig(key=key, value=store, updated_by=reviewer))
    await session.commit()
    await event_bus.publish_config_change("bot_config", key)


def _truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).lower() in ("true", "1", "yes", "on")


def _settings_kb(flags: dict) -> InlineKeyboardMarkup:
    def flag(on: bool) -> str:
        return "🟢" if on else "⚪️"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{flag(flags['sham'])} شام كاش",
            callback_data="adm:cfg:toggle:pay_sham_cash_enabled",
        )],
        [InlineKeyboardButton(
            text=f"{flag(flags['usdt'])} USDT",
            callback_data="adm:cfg:toggle:pay_usdt_enabled",
        )],
        [InlineKeyboardButton(
            text=f"{flag(flags['binance'])} Binance Pay",
            callback_data="adm:cfg:toggle:pay_binance_enabled",
        )],
        [InlineKeyboardButton(text="✏️ رقم شام كاش", callback_data="adm:cfg:sham_account")],
        [InlineKeyboardButton(text="✏️ اسم المستلم", callback_data="adm:cfg:sham_recipient")],
        [InlineKeyboardButton(text="✏️ تعليمات شام كاش", callback_data="adm:cfg:sham_instructions")],
        [InlineKeyboardButton(text="📷 رفع QR شام كاش", callback_data="adm:cfg:sham_qr")],
        [InlineKeyboardButton(text="📢 قناة التوثيق ID", callback_data="adm:cfg:proof_id")],
        [InlineKeyboardButton(
            text=f"{flag(flags['proof_auto'])} نشر تلقائي بقناة التوثيق",
            callback_data="adm:cfg:toggle:proof_channel_auto_post",
        )],
        [InlineKeyboardButton(
            text=f"{flag(flags['force'])} إجبار الاشتراك",
            callback_data="adm:cfg:toggle:force_subscribe_enabled",
        )],
        [InlineKeyboardButton(text="📢 Channel ID", callback_data="adm:cfg:force_id")],
        [InlineKeyboardButton(text="🔗 Channel URL", callback_data="adm:cfg:force_url")],
        [InlineKeyboardButton(text="👋 ترحيب عربي", callback_data="adm:cfg:welcome_ar")],
        [InlineKeyboardButton(text="👋 Welcome EN", callback_data="adm:cfg:welcome_en")],
        [InlineKeyboardButton(
            text=f"{flag(flags['maint'])} وضع الصيانة",
            callback_data="adm:cfg:toggle:maintenance_mode",
        )],
        [InlineKeyboardButton(text="💱 سعر الصرف SYP", callback_data="adm:settings:syp")],
        [InlineKeyboardButton(text="🔔 مسؤولو التصعيد (Telegram IDs)", callback_data="adm:cfg:escalation")],
        [InlineKeyboardButton(text="⏱️ مهلة التسليم اليدوي (دقيقة)", callback_data="adm:cfg:manual_sla")],
        [InlineKeyboardButton(text="🏠 القائمة", callback_data="adm:home")],
    ])


async def _render_settings(target, *, edit: bool = False):
    async with AsyncSessionLocal() as session:
        sham_acc = await _get_cfg(session, "sham_cash_account_number", "—")
        sham_qr = await _get_cfg(session, "sham_cash_qr_url", "") or await _get_cfg(session, "sham_cash_qr_file_id", "")
        proof_id = await _get_cfg(session, "proof_channel_id", "—")
        flags = {
            "sham": _truthy(await _get_cfg(session, "pay_sham_cash_enabled", True)),
            "usdt": _truthy(await _get_cfg(session, "pay_usdt_enabled", True)),
            "binance": _truthy(await _get_cfg(session, "pay_binance_enabled", True)),
            "force": _truthy(await _get_cfg(session, "force_subscribe_enabled", False)),
            "maint": _truthy(await _get_cfg(session, "maintenance_mode", False)),
            "proof_auto": _truthy(await _get_cfg(session, "proof_channel_auto_post", False)),
        }
        force_id = await _get_cfg(session, "main_channel_id", "—")
        force_url = await _get_cfg(session, "main_channel_url", "—")
        channel_health = await _get_cfg(session, "force_sub_channel_health", None)
        escalation = await _get_cfg(session, "escalation_telegram_ids", "")
        manual_sla = await _get_cfg(session, "manual_delivery_sla_minutes", 60)
    health_line = ""
    if isinstance(channel_health, dict) and channel_health.get("ok") is False:
        health_line = (
            f"\n⚠️ <b>صحة قناة الإجبار:</b> خطأ على "
            f"<code>{channel_health.get('channel_id')}</code>\n"
            f"<i>{str(channel_health.get('error') or '')[:120]}</i>\n"
            f"آخر فحص: {channel_health.get('checked_at') or '—'}\n"
        )
    text = (
        "⚙️ <b>إعدادات البوت والقنوات</b>\n\n"
        f"شام كاش: <code>{sham_acc}</code>\n"
        f"QR شام كاش: <code>{(str(sham_qr)[:40] + '…') if sham_qr else '—'}</code>\n"
        f"قناة التوثيق: <code>{proof_id}</code>\n"
        f"قناة إجبارية: <code>{force_id}</code>\n"
        f"رابط القناة: {force_url}\n"
        f"تصعيد التنبيهات: <code>{escalation or '— (ADMIN_TELEGRAM_IDS)'}</code>\n"
        f"مهلة التسليم اليدوي: <code>{manual_sla}</code> دقيقة\n"
        f"{health_line}\n"
        "بدّل المفاتيح أو عدّل القيم أدناه — تُحفظ فوراً في قاعدة البيانات."
    )
    kb = _settings_kb(flags)
    if edit:
        try:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(F.text == "⚙️ إعدادات شام كاش / سعر الصرف")
@router.message(F.text == "⚙️ إعدادات البوت")
@router.callback_query(F.data == "adm:cfg")
async def settings_hub(event: Message | CallbackQuery, state: FSMContext):
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
        await _render_settings(event.message, edit=True)
    else:
        await _render_settings(event, edit=False)


@router.callback_query(F.data.startswith("adm:cfg:toggle:"))
async def toggle_flag(callback: CallbackQuery):
    key = callback.data.split(":")[-1]
    default_off = key in ("maintenance_mode", "force_subscribe_enabled", "proof_channel_auto_post")
    async with AsyncSessionLocal() as session:
        cur = _truthy(await _get_cfg(session, key, False if default_off else True))
        await _set_cfg(session, key, (not cur), reviewer=f"tg:{callback.from_user.id}")
    await callback.answer("تم التحديث")
    await _render_settings(callback.message, edit=True)


@router.callback_query(F.data == "adm:cfg:sham_account")
async def ask_sham_account(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.sham_account)
    await callback.message.answer("أرسل رقم/كود حساب شام كاش الآن:")
    await callback.answer()


@router.message(SettingsFSM.sham_account)
async def save_sham_account(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "sham_cash_account_number", val, reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer(f"✅ تم حفظ رقم شام كاش: <code>{val}</code>", parse_mode="HTML")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:sham_instructions")
async def ask_sham_instructions(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.sham_instructions)
    await callback.message.answer("أرسل تعليمات شام كاش (نص كامل):")
    await callback.answer()


@router.message(SettingsFSM.sham_instructions)
async def save_sham_instructions(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "sham_cash_instructions_ar", val, reviewer=f"tg:{message.from_user.id}")
        await _set_cfg(session, "sham_cash_instructions", val, reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer("✅ تم حفظ التعليمات.")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:sham_recipient")
async def ask_sham_recipient(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.sham_recipient)
    await callback.message.answer("أرسل اسم مستلم شام كاش الظاهر تحت الـ QR:")
    await callback.answer()


@router.message(SettingsFSM.sham_recipient)
async def save_sham_recipient(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "sham_cash_recipient_name", val, reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer(f"✅ اسم المستلم: <code>{val}</code>", parse_mode="HTML")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:sham_qr")
async def ask_sham_qr(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.sham_qr)
    await callback.message.answer("📷 أرسل الآن صورة QR Code الخاصة بشام كاش كـ صورة (Photo):")
    await callback.answer()


@router.message(SettingsFSM.sham_qr, F.photo)
async def save_sham_qr(message: Message, state: FSMContext):
    photo = message.photo[-1]
    file_id = photo.file_id
    url = ""
    try:
        from src.core.services.sham_cash_qr import save_qr_bytes
        file_obj = await message.bot.get_file(file_id)
        stream = await message.bot.download_file(file_obj.file_path)
        data = stream.read()
        ext = ".jpg"
        path_hint = (file_obj.file_path or "").lower()
        if path_hint.endswith(".png"):
            ext = ".png"
        elif path_hint.endswith(".webp"):
            ext = ".webp"
        url = save_qr_bytes(data, ext=ext)
    except Exception as e:
        await message.answer(f"⚠️ حُفظ file_id لكن تعذر حفظ نسخة محلية: {e}")
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "sham_cash_qr_file_id", file_id, reviewer=f"tg:{message.from_user.id}")
        if url:
            await _set_cfg(session, "sham_cash_qr_url", url, reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer(
        f"✅ تم حفظ QR شام كاش.\nfile_id محفوظ"
        + (f"\nURL: <code>{url}</code>" if url else ""),
        parse_mode="HTML",
    )
    await _render_settings(message)


@router.message(SettingsFSM.sham_qr)
async def save_sham_qr_reject(message: Message, state: FSMContext):
    await message.answer("⚠️ أرسل صورة (Photo) لرمز QR فقط.")


@router.callback_query(F.data == "adm:cfg:proof_id")
async def ask_proof_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.proof_channel_id)
    await callback.message.answer("أرسل معرف قناة التوثيق (@channel أو -100…):")
    await callback.answer()


@router.message(SettingsFSM.proof_channel_id)
async def save_proof_id(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "proof_channel_id", val, reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer(f"✅ قناة التوثيق: <code>{val}</code>", parse_mode="HTML")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:welcome_ar")
async def ask_welcome_ar(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.welcome_ar)
    await callback.message.answer("أرسل رسالة الترحيب بالعربية:")
    await callback.answer()


@router.message(SettingsFSM.welcome_ar)
async def save_welcome_ar(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "welcome_message_ar", (message.text or "").strip(), reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer("✅ تم حفظ الترحيب العربي.")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:welcome_en")
async def ask_welcome_en(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.welcome_en)
    await callback.message.answer("Send the English welcome message:")
    await callback.answer()


@router.message(SettingsFSM.welcome_en)
async def save_welcome_en(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "welcome_message_en", (message.text or "").strip(), reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer("✅ English welcome saved.")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:force_id")
async def ask_force_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.force_channel_id)
    await callback.message.answer("أرسل Channel ID (مثل -100…):")
    await callback.answer()


@router.message(SettingsFSM.force_channel_id)
async def save_force_id(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "main_channel_id", (message.text or "").strip(), reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer("✅ تم حفظ Channel ID.")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:force_url")
async def ask_force_url(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.force_channel_url)
    await callback.message.answer("أرسل رابط القناة (https://t.me/…):")
    await callback.answer()


@router.message(SettingsFSM.force_channel_url)
async def save_force_url(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "main_channel_url", (message.text or "").strip(), reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer("✅ تم حفظ رابط القناة.")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:escalation")
async def ask_escalation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.escalation_ids)
    await callback.message.answer(
        "أرسل معرفات تيليجرام لمسؤولي/مساعدي التصعيد مفصولة بفاصلة:\n"
        "<code>111001,222003</code>\n"
        "(فارغ = استخدام ADMIN_TELEGRAM_IDS فقط)",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SettingsFSM.escalation_ids)
async def save_escalation(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "escalation_telegram_ids", val, reviewer=f"tg:{message.from_user.id}")
    from src.core.services.manual_delivery_status import cache_escalation_ids
    await cache_escalation_ids(val)
    await state.clear()
    await message.answer(f"✅ تم حفظ مسؤولي التصعيد: <code>{val or '—'}</code>", parse_mode="HTML")
    await _render_settings(message)


@router.callback_query(F.data == "adm:cfg:manual_sla")
async def ask_manual_sla(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFSM.manual_sla)
    await callback.message.answer("أرسل مهلة التسليم اليدوي بالدقائق (مثال: 60):")
    await callback.answer()


@router.message(SettingsFSM.manual_sla)
async def save_manual_sla(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    try:
        minutes = int(float(raw))
        if minutes < 5 or minutes > 24 * 60:
            raise ValueError("out of range")
    except ValueError:
        await message.answer("❌ أدخل رقماً صالحاً بين 5 و 1440.")
        return
    async with AsyncSessionLocal() as session:
        await _set_cfg(session, "manual_delivery_sla_minutes", minutes, reviewer=f"tg:{message.from_user.id}")
    await state.clear()
    await message.answer(f"✅ مهلة التسليم اليدوي: <code>{minutes}</code> دقيقة", parse_mode="HTML")
    await _render_settings(message)
