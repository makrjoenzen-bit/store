"""
Phase 14.6 — Referral milestone giveaway: anti-fraud, atomic claim, auto-pause.

Run: pytest -v tests/test_phase14_6_referral_campaign.py
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_6.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase146Hot000000000000000000000001"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, ReferralCampaign, CampaignRewardItem, CampaignInviteRecord,
)
from src.core.redis import event_bus
from src.core.security import decrypt_secret
from src.core.services.referral_campaign_service import referral_campaign_service


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        ref = Customer(telegram_id=1001, telegram_username="referrer", first_name="Ali")
        session.add(ref)
        await session.flush()
        c = await referral_campaign_service.create_campaign(
            session,
            title="حملة حسابات Gemini المجانية",
            reward_name="حساب Gemini Pro مجاني",
            required_invites=3,
            max_claims_per_user=1,
            require_channel_sub=True,
            auto_deliver=True,
            is_active=True,
            reviewer="tg:900001",
        )
        await referral_campaign_service.add_stock(
            session,
            c["id"],
            ["acc1:pass1", "acc2:pass2", "acc3:pass3"],
            reviewer="tg:900001",
        )
        await session.commit()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_unsubscribed_invite_not_counted_until_verify():
    async with AsyncSessionLocal() as session:
        referrer = (await session.execute(
            select(Customer).where(Customer.telegram_id == 1001)
        )).scalar_one()
        invitee = Customer(telegram_id=2001, first_name="New1")
        session.add(invitee)
        await session.flush()

        r = await referral_campaign_service.record_pending_invite(
            session, referrer=referrer, invitee=invitee, is_new_customer=True
        )
        assert r["status"] == "pending"
        campaign = await referral_campaign_service.get_active_campaign(session)
        assert await referral_campaign_service.verified_invite_count(
            session, campaign.id, referrer.id
        ) == 0

        # Force-sub verify → counts
        with patch.object(event_bus, "publish", new_callable=AsyncMock):
            vr = await referral_campaign_service.verify_invitee(session, invitee_id=invitee.id)
        assert vr["status"] == "ok"
        assert await referral_campaign_service.verified_invite_count(
            session, campaign.id, referrer.id
        ) == 1


@pytest.mark.asyncio
async def test_reject_existing_self_and_duplicate():
    async with AsyncSessionLocal() as session:
        referrer = (await session.execute(
            select(Customer).where(Customer.telegram_id == 1001)
        )).scalar_one()

        # Self-referral
        r1 = await referral_campaign_service.record_pending_invite(
            session, referrer=referrer, invitee=referrer, is_new_customer=True
        )
        assert r1["reason"] == "self_referral"

        # Existing user
        old = Customer(telegram_id=2002, first_name="Old")
        session.add(old)
        await session.flush()
        r2 = await referral_campaign_service.record_pending_invite(
            session, referrer=referrer, invitee=old, is_new_customer=False
        )
        assert r2["reason"] == "existing_user"

        # Duplicate
        new = Customer(telegram_id=2003, first_name="Dup")
        session.add(new)
        await session.flush()
        await referral_campaign_service.record_pending_invite(
            session, referrer=referrer, invitee=new, is_new_customer=True
        )
        r3 = await referral_campaign_service.record_pending_invite(
            session, referrer=referrer, invitee=new, is_new_customer=True
        )
        assert r3["reason"] == "duplicate_invitee"


@pytest.mark.asyncio
async def test_third_invite_delivers_unique_account():
    async with AsyncSessionLocal() as session:
        referrer = (await session.execute(
            select(Customer).where(Customer.telegram_id == 1001)
        )).scalar_one()
        with patch.object(event_bus, "publish", new_callable=AsyncMock):
            result = None
            for i in range(3):
                inv = Customer(telegram_id=3000 + i, first_name=f"F{i}")
                session.add(inv)
                await session.flush()
                await referral_campaign_service.record_pending_invite(
                    session, referrer=referrer, invitee=inv, is_new_customer=True
                )
                result = await referral_campaign_service.verify_invitee(
                    session, invitee_id=inv.id
                )

            last = result["results"][-1]
            assert last["verified_invites"] >= 3
            delivery = last.get("delivery") or {}
            assert delivery.get("status") == "delivered"
            assert delivery.get("payload") in ("acc1:pass1", "acc2:pass2", "acc3:pass3")

            claimed = (await session.execute(
                select(CampaignRewardItem).where(CampaignRewardItem.is_claimed == True)  # noqa: E712
            )).scalars().all()
            assert len(claimed) == 1
            assert claimed[0].claimed_by_customer_id == referrer.id
            assert decrypt_secret(claimed[0].payload_encrypted) == delivery["payload"]


@pytest.mark.asyncio
async def test_max_claims_per_user_blocks_drain():
    async with AsyncSessionLocal() as session:
        referrer = (await session.execute(
            select(Customer).where(Customer.telegram_id == 1001)
        )).scalar_one()
        campaign = await referral_campaign_service.get_active_campaign(session)

        with patch.object(event_bus, "publish", new_callable=AsyncMock):
            # First claim via direct atomic API
            d1 = await referral_campaign_service.claim_reward_atomic(
                session, campaign_id=campaign.id, customer_id=referrer.id
            )
            await session.commit()
            assert d1["status"] == "delivered"

            d2 = await referral_campaign_service.claim_reward_atomic(
                session, campaign_id=campaign.id, customer_id=referrer.id
            )
            assert d2["status"] == "blocked"
            assert d2["reason"] == "max_claims_per_user"


@pytest.mark.asyncio
async def test_concurrent_claims_unique_payloads():
    async with AsyncSessionLocal() as session:
        campaign = await referral_campaign_service.get_active_campaign(session)
        # Two different winners
        c1 = Customer(telegram_id=4001, first_name="W1")
        c2 = Customer(telegram_id=4002, first_name="W2")
        session.add_all([c1, c2])
        await session.commit()
        id1, id2, cid = c1.id, c2.id, campaign.id

    async def claim_one(cust_id):
        async with AsyncSessionLocal() as s:
            with patch.object(event_bus, "publish", new_callable=AsyncMock):
                # Retry briefly on lock contention
                for _ in range(5):
                    r = await referral_campaign_service.claim_reward_atomic(
                        s, campaign_id=cid, customer_id=cust_id
                    )
                    if r.get("status") != "busy":
                        await s.commit()
                        return r
                    await asyncio.sleep(0.05)
                return r

    r1, r2 = await asyncio.gather(claim_one(id1), claim_one(id2))
    assert r1["status"] == "delivered"
    assert r2["status"] == "delivered"
    assert r1["payload"] != r2["payload"]
    assert r1["item_id"] != r2["item_id"]


@pytest.mark.asyncio
async def test_final_item_autopauses_and_alerts():
    async with AsyncSessionLocal() as session:
        campaign = await referral_campaign_service.get_active_campaign(session)
        # Leave only 1 unclaimed
        items = (await session.execute(
            select(CampaignRewardItem).where(CampaignRewardItem.campaign_id == campaign.id)
        )).scalars().all()
        for item in items[1:]:
            item.is_claimed = True
        await session.commit()

        winner = Customer(telegram_id=5001, first_name="Last")
        session.add(winner)
        await session.flush()

        alerts = []

        async def capture_publish(channel, payload=None, **kwargs):
            alerts.append((channel, payload))

        # publish_alert and publish both used
        with patch.object(event_bus, "publish", new_callable=AsyncMock) as pub:
            result = await referral_campaign_service.claim_reward_atomic(
                session, campaign_id=campaign.id, customer_id=winner.id
            )
            await session.commit()

        assert result["status"] == "delivered"
        assert result.get("paused") is True
        assert result.get("remaining") == 0

        await session.refresh(campaign)
        assert campaign.is_active is False

        # Admin alert published
        channels = [c.args[0] for c in pub.await_args_list]
        assert "alerts:stream" in channels
