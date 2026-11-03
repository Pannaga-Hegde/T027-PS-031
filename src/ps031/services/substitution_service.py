from dataclasses import dataclass
from decimal import Decimal

from ps031.models.inventory import InventoryItem
from ps031.models.order import OrderItem, SubstitutionPolicy


@dataclass(frozen=True)
class SubstitutionResult:
    original_sku_id: str
    substituted_sku_id: str | None
    fulfilled_qty: int
    shortfall_qty: int
    charged_unit_price: Decimal


class SubstitutionService:
    """
    Implements BR-05: Product Substitution.

    Rules:
    - ALLOW: permits any SKU in the same category with price within +15% of the original.
      The customer pays the original requested item's unit price (even if the substitute is cheaper).
    - SIMILAR_ONLY: permits only the same brand (and same category, within +15% price).
      The customer pays the original requested item's unit price.
    - NONE: converts the shortfall into a partial line (no substitution).
    - Substituted line never costs more than the original because customer pays the original unit price.
    - Fulfillment is capped by available inventory stock.
    - Candidate selection priority (OQ-04): Deterministic ordering by:
      1. Minimal price delta abs(candidate.price - original_price)
      2. Lower candidate price
      3. Lexicographical sku_id sort
    """

    MAX_PRICE_MULTIPLIER = Decimal("1.15")

    def resolve_line_shortfall(
        self,
        requested_item: OrderItem,
        available_inventory: list[InventoryItem],
        preference: SubstitutionPolicy,
        needed_qty: int | None = None,
    ) -> SubstitutionResult:
        """
        Pure decision method: resolves shortfall for a requested order item
        against available store inventory according to customer substitution preference.
        """
        qty_needed = needed_qty if needed_qty is not None else requested_item.requested_qty
        if qty_needed <= 0:
            return SubstitutionResult(
                original_sku_id=requested_item.sku_id,
                substituted_sku_id=None,
                fulfilled_qty=0,
                shortfall_qty=0,
                charged_unit_price=requested_item.unit_price,
            )

        if preference == SubstitutionPolicy.NONE:
            return SubstitutionResult(
                original_sku_id=requested_item.sku_id,
                substituted_sku_id=None,
                fulfilled_qty=0,
                shortfall_qty=qty_needed,
                charged_unit_price=requested_item.unit_price,
            )

        max_allowed_price = requested_item.unit_price * self.MAX_PRICE_MULTIPLIER

        eligible_candidates: list[InventoryItem] = []
        for candidate in available_inventory:
            # Candidate must be a different SKU with positive stock
            if candidate.sku_id == requested_item.sku_id or candidate.stock <= 0:
                continue

            # Must be in the same category
            if candidate.category != requested_item.category:
                continue

            # Price must be within +15% of original unit price
            if candidate.price > max_allowed_price:
                continue

            # SIMILAR_ONLY requires same brand
            if preference == SubstitutionPolicy.SIMILAR_ONLY:
                if candidate.brand != requested_item.brand:
                    continue

            eligible_candidates.append(candidate)

        if not eligible_candidates:
            return SubstitutionResult(
                original_sku_id=requested_item.sku_id,
                substituted_sku_id=None,
                fulfilled_qty=0,
                shortfall_qty=qty_needed,
                charged_unit_price=requested_item.unit_price,
            )

        # OQ-04: Deterministic selection among multiple eligible candidates
        chosen_candidate = self.select_candidate(eligible_candidates, requested_item)

        fulfilled_qty = min(qty_needed, chosen_candidate.stock)
        shortfall_qty = qty_needed - fulfilled_qty

        # BR-05: The customer pays the original requested item's unit price for substituted units
        charged_unit_price = requested_item.unit_price

        return SubstitutionResult(
            original_sku_id=requested_item.sku_id,
            substituted_sku_id=chosen_candidate.sku_id,
            fulfilled_qty=fulfilled_qty,
            shortfall_qty=shortfall_qty,
            charged_unit_price=charged_unit_price,
        )

    def select_candidate(
        self,
        candidates: list[InventoryItem],
        requested_item: OrderItem,
    ) -> InventoryItem:
        """
        Deterministic tie-breaking for OQ-04.
        1. Minimal absolute price delta from original unit price
        2. Lower price
        3. Lexicographical sku_id sort
        """
        return min(
            candidates,
            key=lambda c: (
                abs(c.price - requested_item.unit_price),
                c.price,
                c.sku_id,
            ),
        )

    def apply_substitution(
        self,
        item: OrderItem,
        available_inventory: list[InventoryItem],
        preference: SubstitutionPolicy,
        needed_qty: int | None = None,
    ) -> SubstitutionResult:
        """
        Applies substitution decision onto the OrderItem in-place and returns result.
        """
        result = self.resolve_line_shortfall(
            requested_item=item,
            available_inventory=available_inventory,
            preference=preference,
            needed_qty=needed_qty,
        )
        if result.substituted_sku_id is not None:
            item.substituted_sku_id = result.substituted_sku_id
            item.fulfilled_qty = result.fulfilled_qty
            item.charged_unit_price = result.charged_unit_price
        else:
            item.substituted_sku_id = None
            item.fulfilled_qty = 0
            item.charged_unit_price = item.unit_price
        return result
