from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.entities import BotConfig, AdminUser
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus

router = APIRouter(prefix="/bot-config", tags=["Bot Configuration"])


class ConfigUpdateDTO(BaseModel):
    configs: Dict[str, Any]


@router.get("")
async def get_all_bot_configs(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(BotConfig)
    res = await session.execute(stmt)
    rows = res.scalars().all()
    return {row.key: row.value for row in rows}


@router.put("")
async def update_bot_configs(
    dto: ConfigUpdateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    for key, value in dto.configs.items():
        stmt = select(BotConfig).where(BotConfig.key == key)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row:
            row.value = value
            row.updated_by = admin.username
        else:
            new_row = BotConfig(key=key, value=value, updated_by=admin.username)
            session.add(new_row)

    await session.commit()
    # Invalidate cache and broadcast to Bot & Dashboard
    await event_bus.publish_config_change("bot_config")
    return {"status": "ok", "message": "Bot configuration updated in real time"}
