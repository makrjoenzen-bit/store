from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.entities import AdminUser
from src.core.security import verify_password, create_access_token, decode_access_token, get_password_hash
from src.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    display_name: Optional[str]


class AdminUserDTO(BaseModel):
    id: str
    username: str
    display_name: Optional[str]
    role: str


async def get_current_admin(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db)
) -> AdminUser:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload.get("sub")
    stmt = select(AdminUser).where(AdminUser.username == username, AdminUser.is_active == True)
    res = await session.execute(stmt)
    admin = res.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return admin


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db)
):
    stmt = select(AdminUser).where(AdminUser.username == form_data.username)
    res = await session.execute(stmt)
    admin = res.scalar_one_or_none()

    # Bootstrap default admin if database has no admin users yet
    if not admin and form_data.username == "admin":
        count_stmt = select(AdminUser)
        count_res = await session.execute(count_stmt)
        if not count_res.scalars().first():
            # Create initial admin
            admin = AdminUser(
                username="admin",
                password_hash=get_password_hash(form_data.password),
                display_name="System Administrator",
                role="admin"
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)

    if not admin or not verify_password(form_data.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    token = create_access_token(
        data={"sub": admin.username, "role": admin.role},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    return TokenResponse(
        access_token=token,
        username=admin.username,
        display_name=admin.display_name,
    )


@router.get("/me", response_model=AdminUserDTO)
async def get_me(admin: AdminUser = Depends(get_current_admin)):
    return AdminUserDTO(
        id=str(admin.id),
        username=admin.username,
        display_name=admin.display_name,
        role=admin.role,
    )
