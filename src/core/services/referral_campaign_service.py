"""
Referral Milestone Giveaway Engine — fraud-resistant invite counting + atomic reward delivery.
Shared by Customer Bot, Admin Bot, and Dashboard API.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import (
    ReferralCampaign, CampaignRewardItem, CampaignInviteRecord, Customer, AuditLog,
)
from src.core.redis import event_bus
from src.core.security import encrypt_secret, decrypt_secret
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now():
    return datetime.now(timezone.utc)


class ReferralCampaignService:
    # ----- serialization -----
    def _stock_counts(self, available: int, claimed: int) -> Dict[str, int]:
        return {"available": available, "claimed": claimed, "total": available + claimed}

    async def _counts_for(self, session: AsyncSession, campaign_id) -> Dict[str, int]:
        avail = (await session.execute(
            select(func.count(CampaignRewardItem.id)).where(
                and_(
                    CampaignRewardItem.campaign_id == campaign_id,
                    CampaignRewardItem.is_claimed == False,  # noqa: E712
                )
            )
        )).scalar_one()
        claimed = (await session.execute(
            select(func.count(CampaignRewardItem.id)).where(
                and_(
                    CampaignRewardItem.campaign_id == campaign_id,
                    CampaignRewardItem.is_claimed == True,  # noqa: E712
                )
            )
        )).scalar_one()
        return self._stock_counts(int(avail or 0), int(claimed or 0))

    def serialize_campaign(self, c: ReferralCampaign, stock: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        return {
            "id": str(c.id),
            "title": c.title,
            "reward_name": c.reward_name,
            "required_invites": int(c.required_invites),
            "max_claims_per_user": int(c.max_claims_per_user),
            "require_channel_sub": bool(c.require_channel_sub),
            "auto_deliver": bool(c.auto_deliver),
            "is_active": bool(c.is_active),
            "stock": stock or {"available": 0, "claimed": 0, "total": 0},
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }

    # ----- CRUD -----
    async def list_campaigns(self, session: AsyncSession) -> List[Dict[str, Any]]:
        rows = (await session.execute(
            select(ReferralCampaign).order_by(ReferralCampaign.created_at.desc())
        )).scalars().all()
        out = []
        for c in rows:
            stock = await self._counts_for(session, c.id)
            out.append(self.serialize_campaign(c, stock))
        return out

    async def get_active_campaign(self, session: AsyncSession) -> Optional[ReferralCampaign]:
        return (await session.execute(
            select(ReferralCampaign)
            .where(ReferralCampaign.is_active == True)  # noqa: E712
            .order_by(ReferralCampaign.created_at.desc())
        )).scalars().first()

    async def get_campaign(self, session: AsyncSession, campaign_id: Any) -> Optional[ReferralCampaign]:
        return (await session.execute(
            select(ReferralCampaign).where(ReferralCampaign.id == _uid(campaign_id))
        )).scalar_one_or_none()

    async def create_campaign(
        self,
        session: AsyncSession,
        *,
        title: str,
        reward_name: str,
        required_invites: int = 3,
        max_claims_per_user: int = 1,
        require_channel_sub: bool = True,
        auto_deliver: bool = True,
        is_active: bool = True,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if required_invites < 1:
            raise ValueError("required_invites must be >= 1")
        if max_claims_per_user < 1:
            raise ValueError("max_claims_per_user must be >= 1")
        c = ReferralCampaign(
            title=title.strip(),
            reward_name=reward_name.strip(),
            required_invites=int(required_invites),
            max_claims_per_user=int(max_claims_per_user),
            require_channel_sub=bool(require_channel_sub),
            auto_deliver=bool(auto_deliver),
            is_active=bool(is_active),
        )
        session.add(c)
        await session.flush()
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="referral_campaign_create",
            entity_type="referral_campaign",
            entity_id=c.id,
            new_values=merge_audit_new_values(reviewer, {
                "title": c.title,
                "required_invites": c.required_invites,
            }),
        ))
        await session.commit()
        stock = await self._counts_for(session, c.id)
        return self.serialize_campaign(c, stock)

    async def update_campaign(
        self,
        session: AsyncSession,
        campaign_id: Any,
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
        **fields,
    ) -> Dict[str, Any]:
        c = await self.get_campaign(session, campaign_id)
        if not c:
            raise ValueError("Campaign not found")
        allowed = {
            "title", "reward_name", "required_invites", "max_claims_per_user",
            "require_channel_sub", "auto_deliver", "is_active",
        }
        old = self.serialize_campaign(c)
        for k, v in fields.items():
            if k in allowed and v is not None:
                if k in ("required_invites", "max_claims_per_user") and int(v) < 1:
                    raise ValueError(f"{k} must be >= 1")
                setattr(c, k, v)
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="referral_campaign_update",
            entity_type="referral_campaign",
            entity_id=c.id,
            old_values=old,
            new_values=merge_audit_new_values(reviewer, self.serialize_campaign(c)),
        ))
        await session.commit()
        stock = await self._counts_for(session, c.id)
        return self.serialize_campaign(c, stock)

    async def add_stock(
        self,
        session: AsyncSession,
        campaign_id: Any,
        payloads: List[str],
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        c = await self.get_campaign(session, campaign_id)
        if not c:
            raise ValueError("Campaign not found")
        lines = [p.strip() for p in payloads if p and str(p).strip()]
        if not lines:
            raise ValueError("No reward payloads provided")
        for line in lines:
            session.add(CampaignRewardItem(
                campaign_id=c.id,
                payload_encrypted=encrypt_secret(line),
                is_claimed=False,
            ))
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="referral_campaign_stock_upload",
            entity_type="referral_campaign",
            entity_id=c.id,
            new_values=merge_audit_new_values(reviewer, {"added": len(lines)}),
        ))
        await session.commit()
        stock = await self._counts_for(session, c.id)
        # Re-activate if stock restored and was paused for empty pool
        return {"status": "ok", "added": len(lines), "stock": stock, "campaign": self.serialize_campaign(c, stock)}

    async def list_claims(self, session: AsyncSession, campaign_id: Any) -> List[Dict[str, Any]]:
        rows = (await session.execute(
            select(CampaignRewardItem, Customer)
            .outerjoin(Customer, Customer.id == CampaignRewardItem.claimed_by_customer_id)
            .where(
                and_(
                    CampaignRewardItem.campaign_id == _uid(campaign_id),
                    CampaignRewardItem.is_claimed == True,  # noqa: E712
                )
            )
            .order_by(CampaignRewardItem.claimed_at.desc())
        )).all()
        out = []
        for item, cust in rows:
            out.append({
                "item_id": str(item.id),
                "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
                "customer_id": str(cust.id) if cust else None,
                "telegram_id": cust.telegram_id if cust else None,
                "first_name": cust.first_name if cust else None,
                "username": cust.telegram_username if cust else None,
            })
        return out

    # ----- progress / anti-fraud -----
    async def verified_invite_count(
        self, session: AsyncSession, campaign_id: Any, referrer_id: Any
    ) -> int:
        return int((await session.execute(
            select(func.count(CampaignInviteRecord.id)).where(
                and_(
                    CampaignInviteRecord.campaign_id == _uid(campaign_id),
                    CampaignInviteRecord.referrer_id == _uid(referrer_id),
                    CampaignInviteRecord.is_verified == True,  # noqa: E712
                )
            )
        )).scalar_one() or 0)

    async def claims_count(
        self, session: AsyncSession, campaign_id: Any, customer_id: Any
    ) -> int:
        return int((await session.execute(
            select(func.count(CampaignRewardItem.id)).where(
                and_(
                    CampaignRewardItem.campaign_id == _uid(campaign_id),
                    CampaignRewardItem.claimed_by_customer_id == _uid(customer_id),
                    CampaignRewardItem.is_claimed == True,  # noqa: E712
                )
            )
        )).scalar_one() or 0)

    async def get_user_progress(
        self, session: AsyncSession, customer: Customer
    ) -> Optional[Dict[str, Any]]:
        c = await self.get_active_campaign(session)
        if not c:
            return None
        stock = await self._counts_for(session, c.id)
        verified = await self.verified_invite_count(session, c.id, customer.id)
        claims = await self.claims_count(session, c.id, customer.id)
        return {
            "campaign": self.serialize_campaign(c, stock),
            "verified_invites": verified,
            "required_invites": c.required_invites,
            "claims_used": claims,
            "max_claims_per_user": c.max_claims_per_user,
            "referral_link_suffix": f"ref_{customer.telegram_id}",
        }

    async def record_pending_invite(
        self,
        session: AsyncSession,
        *,
        referrer: Customer,
        invitee: Customer,
        is_new_customer: bool,
    ) -> Dict[str, Any]:
        """
        Create unverified invite for active campaign.
        Rejects: self-referral, existing users, duplicates.
        """
        if referrer.id == invitee.id or referrer.telegram_id == invitee.telegram_id:
            return {"status": "rejected", "reason": "self_referral"}
        if not is_new_customer:
            return {"status": "rejected", "reason": "existing_user"}

        campaign = await self.get_active_campaign(session)
        if not campaign:
            return {"status": "ignored", "reason": "no_active_campaign"}

        # Duplicate invitee for this campaign
        existing = (await session.execute(
            select(CampaignInviteRecord).where(
                and_(
                    CampaignInviteRecord.campaign_id == campaign.id,
                    CampaignInviteRecord.invitee_id == invitee.id,
                )
            )
        )).scalar_one_or_none()
        if existing:
            return {"status": "rejected", "reason": "duplicate_invitee"}

        rec = CampaignInviteRecord(
            campaign_id=campaign.id,
            referrer_id=referrer.id,
            invitee_id=invitee.id,
            is_verified=False,
        )
        session.add(rec)
        await session.flush()

        if not campaign.require_channel_sub:
            return await self.verify_invitee(session, invitee_id=invitee.id)

        await session.commit()
        return {
            "status": "pending",
            "campaign_id": str(campaign.id),
            "invite_id": str(rec.id),
            "requires_channel_sub": True,
        }

    async def verify_invitee(
        self,
        session: AsyncSession,
        *,
        invitee_id: Any,
    ) -> Dict[str, Any]:
        """
        Mark pending invite(s) for this invitee as verified and maybe deliver reward.
        Called after Force-Sub verification succeeds.
        """
        invitee_uuid = _uid(invitee_id)
        pending = (await session.execute(
            select(CampaignInviteRecord)
            .where(
                and_(
                    CampaignInviteRecord.invitee_id == invitee_uuid,
                    CampaignInviteRecord.is_verified == False,  # noqa: E712
                )
            )
            .with_for_update()
        )).scalars().all()
        if not pending:
            return {"status": "noop", "reason": "no_pending_invites"}

        results = []
        for rec in pending:
            campaign = await self.get_campaign(session, rec.campaign_id)
            if not campaign or not campaign.is_active:
                continue
            rec.is_verified = True
            await session.flush()
            verified = await self.verified_invite_count(session, campaign.id, rec.referrer_id)
            referrer = (await session.execute(
                select(Customer).where(Customer.id == rec.referrer_id)
            )).scalar_one_or_none()

            progress_payload = {
                "status": "verified",
                "campaign_id": str(campaign.id),
                "referrer_id": str(rec.referrer_id),
                "referrer_telegram_id": referrer.telegram_id if referrer else None,
                "verified_invites": verified,
                "required_invites": campaign.required_invites,
                "reward_name": campaign.reward_name,
            }

            if verified < campaign.required_invites:
                # Progress ping to referrer
                if referrer and referrer.telegram_id:
                    left = campaign.required_invites - verified
                    await event_bus.publish("bot:send_message", {
                        "telegram_id": referrer.telegram_id,
                        "text": (
                            f"🎉 انضم صديق جديد عبر رابطك!\n"
                            f"تقدمك الآن: **{verified} / {campaign.required_invites}**\n"
                            f"{'بقي صديق واحد فقط للحصول على الجائزة!' if left == 1 else f'بقي {left} أصدقاء للحصول على الجائزة!'}"
                        ),
                    })
                results.append(progress_payload)
                continue

            # Milestone reached → try claim / deliver
            delivery = await self._try_deliver_reward(
                session, campaign=campaign, referrer=referrer
            )
            progress_payload["delivery"] = delivery
            results.append(progress_payload)

        await session.commit()
        return {"status": "ok", "results": results}

    async def _try_deliver_reward(
        self,
        session: AsyncSession,
        *,
        campaign: ReferralCampaign,
        referrer: Optional[Customer],
    ) -> Dict[str, Any]:
        if not referrer:
            return {"status": "failed", "reason": "no_referrer"}

        claims = await self.claims_count(session, campaign.id, referrer.id)
        if claims >= campaign.max_claims_per_user:
            return {"status": "blocked", "reason": "max_claims_per_user"}

        if not campaign.auto_deliver:
            await event_bus.publish("alerts:stream", {
                "title": "موافقة جائزة إحالة مطلوبة",
                "message": (
                    f"المستخدم {referrer.telegram_id} وصل لـ {campaign.required_invites} دعوات "
                    f"في «{campaign.title}». التسليم اليدوي مفعّل."
                ),
                "level": "info",
                "meta": {
                    "alert_type": "referral_manual_deliver",
                    "campaign_id": str(campaign.id),
                    "customer_id": str(referrer.id),
                    "telegram_id": referrer.telegram_id,
                },
            })
            return {"status": "pending_admin", "reason": "auto_deliver_off"}

        claimed = await self.claim_reward_atomic(
            session, campaign_id=campaign.id, customer_id=referrer.id
        )
        return claimed

    async def claim_reward_atomic(
        self,
        session: AsyncSession,
        *,
        campaign_id: Any,
        customer_id: Any,
    ) -> Dict[str, Any]:
        """
        Atomically pop one unclaimed reward under Redis + row lock.
        Concurrent callers never receive the same payload.
        """
        cid = _uid(campaign_id)
        cust_id = _uid(customer_id)
        campaign = await self.get_campaign(session, cid)
        if not campaign:
            return {"status": "failed", "reason": "campaign_not_found"}
        if not campaign.is_active:
            return {"status": "failed", "reason": "campaign_inactive"}

        claims = await self.claims_count(session, cid, cust_id)
        if claims >= campaign.max_claims_per_user:
            return {"status": "blocked", "reason": "max_claims_per_user"}

        lock_key = f"lock:referral_claim:{cid}"
        token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=15)
        if not token:
            # Brief contention — retry once via row lock path after short wait is caller's job
            return {"status": "busy", "reason": "lock_contention"}

        try:
            item = (await session.execute(
                select(CampaignRewardItem)
                .where(
                    and_(
                        CampaignRewardItem.campaign_id == cid,
                        CampaignRewardItem.is_claimed == False,  # noqa: E712
                    )
                )
                .order_by(CampaignRewardItem.created_at.asc())
                .with_for_update()
                .limit(1)
            )).scalar_one_or_none()

            if not item:
                campaign.is_active = False
                await session.flush()
                await event_bus.publish("alerts:stream", {
                    "title": "نفد مخزون حملة الجوائز",
                    "message": f"⚠️ نفد مخزون حملة «{campaign.title}» وتم إيقافها تلقائياً.",
                    "level": "warning",
                    "meta": {"alert_type": "referral_stock_empty", "campaign_id": str(campaign.id)},
                })
                return {"status": "empty", "reason": "no_stock", "paused": True}

            # Re-check claims under lock
            claims = await self.claims_count(session, cid, cust_id)
            if claims >= campaign.max_claims_per_user:
                return {"status": "blocked", "reason": "max_claims_per_user"}

            plaintext = decrypt_secret(item.payload_encrypted)
            item.is_claimed = True
            item.claimed_by_customer_id = cust_id
            item.claimed_at = _utc_now()
            await session.flush()

            stock = await self._counts_for(session, cid)
            customer = (await session.execute(
                select(Customer).where(Customer.id == cust_id)
            )).scalar_one_or_none()

            # Deliver to customer
            if customer and customer.telegram_id:
                await event_bus.publish("bot:send_message", {
                    "telegram_id": customer.telegram_id,
                    "text": (
                        f"🎁 **مبروك! ربحت {campaign.reward_name}**\n\n"
                        f"تم تسليم جائزتك تلقائياً بعد دعوة "
                        f"**{campaign.required_invites}** أصدقاء.\n\n"
                        f"🔑 **بيانات الحساب / المفتاح:**\n"
                        f"```\n{plaintext}\n```\n\n"
                        f"استمتع بجائزتك! 🌟"
                    ),
                })

            # Admin Bot card
            await event_bus.publish("alerts:stream", {
                "title": "تسليم جائزة إحالة",
                "message": (
                    f"🎁 تم تسليم جائزة إحالة تلقائياً للمستخدم "
                    f"`{customer.telegram_id if customer else cust_id}` "
                    f"— المتبقي في المخزون: **{stock['available']}**"
                ),
                "level": "info",
                "meta": {
                    "alert_type": "referral_reward_delivered",
                    "campaign_id": str(campaign.id),
                    "remaining": stock["available"],
                },
            })

            # Optional masked proof card on Proof Channel
            try:
                await event_bus.publish("channel:broadcast_proof", {
                    "type": "referral_gift",
                    "order_number": f"GIFT-{str(item.id)[:8].upper()}",
                    "customer_username": getattr(customer, "telegram_username", None),
                    "customer_first_name": getattr(customer, "first_name", None),
                    "product_title": campaign.reward_name,
                    "quantity": 1,
                    "payment_method": "Referral Gift",
                })
            except Exception:
                pass

            # Auto-pause on empty
            if stock["available"] == 0:
                campaign.is_active = False
                await event_bus.publish("alerts:stream", {
                    "title": "نفد مخزون حملة الجوائز",
                    "message": (
                        f"⚠️ نفد مخزون حملة الجوائز المجانية «{campaign.title}» "
                        f"وتم إيقافها تلقائياً."
                    ),
                    "level": "warning",
                    "meta": {"alert_type": "referral_stock_empty", "campaign_id": str(campaign.id)},
                })

            return {
                "status": "delivered",
                "item_id": str(item.id),
                "payload": plaintext,
                "reward_name": campaign.reward_name,
                "remaining": stock["available"],
                "paused": stock["available"] == 0,
            }
        finally:
            await event_bus.release_lock(lock_key, token=token)

    async def admin_manual_deliver(
        self,
        session: AsyncSession,
        *,
        campaign_id: Any,
        customer_id: Any,
        approve: bool,
        reviewer: str = "admin",
    ) -> Dict[str, Any]:
        if not approve:
            return {"status": "rejected"}
        return await self.claim_reward_atomic(
            session, campaign_id=campaign_id, customer_id=customer_id
        )


referral_campaign_service = ReferralCampaignService()
