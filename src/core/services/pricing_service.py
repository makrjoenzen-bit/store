from decimal import Decimal, ROUND_CEILING
from typing import Optional, Tuple, Dict, Any, List
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import MasterProduct, ProductSupplierLink, Supplier, PricingRule, BotConfig
from src.core.models.enums import SupplierStatus, PricingScope, PricingRuleType
from src.config import settings


def link_is_sellable(link: ProductSupplierLink, require_active_supplier: bool = True) -> bool:
    """True when a supplier link can win routing: in stock, priced, qty>0 (or unlimited), supplier ACTIVE."""
    if not link or not link.is_in_stock or link.last_known_cost is None:
        return False
    if link.available_quantity is not None and link.available_quantity <= 0:
        return False
    if require_active_supplier:
        supplier = getattr(link, "supplier", None)
        if supplier is not None and supplier.status != SupplierStatus.ACTIVE:
            return False
    return True


def pick_cheapest_sellable_link(links: List[ProductSupplierLink]) -> Optional[ProductSupplierLink]:
    """In-memory cheapest-available selection (mirrors get_best_supplier_and_price ordering)."""
    candidates = [l for l in links if link_is_sellable(l)]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda l: (
            Decimal(str(l.last_known_cost)),
            -(l.priority_override if l.priority_override is not None else -1),
            -(getattr(l.supplier, "priority_weight", 0) or 0),
        ),
    )


class PricingService:
    """Calculates supplier routing, cascading profit margins, and dynamic final retail prices."""

    # Minimum net USD kept on every order after ALL stacked incentives are paid out.
    DEFAULT_PROFIT_SAFETY_FLOOR = Decimal("0.10")

    async def get_incentive_rate(self, session: AsyncSession) -> Decimal:
        """
        Combined cashback + referral commission rate as a fraction (e.g. 0.07 for 7%).
        Single shared source of truth for the profit guard on every sales channel.
        """
        try:
            cb = (await session.execute(
                select(BotConfig.value).where(BotConfig.key == "cashback_percent")
            )).scalar_one_or_none()
            ref = (await session.execute(
                select(BotConfig.value).where(BotConfig.key == "referral_commission_percent")
            )).scalar_one_or_none()
            cb_pct = Decimal(str(cb)) if cb is not None else Decimal("2.0")
            ref_pct = Decimal(str(ref)) if ref is not None else Decimal("5.0")
        except Exception:
            cb_pct, ref_pct = Decimal("2.0"), Decimal("5.0")
        return max(Decimal("0.0"), (cb_pct + ref_pct) / Decimal("100.0"))

    def profit_guard_min_payable(
        self,
        gross_total: Decimal,
        supplier_cost: Decimal,
        incentive_rate: Decimal,
        safety_floor: Decimal = DEFAULT_PROFIT_SAFETY_FLOOR,
    ) -> Decimal:
        """
        Minimum the customer must actually pay so net margin never goes negative.

        IMPORTANT: cashback and referral commission are credited from `order.our_price`
        (the GROSS pre-discount value) by the JIT executor, so the incentive reserve
        must be computed on the gross total -- not on the discounted amount paid.
        ROUND_CEILING guarantees we never round the floor down into a loss.
        """
        incentive_reserve = (gross_total * incentive_rate).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        return (supplier_cost + incentive_reserve + safety_floor).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )

    def clamp_total_discount(
        self,
        gross_total: Decimal,
        requested_discount: Decimal,
        supplier_cost: Decimal,
        incentive_rate: Decimal,
        safety_floor: Decimal = DEFAULT_PROFIT_SAFETY_FLOOR,
    ) -> Decimal:
        """Caps stacked discounts so the payable amount stays above the profit floor."""
        min_payable = self.profit_guard_min_payable(
            gross_total, supplier_cost, incentive_rate, safety_floor
        )
        max_discount = max(Decimal("0.00"), gross_total - min_payable)
        return max(Decimal("0.00"), min(requested_discount, max_discount))

    def enforce_profit_floor(
        self,
        gross_total: Decimal,
        payable: Decimal,
        supplier_cost: Decimal,
        incentive_rate: Decimal,
        safety_floor: Decimal = DEFAULT_PROFIT_SAFETY_FLOOR,
    ) -> Decimal:
        """
        Final backstop: never let the charged amount fall below the safe floor, even for
        low-markup products where the list price itself is under cost + incentives.
        """
        min_payable = self.profit_guard_min_payable(
            gross_total, supplier_cost, incentive_rate, safety_floor
        )
        return max(payable, min_payable)

    async def get_active_pricing_rule(
        self,
        session: AsyncSession,
        product_id: Optional[str] = None,
        category_id: Optional[str] = None
    ) -> Optional[PricingRule]:
        """Resolves rule cascade: Product Override -> Category Override -> Global Default."""
        import uuid
        # 1. Product-specific rule
        if product_id:
            p_uuid = uuid.UUID(product_id) if isinstance(product_id, str) else product_id
            stmt = select(PricingRule).where(
                and_(
                    PricingRule.scope == PricingScope.PRODUCT,
                    PricingRule.scope_id == p_uuid,
                    PricingRule.is_active == True
                )
            ).order_by(PricingRule.priority.desc())
            res = await session.execute(stmt)
            rule = res.scalars().first()
            if rule:
                return rule

        # 2. Category-specific rule
        if category_id:
            c_uuid = uuid.UUID(category_id) if isinstance(category_id, str) else category_id
            stmt = select(PricingRule).where(
                and_(
                    PricingRule.scope == PricingScope.CATEGORY,
                    PricingRule.scope_id == c_uuid,
                    PricingRule.is_active == True
                )
            ).order_by(PricingRule.priority.desc())
            res = await session.execute(stmt)
            rule = res.scalars().first()
            if rule:
                return rule

        # 3. Global default rule
        stmt = select(PricingRule).where(
            and_(
                PricingRule.scope == PricingScope.GLOBAL,
                PricingRule.is_active == True
            )
        ).order_by(PricingRule.priority.desc())
        res = await session.execute(stmt)
        return res.scalars().first()

    def calculate_price_from_cost(
        self,
        wholesale_cost: Decimal,
        rule: Optional[PricingRule]
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Returns (final_price, profit_margin, gas_buffer).
        """
        if rule and not rule.gas_buffer_enabled:
            gas_buffer = Decimal("0.00")
        elif rule and rule.gas_buffer_fixed is not None:
            gas_buffer = rule.gas_buffer_fixed
        else:
            gas_buffer = Decimal(str(settings.DEFAULT_GAS_BUFFER_USD))

        if not rule:
            # Fallback default: +$5 fixed margin
            margin = Decimal("5.00")
            final_price = wholesale_cost + margin + gas_buffer
            return final_price.quantize(Decimal("0.01")), margin, gas_buffer

        if rule.rule_type == PricingRuleType.FIXED:
            margin = rule.value
            final_price = wholesale_cost + margin + gas_buffer
        elif rule.rule_type == PricingRuleType.PERCENTAGE:
            # e.g. 0.25 for 25%
            margin = wholesale_cost * (rule.value if rule.value < 1 else rule.value / Decimal("100.0"))
            final_price = wholesale_cost + margin + gas_buffer
        elif rule.rule_type in (PricingRuleType.FLOOR, PricingRuleType.FIXED_PRICE):
            # Fixed retail override: sell at rule.value unless wholesale(+gas+safety) exceeds it.
            min_viable = (wholesale_cost + gas_buffer + self.DEFAULT_PROFIT_SAFETY_FLOOR).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING
            )
            final_price = max(rule.value, min_viable)
            margin = final_price - wholesale_cost - gas_buffer
        else:
            margin = Decimal("5.00")
            final_price = wholesale_cost + margin + gas_buffer

        return final_price.quantize(Decimal("0.01")), margin.quantize(Decimal("0.01")), gas_buffer

    async def get_best_supplier_and_price(
        self,
        session: AsyncSession,
        master_product_id: Any
    ) -> Dict[str, Any]:
        """
        Finds the winning supplier (cheapest in-stock & active), computes retail price.
        Out-of-stock (is_in_stock=False OR available_quantity<=0) suppliers are ignored.
        """
        import uuid
        prod_uuid = uuid.UUID(master_product_id) if isinstance(master_product_id, str) else master_product_id

        stmt = (
            select(ProductSupplierLink)
            .join(Supplier, ProductSupplierLink.supplier_id == Supplier.id)
            .where(
                and_(
                    ProductSupplierLink.master_product_id == prod_uuid,
                    ProductSupplierLink.is_in_stock == True,
                    Supplier.status == SupplierStatus.ACTIVE,
                    ProductSupplierLink.last_known_cost.isnot(None),
                    or_(
                        ProductSupplierLink.available_quantity.is_(None),
                        ProductSupplierLink.available_quantity > 0,
                    ),
                )
            )
            .options(selectinload(ProductSupplierLink.supplier))
            # Cheapest wholesale wins; priority only breaks cost ties.
            # quality_penalty_score lowers effective routing priority (Phase 17).
            .order_by(
                ProductSupplierLink.last_known_cost.asc(),
                ProductSupplierLink.priority_override.desc().nullslast(),
                (Supplier.priority_weight - Supplier.quality_penalty_score).desc(),
            )
        )
        res = await session.execute(stmt)
        links = res.scalars().all()

        if not links:
            return {
                "is_available": False,
                "winner_link": None,
                "supplier": None,
                "wholesale_cost": None,
                "supplier_cost": None,
                "final_price": None,
                "profit_margin": None,
                "gas_buffer": None,
                "all_candidates": [],
            }

        winner_link = links[0]
        cost = winner_link.last_known_cost

        # Load product category
        prod_stmt = select(MasterProduct).where(MasterProduct.id == prod_uuid)
        prod_res = await session.execute(prod_stmt)
        product = prod_res.scalar_one_or_none()

        rule = await self.get_active_pricing_rule(
            session,
            product_id=str(master_product_id),
            category_id=str(product.category_id) if product and product.category_id else None
        )

        final_price, margin, gas_buf = self.calculate_price_from_cost(cost, rule)

        return {
            "is_available": True,
            "winner_link": winner_link,
            "supplier": winner_link.supplier,
            "wholesale_cost": cost,
            "supplier_cost": cost,
            "final_price": final_price,
            "profit_margin": margin,
            "gas_buffer": gas_buf,
            "bulk_pricing_tiers": getattr(product, "bulk_pricing_tiers", None) if product else None,
            "warranty_days": getattr(product, "warranty_days", 30) if product else 30,
            "all_candidates": links,
        }

    def normalize_bulk_tiers(self, tiers: Any) -> List[Dict[str, Decimal]]:
        """Parse [{min_qty, unit_price}] into sorted ascending min_qty list."""
        if not tiers:
            return []
        raw = tiers if isinstance(tiers, list) else []
        out: List[Dict[str, Decimal]] = []
        for t in raw:
            if not isinstance(t, dict):
                continue
            try:
                mq = int(t.get("min_qty") or t.get("from") or 0)
                up = Decimal(str(t.get("unit_price") or t.get("price") or 0))
            except Exception:
                continue
            if mq >= 2 and up > 0:
                out.append({"min_qty": mq, "unit_price": up.quantize(Decimal("0.01"))})
        out.sort(key=lambda x: x["min_qty"])
        return out

    def resolve_bulk_unit_price(
        self,
        list_unit_price: Decimal,
        quantity: int,
        tiers: Any,
        *,
        unit_cost: Decimal,
        incentive_rate: Decimal,
    ) -> Decimal:
        """
        Apply the highest matching bulk tier unit price, then clamp so
        qty * unit never falls below Profit Guard min payable.
        """
        qty = max(1, int(quantity))
        unit = Decimal(str(list_unit_price)).quantize(Decimal("0.01"))
        for tier in reversed(self.normalize_bulk_tiers(tiers)):
            if qty >= int(tier["min_qty"]):
                unit = Decimal(str(tier["unit_price"])).quantize(Decimal("0.01"))
                break

        gross = (list_unit_price * qty).quantize(Decimal("0.01"))
        total_cost = (Decimal(str(unit_cost)) * qty).quantize(Decimal("0.01"))
        min_payable = self.profit_guard_min_payable(gross, total_cost, incentive_rate)
        min_unit = (min_payable / Decimal(qty)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        return max(unit, min_unit)

    def format_bulk_tiers_caption(self, list_unit_price: Decimal, tiers: Any) -> str:
        """Arabic/English bulk lines for channel restock cards."""
        lines = []
        for tier in self.normalize_bulk_tiers(tiers):
            mq = int(tier["min_qty"])
            up = tier["unit_price"]
            if up >= list_unit_price:
                continue
            lines.append(f"• From {mq}+: `${up:.2f}` / each")
        if not lines:
            return ""
        return "➕ **Bulk pricing**\n" + "\n".join(lines)


pricing_service = PricingService()
