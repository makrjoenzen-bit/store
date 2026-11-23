"""Shared AuditLog actor tagging — Dashboard + Admin Bot mutations."""
from __future__ import annotations

from typing import Any, Dict, Optional


def audit_actor_fields(reviewer: str = "admin") -> Dict[str, Any]:
    """
    Tag every financial/destructive AuditLog with the acting admin identity.
    Admin Bot passes reviewer as ``tg:<telegram_id>``; Dashboard uses email/username.
    """
    meta: Dict[str, Any] = {"reviewer": reviewer or "admin"}
    raw = (reviewer or "").strip()
    if raw.startswith("tg:"):
        try:
            meta["admin_telegram_id"] = int(raw[3:].strip())
        except ValueError:
            pass
    return meta


def merge_audit_new_values(reviewer: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(extra or {})
    out.update(audit_actor_fields(reviewer))
    return out
