"""Admin authentication — JWT in HttpOnly cookie and/or Authorization Bearer."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.entities import AdminUser
from src.core.security import (
    verify_password,
    create_access_token,
    decode_access_token,
    get_password_hash,
)
from src.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

COOKIE_NAME = getattr(settings, "AUTH_COOKIE_NAME", None) or "access_token"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    display_name: Optional[str] = None


class LoginJSON(BaseModel):
    username: str
    password: str


class AdminUserDTO(BaseModel):
    id: str
    username: str
    display_name: Optional[str]
    role: str


def _extract_token(request: Request, bearer: Optional[str]) -> Optional[str]:
    if bearer:
        return bearer
    return request.cookies.get(COOKIE_NAME)


def _set_auth_cookie(response: Response, token: str) -> None:
    max_age = int(settings.ACCESS_TOKEN_EXPIRE_MINUTES or 1440) * 60
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=bool(settings.is_production),
        max_age=max_age,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


async def _ensure_env_admin(session: AsyncSession) -> Optional[AdminUser]:
    """Bootstrap/sync AdminUser from ADMIN_USERNAME / ADMIN_PASSWORD when set."""
    username = (settings.ADMIN_USERNAME or "").strip()
    password = (settings.ADMIN_PASSWORD or "").strip()
    if not username or not password:
        return None

    stmt = select(AdminUser).where(AdminUser.username == username)
    admin = (await session.execute(stmt)).scalar_one_or_none()
    if admin:
        if not verify_password(password, admin.password_hash):
            admin.password_hash = get_password_hash(password)
            admin.is_active = True
            await session.commit()
            await session.refresh(admin)
        return admin

    count = (await session.execute(select(func.count()).select_from(AdminUser))).scalar() or 0
    if count == 0 or username == "admin":
        admin = AdminUser(
            username=username,
            password_hash=get_password_hash(password),
            display_name="System Administrator",
            role="admin",
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        return admin
    return None


async def get_current_admin(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> AdminUser:
    raw = _extract_token(request, token)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(raw)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload.get("sub")
    stmt = select(AdminUser).where(AdminUser.username == username, AdminUser.is_active == True)  # noqa: E712
    admin = (await session.execute(stmt)).scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return admin


async def _authenticate(session: AsyncSession, username: str, password: str) -> AdminUser:
    await _ensure_env_admin(session)

    stmt = select(AdminUser).where(AdminUser.username == username)
    admin = (await session.execute(stmt)).scalar_one_or_none()

    # Legacy bootstrap: first login as admin when DB empty
    if not admin and username == "admin":
        count = (await session.execute(select(func.count()).select_from(AdminUser))).scalar() or 0
        if count == 0:
            admin = AdminUser(
                username="admin",
                password_hash=get_password_hash(password),
                display_name="System Administrator",
                role="admin",
                is_active=True,
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)

    if not admin or not verify_password(password, admin.password_hash):
        # Env fallback match
        env_user = (settings.ADMIN_USERNAME or "").strip()
        env_pass = (settings.ADMIN_PASSWORD or "").strip()
        if env_user and env_pass and username == env_user and password == env_pass:
            admin = await _ensure_env_admin(session)
        if not admin or not verify_password(password, admin.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )
    return admin


def _issue_token(admin: AdminUser) -> str:
    return create_access_token(
        data={"sub": admin.username, "role": admin.role},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db),
):
    admin = await _authenticate(session, form_data.username, form_data.password)
    token = _issue_token(admin)
    _set_auth_cookie(response, token)
    return TokenResponse(
        access_token=token,
        username=admin.username,
        display_name=admin.display_name,
    )


@router.post("/login/json", response_model=TokenResponse)
async def login_json(
    body: LoginJSON,
    response: Response,
    session: AsyncSession = Depends(get_db),
):
    admin = await _authenticate(session, body.username, body.password)
    token = _issue_token(admin)
    _set_auth_cookie(response, token)
    return TokenResponse(
        access_token=token,
        username=admin.username,
        display_name=admin.display_name,
    )


@router.post("/logout")
async def logout(response: Response):
    _clear_auth_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=AdminUserDTO)
async def get_me(admin: AdminUser = Depends(get_current_admin)):
    return AdminUserDTO(
        id=str(admin.id),
        username=admin.username,
        display_name=admin.display_name,
        role=admin.role,
    )
