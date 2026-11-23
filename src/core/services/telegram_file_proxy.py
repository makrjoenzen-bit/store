"""Download Telegram files by file_id using a bot token (for Admin Bot re-upload / Dashboard proxy)."""
from __future__ import annotations

from typing import Optional, Tuple

import httpx
from loguru import logger

from src.config import settings


async def download_telegram_file(
    file_id: str,
    *,
    bot_token: Optional[str] = None,
) -> Tuple[bytes, Optional[str]]:
    """
    Returns (bytes, file_path_hint).
    Uses Customer Bot token by default (receipts are uploaded there).
    """
    token = bot_token or settings.TELEGRAM_BOT_TOKEN
    if not token or not file_id:
        raise ValueError("Missing bot token or file_id")
    async with httpx.AsyncClient(timeout=60.0) as client:
        meta = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
        )
        data = meta.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or "getFile failed")
        path = data["result"]["file_path"]
        file_res = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        file_res.raise_for_status()
        return file_res.content, path


async def safe_download_telegram_file(file_id: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        return await download_telegram_file(file_id)
    except Exception as e:
        logger.warning(f"Telegram file download failed for {file_id[:20]}…: {e}")
        return None, None
