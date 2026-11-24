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

# Login brute-force guard: 5 failures / IP / 60s
_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 60
_login_fail_memory: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    """
    Prefer Nginx-set X-Real-IP. Never trust the left-most X-Forwarded-For
    (client-spoofable). If only XFF is present, use the right-most hop.
    """
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def _enforce_login_rate_limit(request: Request) -> None:
    """Raise 429 after too many recent failures for this IP (Redis, memory fallback)."""
    import time

    ip = _client_ip(request)
    key = f"auth:login:fail:{ip}"
    try:
        from src.core.redis import event_bus
        redis = event_bus._redis
        if redis is not None:
            count = int(await redis.get(key) or 0)
            if count >= _LOGIN_FAIL_LIMIT:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many login attempts. Try again in a minute.",
                )
            return
    except HTTPException:
        raise
    except Exception:
        pass

    now = time.time()
    bucket = [t for t in _login_fail_memory.get(ip, []) if now - t < _LOGIN_FAIL_WINDOW]
    _login_fail_memory[ip] = bucket
    if len(bucket) >= _LOGIN_FAIL_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a minute.",
        )


async def _record_login_failure(request: Request) -> None:
    import time

    ip = _client_ip(request)
    key = f"auth:login:fail:{ip}"
    try:
        from src.core.redis import event_bus
        redis = event_bus._redis
        if redis is not None:
            count = await redis.incr(key)
            if int(count) == 1:
                await redis.expire(key, _LOGIN_FAIL_WINDOW)
            return
    except Exception:
        pass
    now = time.time()
    bucket = [t for t in _login_fail_memory.get(ip, []) if now - t < _LOGIN_FAIL_WINDOW]
    bucket.append(now)
    _login_fail_memory[ip] = bucket


async def _clear_login_failures(request: Request) -> None:
    ip = _client_ip(request)
    key = f"auth:login:fail:{ip}"
    try:
        from src.core.redis import event_bus
        redis = event_bus._redis
        if redis is not None:
            await redis.delete(key)
    except Exception:
        pass
    _login_fail_memory.pop(ip, None)


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
    jti = payload.get("jti")
    if jti and await _is_jti_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload.get("sub")
    stmt = select(AdminUser).where(AdminUser.username == username, AdminUser.is_active == True)  # noqa: E712
    admin = (await session.execute(stmt)).scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return admin


async def _is_jti_revoked(jti: str) -> bool:
    try:
        from src.core.redis import event_bus
        redis = event_bus._redis
        if redis is not None:
            return bool(await redis.get(f"auth:revoke:{jti}"))
    except Exception:
        pass
    return False


async def _revoke_jti(jti: str, ttl_seconds: int) -> None:
    if not jti:
        return
    try:
        from src.core.redis import event_bus
        redis = event_bus._redis
        if redis is not None:
            await redis.set(f"auth:revoke:{jti}", "1", ex=max(60, int(ttl_seconds)))
    except Exception:
        pass


async def _authenticate(session: AsyncSession, username: str, password: str) -> AdminUser:
    """Authenticate against AdminUser. Initial seed requires ADMIN_USERNAME + ADMIN_PASSWORD in .env."""
    await _ensure_env_admin(session)

    stmt = select(AdminUser).where(AdminUser.username == username)
    admin = (await session.execute(stmt)).scalar_one_or_none()

    if admin and verify_password(password, admin.password_hash):
        return admin

    # Allow env credentials to re-sync the configured admin (never invent from submitted password)
    env_user = (settings.ADMIN_USERNAME or "").strip()
    env_pass = (settings.ADMIN_PASSWORD or "").strip()
    if env_user and env_pass and username == env_user and password == env_pass:
        admin = await _ensure_env_admin(session)
        if admin and verify_password(password, admin.password_hash):
            return admin

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
    )


def _issue_token(admin: AdminUser) -> str:
    return create_access_token(
        data={"sub": admin.username, "role": admin.role},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db),
):
    await _enforce_login_rate_limit(request)
    try:
        admin = await _authenticate(session, form_data.username, form_data.password)
    except HTTPException as e:
        if e.status_code == status.HTTP_401_UNAUTHORIZED:
            await _record_login_failure(request)
        raise
    await _clear_login_failures(request)
    token = _issue_token(admin)
    _set_auth_cookie(response, token)
    return TokenResponse(
        access_token=token,
        username=admin.username,
        display_name=admin.display_name,
    )


@router.post("/login/json", response_model=TokenResponse)
async def login_json(
    request: Request,
    body: LoginJSON,
    response: Response,
    session: AsyncSession = Depends(get_db),
):
    await _enforce_login_rate_limit(request)
    try:
        admin = await _authenticate(session, body.username, body.password)
    except HTTPException as e:
        if e.status_code == status.HTTP_401_UNAUTHORIZED:
            await _record_login_failure(request)
        raise
    await _clear_login_failures(request)
    token = _issue_token(admin)
    _set_auth_cookie(response, token)
    return TokenResponse(
        access_token=token,
        username=admin.username,
        display_name=admin.display_name,
    )


@router.post("/logout")
async def logout(request: Request, response: Response, token: Optional[str] = Depends(oauth2_scheme)):
    raw = _extract_token(request, token)
    if raw:
        payload = decode_access_token(raw) or {}
        jti = payload.get("jti")
        if jti:
            ttl = int(settings.ACCESS_TOKEN_EXPIRE_MINUTES or 1440) * 60
            await _revoke_jti(str(jti), ttl)
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
