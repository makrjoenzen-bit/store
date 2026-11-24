"""Resolve Sham Cash QR + account config for Customer Bot / Mini App."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple

from sqlalchemy import select

from src.core.models.entities import BotConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLIC_UPLOADS = _REPO_ROOT / "dashboard" / "public" / "uploads" / "sham_cash"
_PUBLIC_UPLOADS.mkdir(parents=True, exist_ok=True)


async def _cfg(session, key: str, default: Any = None) -> Any:
    row = (
        await session.execute(select(BotConfig.value).where(BotConfig.key == key))
    ).scalar_one_or_none()
    if row is None:
        return default
    return row


async def get_sham_cash_payment_config(session) -> dict:
    """Live BotConfig bundle for Sham Cash payment UI."""
    account = await _cfg(session, "sham_cash_account_number", "") or ""
    qr_file_id = await _cfg(session, "sham_cash_qr_file_id", "") or ""
    qr_url = await _cfg(session, "sham_cash_qr_url", "") or ""
    recipient = await _cfg(session, "sham_cash_recipient_name", "") or ""
    instructions = await _cfg(session, "sham_cash_instructions", None)
    if instructions is None:
        instructions = await _cfg(
            session,
            "sham_cash_instructions_ar",
            "قم بالتحويل عبر تطبيق شام كاش ثم اضغط «تم الدفع» وأرسل إيصال PDF أو صورة.",
        )
    return {
        "account_number": str(account).strip(),
        "qr_file_id": str(qr_file_id).strip() or None,
        "qr_url": str(qr_url).strip() or None,
        "recipient_name": str(recipient).strip() or None,
        "instructions": str(instructions or "").strip(),
    }


def local_path_from_qr_url(qr_url: Optional[str]) -> Optional[Path]:
    if not qr_url:
        return None
    url = str(qr_url).strip()
    if url.startswith("/uploads/sham_cash/"):
        name = url.rsplit("/", 1)[-1]
        path = _PUBLIC_UPLOADS / name
        return path if path.is_file() else None
    if url.startswith("uploads/sham_cash/"):
        path = _REPO_ROOT / "dashboard" / "public" / url
        return path if path.is_file() else None
    return None


def save_qr_bytes(data: bytes, *, ext: str = ".jpg") -> str:
    """Persist QR image under dashboard/public/uploads/sham_cash/ → return public path."""
    _PUBLIC_UPLOADS.mkdir(parents=True, exist_ok=True)
    name = f"qr{ext if ext.startswith('.') else '.' + ext}"
    path = _PUBLIC_UPLOADS / name
    path.write_bytes(data)
    return f"/uploads/sham_cash/{name}"


async def build_sham_cash_caption(
    session,
    *,
    amount_usd,
    order_number: str = "",
    lang: str = "ar",
) -> str:
    from src.core.services.syp_rate_service import format_sham_cash_amount_line

    cfg = await get_sham_cash_payment_config(session)
    syp_line = await format_sham_cash_amount_line(session, amount_usd)
    is_en = str(lang or "ar").lower().startswith("en")
    account = cfg["account_number"] or "—"
    recipient = cfg.get("recipient_name")
    instructions = cfg.get("instructions") or ""

    if is_en:
        lines = [
            "🏦 <b>Pay via Sham Cash</b>",
            "",
            f"💵 Amount: <code>${float(amount_usd):.2f}</code> USD",
        ]
        if syp_line:
            lines.append(f"💱 SYP: <code>{syp_line}</code>")
        if order_number:
            lines.append(f"🔖 Order: <code>{order_number}</code>")
        lines.append(f"📱 Account: <code>{account}</code>")
        if recipient:
            lines.append(f"👤 Recipient: <b>{recipient}</b>")
        if instructions:
            lines.extend(["", f"📌 {instructions}"])
        lines.extend(["", "After transferring, tap <b>I Have Paid</b> and upload the PDF/receipt."])
        return "\n".join(lines)

    lines = [
        "🏦 <b>الدفع عبر شام كاش</b>",
        "",
        f"💵 المبلغ المطلوب: <code>${float(amount_usd):.2f}</code> USD",
    ]
    if syp_line:
        lines.append(f"💱 بالليرة: <code>{syp_line}</code>")
    if order_number:
        lines.append(f"🔖 الطلب: <code>{order_number}</code>")
    lines.append(f"📱 رقم/كود الحساب: <code>{account}</code>")
    if recipient:
        lines.append(f"👤 المستلم: <b>{recipient}</b>")
    if instructions:
        lines.extend(["", f"📌 {instructions}"])
    lines.extend(["", "بعد التحويل اضغط <b>✅ تم الدفع</b> ثم أرسل إيصال PDF أو صورة."])
    return "\n".join(lines)


async def resolve_qr_photo_input(session) -> Tuple[Optional[Any], Optional[str]]:
    """
    Returns (photo_input, source) for aiogram send_photo.
    photo_input may be FSInputFile, URL string, or Telegram file_id.
    """
    cfg = await get_sham_cash_payment_config(session)
    local = local_path_from_qr_url(cfg.get("qr_url"))
    if local is not None:
        from aiogram.types import FSInputFile
        return FSInputFile(str(local)), "file"
    url = cfg.get("qr_url")
    if url and str(url).startswith("http"):
        from aiogram.types import URLInputFile
        return URLInputFile(str(url)), "url"
    if cfg.get("qr_file_id"):
        return cfg["qr_file_id"], "file_id"
    return None, None
