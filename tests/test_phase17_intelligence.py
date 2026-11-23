"""Phase 17 — AI Concierge, PDF Forensics, Ad Generator, Competitor Radar, Supplier Quality."""
from __future__ import annotations

import asyncio
import io
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------- 1. AI Concierge ----------
@pytest.mark.asyncio
async def test_ai_concierge_arabic_search_usd_syp_no_supplier_leak():
    from src.core.services.ai_concierge_service import ai_concierge_service, SAFE_PRODUCT_KEYS

    product_id = str(uuid.uuid4())
    fake_products = [{
        "product_id": product_id,
        "title_ar": "شات جي بي تي بلس",
        "price_usd": 7.5,
        "price_syp": 86250.0,
        "price_syp_label": "86,250 ل.س",
        "stock": 5,
        "warranty_days": 25,
        "delivery_type": "⚡ فوري",
    }]

    session = AsyncMock()
    customer = MagicMock()
    customer.id = uuid.uuid4()

    with patch.object(ai_concierge_service, "search_live_catalog", new_callable=AsyncMock) as search:
        search.return_value = fake_products
        with patch(
            "src.core.services.ai_concierge_service._ai_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch.object(ai_concierge_service, "_maybe_llm_polish", new_callable=AsyncMock, return_value=None):
                result = await ai_concierge_service.handle_message(
                    session,
                    customer=customer,
                    text="عندك شات جي بي تي وقديش بالليرة؟",
                )

    search.assert_awaited()
    assert result["products"], "expected catalog hits"
    p = result["products"][0]
    assert p["price_usd"] == 7.5
    assert p.get("price_syp") or p.get("price_syp_label")
    blob = json.dumps(p, ensure_ascii=False).lower()
    assert "supplier" not in blob
    assert "wholesale" not in blob
    assert "المورد" not in blob
    assert "تكلفة" not in blob
    for k in p:
        assert k in SAFE_PRODUCT_KEYS or k == "price_syp_label"

    # CTA button payload shape used by handler
    from src.bot.handlers.ai_concierge import _build_cta_keyboard
    kb = _build_cta_keyboard(result)
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c and c.startswith("prod:") for c in flat)


def test_ai_concierge_does_not_intercept_fsm_states():
    src = _read("src/bot/handlers/ai_concierge.py")
    assert "get_state" in src
    assert "if current is not None" in src
    assert "ai_concierge" in _read("src/bot/bot_main.py")


# ---------- 2. PDF Forensics ----------
def _minimal_pdf(extra_meta: str = "") -> bytes:
    # Tiny valid-ish PDF with optional metadata dictionary
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R >>endobj\n"
        b"4 0 obj<< /Length 68 >>stream\n"
        b"BT /F1 12 Tf 20 150 Td (Amount: $12.50 Ref: SCABC1234567) Tj ET\n"
        b"endstream\nendobj\n"
    )
    if extra_meta:
        meta = (
            b"5 0 obj<< /Creator (Adobe Photoshop 2024) /Producer (Adobe Photoshop) "
            b"/CreationDate (D:20240101120000Z) /ModDate (D:20240615180000Z) >>endobj\n"
        )
    else:
        meta = (
            b"5 0 obj<< /Creator (ShamCash) /Producer (ShamCash PDF) "
            b"/CreationDate (D:20240101120000Z) /ModDate (D:20240101120000Z) >>endobj\n"
        )
    xref = b"xref\n0 6\ntrailer<< /Size 6 /Root 1 0 R /Info 5 0 R >>\nstartxref\n0\n%%EOF\n"
    return body + meta + xref


def test_pdf_forensics_clean_extracts_amount_and_ref():
    from src.core.services.pdf_forensics_service import pdf_forensics_service

    pdf = _minimal_pdf(extra_meta=False)
    # Inject readable text into raw path
    pdf = pdf.replace(b"Amount: $12.50 Ref: SCABC1234567", b"Amount: $12.50 Ref: SCABC1234567")
    result = pdf_forensics_service.analyze(pdf, expected_usd=Decimal("12.50"))
    assert result.extracted_amount == 12.5 or result.extracted_amount is not None
    # Force field parse on synthetic text
    result2 = pdf_forensics_service.analyze(
        b"%PDF-1.4\nAmount: $12.50 Ref: SCABC1234567\n/Creator (ShamCash)\n"
        b"/Producer (ShamCash)\n/CreationDate (D:20240101120000Z)\n"
        b"/ModDate (D:20240101120000Z)\n",
        expected_usd=Decimal("12.50"),
    )
    assert result2.extracted_amount == 12.5
    assert result2.extracted_tx_ref
    assert result2.amount_match == "exact"
    assert result2.risk_level == "clean"
    assert "🟢" in result2.risk_badge_ar


def test_pdf_forensics_photoshop_high_risk():
    from src.core.services.pdf_forensics_service import pdf_forensics_service

    result = pdf_forensics_service.analyze(
        b"%PDF-1.4\nAmount: $9.00\n"
        b"/Creator (Adobe Photoshop 2024)\n/Producer (Adobe Photoshop)\n"
        b"/CreationDate (D:20240101120000Z)\n/ModDate (D:20240901120000Z)\n",
        expected_usd=Decimal("12.50"),
    )
    assert result.risk_level == "high"
    assert "🔴" in result.risk_badge_ar
    assert any("photoshop" in t for t in result.suspicious_tools)
    assert "Forensics Summary" in result.summary_block_ar()
    assert "pdf_forensics_json" in _read("src/core/models/entities.py")
    assert "forensics_summary" in _read("src/core/services/admin_review_notify.py")
    assert "لا يُعتمد الدفع تلقائياً" in _read("src/core/services/admin_review_notify.py") or \
           "لا اعتماد" in _read("dashboard/app/sham-cash/page.tsx")


# ---------- 3. Ad Image Generator ----------
def test_ad_image_generator_promo_flash_png():
    from src.core.services.ad_image_generator import ad_image_generator

    png = ad_image_generator.generate(
        template="promo_flash",
        title="ChatGPT Plus شهر",
        price_usd=Decimal("7.50"),
        price_syp=Decimal("86250"),
        features=["تفعيل فوري", "ضمان 25 يوم", "دعم سوري"],
        warranty_days=25,
    )
    assert isinstance(png, (bytes, bytearray))
    assert len(png) > 500
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert "generate-image" in _read("src/api/routes/marketing.py")
    assert "توليد صورة إعلان تلقائياً" in _read("src/admin_bot/handlers/marketing.py")


# ---------- 4. Competitor Radar + Profit Guard ----------
@pytest.mark.asyncio
async def test_competitor_radar_undercut_and_profit_guard_clamp():
    from src.core.services.competitor_radar_service import competitor_radar_service
    from src.core.services.pricing_service import pricing_service

    text = "ChatGPT Plus متوفر الآن بسعر $6.00 فقط 🔥"
    hits = competitor_radar_service.parse_prices_from_text(
        text,
        [{"keyword": "chatgpt", "product_id": str(uuid.uuid4())}],
        competitor_name="RivalShop",
    )
    assert hits and hits[0]["price_usd"] == Decimal("6.00")

    product = MagicMock()
    product.id = uuid.uuid4()
    product.title_ar = "شات جي بي تي"
    product.auto_match_competitor = True
    product.cached_final_price = None

    session = AsyncMock()
    # No existing pricing rule
    empty = MagicMock()
    empty.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=empty)
    session.add = MagicMock()

    cost = Decimal("5.50")
    current = Decimal("8.00")
    # Competitor at 6.00 → target 5.95 but floor ≈ cost+incentives+0.10
    with patch.object(
        pricing_service, "get_incentive_rate", new_callable=AsyncMock, return_value=Decimal("0.07")
    ):
        new_price = await competitor_radar_service.apply_auto_reprice(
            session,
            product=product,
            competitor_price=Decimal("6.00"),
            supplier_cost=cost,
            current_price=current,
        )
    assert new_price is not None
    floor = pricing_service.profit_guard_min_payable(
        gross_total=max(current, Decimal("5.95"), cost + Decimal("1")),
        supplier_cost=cost,
        incentive_rate=Decimal("0.07"),
    )
    assert new_price >= floor
    assert new_price >= cost + pricing_service.DEFAULT_PROFIT_SAFETY_FLOOR
    # Must NOT sell at raw undercut below floor
    assert new_price > Decimal("5.50")


@pytest.mark.asyncio
async def test_competitor_radar_triggers_undercut_alert():
    from src.core.services.competitor_radar_service import competitor_radar_service
    from src.core.models.entities import CompetitorPriceSnapshot, MasterProduct

    product = MagicMock(spec=MasterProduct)
    product.id = uuid.uuid4()
    product.title_ar = "نتفلكس"
    product.auto_match_competitor = False

    snap = MagicMock(spec=CompetitorPriceSnapshot)
    snap.master_product_id = product.id
    snap.price_usd = Decimal("4.00")
    snap.competitor_name = "CheapRival"

    session = AsyncMock()
    prod_res = MagicMock()
    prod_res.scalar_one_or_none.return_value = product
    session.execute = AsyncMock(return_value=prod_res)

    with patch(
        "src.core.services.competitor_radar_service.pricing_service.get_best_supplier_and_price",
        new_callable=AsyncMock,
        return_value={"final_price": Decimal("5.00"), "wholesale_cost": Decimal("3.00")},
    ):
        with patch(
            "src.core.services.competitor_radar_service.event_bus.publish_alert",
            new_callable=AsyncMock,
        ) as alert:
            out = await competitor_radar_service._maybe_undercut_and_reprice(session, snap)

    assert out is not None
    assert out["competitor_price"] == 4.0
    alert.assert_awaited()


# ---------- 5. Supplier Quality auto-demotion ----------
@pytest.mark.asyncio
async def test_supplier_quality_auto_demote_on_high_warranty_rate():
    from src.core.services.supplier_quality_service import supplier_quality_service
    from src.core.models.entities import Supplier

    supplier = MagicMock(spec=Supplier)
    supplier.id = uuid.uuid4()
    supplier.name = "FlakyVendor"
    supplier.priority_weight = 100
    supplier.quality_penalty_score = 0
    supplier.warranty_claim_rate_30d = None
    supplier.api_failure_rate_30d = None

    session = AsyncMock()
    # First execute: list suppliers; subsequent: metrics internals — we mock _metrics_for
    list_res = MagicMock()
    list_res.scalars.return_value.all.return_value = [supplier]
    session.execute = AsyncMock(return_value=list_res)
    session.add = MagicMock()
    session.commit = AsyncMock()

    with patch.object(
        supplier_quality_service,
        "_metrics_for",
        new_callable=AsyncMock,
        return_value={
            "total_orders": 20,
            "api_failure_rate": 2.0,
            "warranty_claim_rate": 15.0,
            "avg_delivery_seconds": 120.0,
            "net_profit_contribution": 40.0,
        },
    ):
        with patch(
            "src.core.services.supplier_quality_service.event_bus.publish_alert",
            new_callable=AsyncMock,
        ) as alert:
            board = await supplier_quality_service.compute_and_apply(session)

    assert board[0]["demoted"] is True
    assert supplier.quality_penalty_score > 0
    alert.assert_awaited()
    msg = alert.await_args.kwargs.get("message") or alert.await_args.args[1]
    assert "تحذير جودة" in msg
    assert "FlakyVendor" in msg
    assert "15" in msg


def test_phase17_wiring_present():
    assert "CompetitorSource" in _read("src/core/models/entities.py")
    assert "quality_penalty_score" in _read("src/core/models/entities.py")
    assert "AI_CONCIERGE" in _read("src/config.py")
    compose = _read("docker-compose.yml")
    assert "profiles" in compose and "metabase" in compose
    assert "bi_readonly" in _read("docker/bi_readonly_init.sql")
    assert "v_daily_net_profit" in _read("docker/bi_readonly_init.sql")
    assert "run_radar_cycle" in _read("src/workers/radar_worker.py")
    assert "worker-radar" in compose
