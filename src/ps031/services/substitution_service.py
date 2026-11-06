from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from ps031.models.order import OrderItem, SubstitutionPolicy
from ps031.models.product import Product


def _extract_sku(candidate: Any) -> str:
    sku = getattr(candidate, "sku", getattr(candidate, "sku_id", None))
    if sku is None or isinstance(sku, bool) or not isinstance(sku, str):
        raise TypeError(f"Candidate SKU must be a string, got {type(sku).__name__}.")
    cleaned = sku.strip()
    if not cleaned:
        raise ValueError("Candidate SKU must not be empty.")
    return cleaned


def _extract_price(candidate: Any) -> Decimal:
    price = getattr(candidate, "price", None)
    if price is None:
        raise ValueError(f"Candidate {candidate!r} missing required 'price' attribute.")
    if isinstance(price, bool):
        raise TypeError("Candidate price cannot be a boolean, got bool.")
    if not isinstance(price, Decimal):
        try:
            price = Decimal(str(price))
        except (InvalidOperation, TypeError, ValueError) as err:
            raise ValueError(f"Invalid candidate price: {price}") from err
    if not price.is_finite():
        raise ValueError(f"Candidate price must be a finite number, got {price}.")
    if price < Decimal("0.00"):
        raise ValueError(f"Candidate price cannot be negative: {price}.")
    return price


def _extract_category(candidate: Any) -> str:
    category = getattr(candidate, "category", "")
    if isinstance(category, bool) or not isinstance(category, str):
        raise TypeError(
            f"Candidate category must be a string, got {type(category).__name__}."
        )
    return category


def _extract_brand(candidate: Any) -> str:
    brand = getattr(candidate, "brand", "")
    if isinstance(brand, bool) or not isinstance(brand, str):
        raise TypeError(
            f"Candidate brand must be a string, got {type(brand).__name__}."
        )
    return brand


def _extract_stock(
    candidate: Any,
    candidate_sku: str,
    stock_by_sku: Mapping[str, int] | None = None,
) -> int | None:
    if stock_by_sku is not None:
        if candidate_sku not in stock_by_sku and not hasattr(candidate, "stock"):
            return 0
        raw_stock = stock_by_sku.get(
            candidate_sku, getattr(candidate, "stock", 0)
        )
    elif hasattr(candidate, "stock"):
        raw_stock = getattr(candidate, "stock")
    else:
        # Stock is not tracked directly on candidate (e.g. catalog Product).
        # Availability is considered unconstrained unless stock_by_sku is provided.
        return None

    if isinstance(raw_stock, bool) or not isinstance(raw_stock, int):
        raise TypeError(
            f"Stock for '{candidate_sku}' must be an integer, got {type(raw_stock).__name__}."
        )
    if raw_stock < 0:
        raise ValueError(
            f"Stock for '{candidate_sku}' cannot be negative: {raw_stock}."
        )
    return raw_stock


def _normalize_policy(preference: SubstitutionPolicy | str) -> SubstitutionPolicy:
    if isinstance(preference, SubstitutionPolicy):
        return preference
    if isinstance(preference, str) and not isinstance(preference, bool):
        try:
            return SubstitutionPolicy(preference)
        except ValueError:
            try:
                return SubstitutionPolicy(preference.upper())
            except ValueError:
                try:
                    return SubstitutionPolicy[preference.upper()]
                except (KeyError, AttributeError):
                    raise ValueError(f"Invalid substitution policy: {preference}")
    raise TypeError(
        f"preference must be SubstitutionPolicy or str, got {type(preference).__name__}."
    )


@dataclass(frozen=True)
class SubstitutionResult:
    """Structured outcome of an item substitution resolution or order-line update."""

    original_product_id: str
    substituted_product_sku: str | None
    fulfilled_quantity: int
    shortfall_quantity: int
    charged_unit_price: Decimal
    substituted_quantity: int = 0

    def __init__(
        self,
        original_product_id: str | None = None,
        substituted_product_sku: str | None = None,
        fulfilled_quantity: int | None = None,
        shortfall_quantity: int | None = None,
        charged_unit_price: Decimal | int | float | str = Decimal("0.00"),
        substituted_quantity: int = 0,
        *,
        original_sku_id: str | None = None,
        substituted_sku_id: str | None = None,
        fulfilled_qty: int | None = None,
        shortfall_qty: int | None = None,
    ) -> None:
        resolved_orig = (
            original_product_id if original_product_id is not None else original_sku_id
        )
        resolved_sub = (
            substituted_product_sku
            if substituted_product_sku is not None
            else substituted_sku_id
        )
        resolved_ful = (
            fulfilled_quantity if fulfilled_quantity is not None else fulfilled_qty
        )
        resolved_short = (
            shortfall_quantity if shortfall_quantity is not None else shortfall_qty
        )

        if not isinstance(charged_unit_price, Decimal):
            charged_unit_price = Decimal(str(charged_unit_price))

        object.__setattr__(self, "original_product_id", resolved_orig or "")
        object.__setattr__(self, "substituted_product_sku", resolved_sub)
        object.__setattr__(
            self, "fulfilled_quantity", resolved_ful if resolved_ful is not None else 0
        )
        object.__setattr__(
            self,
            "shortfall_quantity",
            resolved_short if resolved_short is not None else 0,
        )
        object.__setattr__(self, "charged_unit_price", charged_unit_price)
        object.__setattr__(self, "substituted_quantity", substituted_quantity)

    @property
    def original_sku_id(self) -> str:
        return self.original_product_id

    @property
    def substituted_sku_id(self) -> str | None:
        return self.substituted_product_sku

    @property
    def fulfilled_qty(self) -> int:
        return self.fulfilled_quantity

    @property
    def shortfall_qty(self) -> int:
        return self.shortfall_quantity


@runtime_checkable
class SubstitutionServiceInterface(Protocol):
    """Protocol defining the item substitution service contract."""

    def select_candidate(
        self,
        candidates: Sequence[Product | Any],
        requested_item: OrderItem,
    ) -> Product | Any:
        ...

    def resolve_line_shortfall(
        self,
        requested_item: OrderItem,
        available_inventory: Sequence[Product | Any],
        preference: SubstitutionPolicy | str,
        needed_qty: int | None = None,
        stock_by_sku: Mapping[str, int] | None = None,
    ) -> SubstitutionResult:
        ...

    def apply_substitution(
        self,
        item: OrderItem,
        available_inventory: Sequence[Product | Any] | None = None,
        preference: SubstitutionPolicy | str | None = None,
        needed_qty: int | None = None,
        stock_by_sku: Mapping[str, int] | None = None,
        *,
        substitute_product: Product | Any | str | None = None,
        reserved_quantity: int | None = None,
        reservation_result: Any | None = None,
    ) -> SubstitutionResult:
        ...


class SubstitutionService:
    """
    Implements BR-05: Product Substitution.

    Rules:
    - ALLOW: permits any different SKU in the same category, with a price no more than
      15% above the original product's price (boundary at exactly +15% is eligible).
      Customer pays the original requested item's unit price.
    - SIMILAR_ONLY: permits only a different SKU from the same brand.
      Does not additionally require the same category or the +15% price ceiling.
      Customer pays the original requested item's unit price.
    - NONE: no substitution; the shortfall remains a partial line.
    - Substituted line never costs more than the original because customer pays the original unit price.
    - Fulfillment is capped by available inventory stock and remaining shortfall.
    - Preserves existing fulfilled units and accumulates only actually reserved substitute units.
    - Candidate selection priority:
      1. Minimal absolute price delta abs(candidate.price - original_price)
      2. Lower candidate price
      3. Lexicographical SKU sort
    """

    MAX_PRICE_MULTIPLIER = Decimal("1.15")

    def select_candidate(
        self,
        candidates: Sequence[Product | Any],
        requested_item: OrderItem,
    ) -> Product | Any:
        """
        Deterministic tie-breaking:
        1. Smallest absolute price difference from original unit price
        2. Lower candidate price
        3. Lexicographical SKU sort
        """
        if not isinstance(requested_item, OrderItem):
            raise TypeError(
                f"requested_item must be an OrderItem instance, got {type(requested_item).__name__}."
            )
        if not candidates:
            raise ValueError("No candidates available for selection.")

        orig_price = requested_item.unit_price

        return min(
            candidates,
            key=lambda c: (
                abs(_extract_price(c) - orig_price),
                _extract_price(c),
                _extract_sku(c),
            ),
        )

    def resolve_line_shortfall(
        self,
        requested_item: OrderItem,
        available_inventory: Sequence[Product | Any],
        preference: SubstitutionPolicy | str,
        needed_qty: int | None = None,
        stock_by_sku: Mapping[str, int] | None = None,
    ) -> SubstitutionResult:
        """
        Pure decision method: resolves shortfall for a requested order item
        against available store inventory according to customer substitution preference.
        """
        if not isinstance(requested_item, OrderItem):
            raise TypeError(
                f"requested_item must be an OrderItem instance, got {type(requested_item).__name__}."
            )

        policy = _normalize_policy(preference)

        if needed_qty is not None:
            if isinstance(needed_qty, bool) or not isinstance(needed_qty, int):
                raise TypeError(
                    f"needed_qty must be an integer, got {type(needed_qty).__name__}."
                )
            if needed_qty < 0:
                raise ValueError(f"needed_qty cannot be negative, got {needed_qty}.")
            qty_needed = needed_qty
        else:
            qty_needed = max(
                0, requested_item.quantity - requested_item.fulfilled_quantity
            )

        orig_price = requested_item.unit_price

        # If no shortfall exists or policy is NONE, return early
        if qty_needed == 0:
            return SubstitutionResult(
                original_product_id=requested_item.product_id,
                substituted_product_sku=requested_item.substituted_product_sku,
                fulfilled_quantity=requested_item.fulfilled_quantity,
                shortfall_quantity=0,
                charged_unit_price=orig_price,
                substituted_quantity=requested_item.substituted_quantity,
            )

        if policy == SubstitutionPolicy.NONE:
            return SubstitutionResult(
                original_product_id=requested_item.product_id,
                substituted_product_sku=requested_item.substituted_product_sku,
                fulfilled_quantity=requested_item.fulfilled_quantity,
                shortfall_quantity=qty_needed,
                charged_unit_price=orig_price,
                substituted_quantity=requested_item.substituted_quantity,
            )

        max_allowed_price = orig_price * self.MAX_PRICE_MULTIPLIER

        eligible_candidates: list[Product | Any] = []
        for candidate in available_inventory:
            c_sku = _extract_sku(candidate)

            # Rule: Original SKU is never selected as its own substitute
            if c_sku == requested_item.product_id:
                continue

            # Respect single-substitute SKU constraint if line already has one recorded
            if (
                requested_item.substituted_product_sku is not None
                and requested_item.substituted_quantity > 0
                and c_sku != requested_item.substituted_product_sku
            ):
                continue

            # Stock check: Zero-stock candidate is not selected
            c_stock = _extract_stock(candidate, c_sku, stock_by_sku)
            if c_stock is not None and c_stock <= 0:
                continue

            c_price = _extract_price(candidate)
            c_category = _extract_category(candidate)
            c_brand = _extract_brand(candidate)

            # Policy filtering
            if policy == SubstitutionPolicy.ALLOW:
                # 1. ALLOW: same category, price <= +15% of original
                if c_category != requested_item.category:
                    continue
                if c_price > max_allowed_price:
                    continue
                eligible_candidates.append(candidate)
            elif policy == SubstitutionPolicy.SIMILAR_ONLY:
                # 2. SIMILAR_ONLY: same brand. Does NOT require same category or +15% ceiling.
                if c_brand != requested_item.brand:
                    continue
                eligible_candidates.append(candidate)

        if not eligible_candidates:
            return SubstitutionResult(
                original_product_id=requested_item.product_id,
                substituted_product_sku=requested_item.substituted_product_sku,
                fulfilled_quantity=requested_item.fulfilled_quantity,
                shortfall_quantity=qty_needed,
                charged_unit_price=orig_price,
                substituted_quantity=requested_item.substituted_quantity,
            )

        chosen = self.select_candidate(eligible_candidates, requested_item)
        chosen_sku = _extract_sku(chosen)
        chosen_stock = _extract_stock(chosen, chosen_sku, stock_by_sku)

        if chosen_stock is not None:
            sub_fulfilled = min(qty_needed, chosen_stock)
        else:
            sub_fulfilled = qty_needed
        shortfall = qty_needed - sub_fulfilled

        return SubstitutionResult(
            original_product_id=requested_item.product_id,
            substituted_product_sku=chosen_sku,
            fulfilled_quantity=requested_item.fulfilled_quantity + sub_fulfilled,
            shortfall_quantity=shortfall,
            charged_unit_price=orig_price,
            substituted_quantity=requested_item.substituted_quantity + sub_fulfilled,
        )

    def apply_substitution(
        self,
        item: OrderItem,
        available_inventory: Sequence[Product | Any] | None = None,
        preference: SubstitutionPolicy | str | None = None,
        needed_qty: int | None = None,
        stock_by_sku: Mapping[str, int] | None = None,
        *,
        substitute_product: Product | Any | str | None = None,
        reserved_quantity: int | None = None,
        reservation_result: Any | None = None,
    ) -> SubstitutionResult:
        """
        Updates an OrderItem with substitution data.

        - Preserves units already fulfilled with original product.
        - Adds only actually reserved substitute quantity to fulfilled quantity.
        - Does not erase existing fulfilment when no eligible candidate is found.
        - Never fulfils more than the ordered quantity.
        - Never records more substituted units than remaining shortfall.
        - Prevents silently replacing an already recorded different substitute SKU.
        - Customer charge remains the original product price.
        """
        if not isinstance(item, OrderItem):
            raise TypeError(
                f"item must be an OrderItem instance, got {type(item).__name__}."
            )

        # Path A: Authoritative reservation application
        if reservation_result is not None or reserved_quantity is not None:
            if reservation_result is not None:
                sub_sku = getattr(reservation_result, "sku", None)
                actual_reserved = getattr(reservation_result, "reserved_quantity", 0)
            else:
                if substitute_product is None:
                    raise ValueError(
                        "substitute_product or substitute SKU is required when reserved_quantity is provided."
                    )
                sub_sku = (
                    substitute_product
                    if isinstance(substitute_product, str)
                    else _extract_sku(substitute_product)
                )
                actual_reserved = reserved_quantity

            if isinstance(actual_reserved, bool) or not isinstance(actual_reserved, int):
                raise TypeError(
                    f"Reserved quantity must be an integer, got {type(actual_reserved).__name__}."
                )
            if actual_reserved < 0:
                raise ValueError(
                    f"Reserved quantity cannot be negative, got {actual_reserved}."
                )

            if sub_sku is None or not str(sub_sku).strip():
                raise ValueError("A valid substitute SKU is required for substitution.")
            sub_sku = str(sub_sku).strip()

            if sub_sku == item.product_id:
                raise ValueError(
                    "Substitute SKU cannot be the same as the original product ID."
                )

            # Guard single substitute SKU per line
            if (
                item.substituted_product_sku is not None
                and item.substituted_quantity > 0
                and item.substituted_product_sku != sub_sku
            ):
                raise ValueError(
                    f"OrderItem already has a different substitute SKU recorded ('{item.substituted_product_sku}'). "
                    f"Cannot replace with '{sub_sku}'."
                )

            # Authoritative shortfall cap
            remaining_shortfall = max(0, item.quantity - item.fulfilled_quantity)

            if actual_reserved > remaining_shortfall:
                raise ValueError(
                    f"Reserved quantity ({actual_reserved}) exceeds "
                    f"remaining order-line shortfall ({remaining_shortfall}). "
                    "Excess reservation must be released.")

            qty_to_add = actual_reserved

            if qty_to_add > 0:
                item.substituted_product_sku = sub_sku
                item.substituted_quantity = item.substituted_quantity + qty_to_add
                item.fulfilled_quantity = item.fulfilled_quantity + qty_to_add

            item.charged_unit_price = item.unit_price

            return SubstitutionResult(
                original_product_id=item.product_id,
                substituted_product_sku=item.substituted_product_sku,
                fulfilled_quantity=item.fulfilled_quantity,
                shortfall_quantity=max(0, item.quantity - item.fulfilled_quantity),
                charged_unit_price=item.unit_price,
                substituted_quantity=item.substituted_quantity,
            )

        # Path B: Decision + apply flow
        if available_inventory is None or preference is None:
            raise ValueError(
                "Either (available_inventory and preference) or (substitute_product/reservation_result and reserved_quantity) must be provided."
            )

        result = self.resolve_line_shortfall(
            requested_item=item,
            available_inventory=available_inventory,
            preference=preference,
            needed_qty=needed_qty,
            stock_by_sku=stock_by_sku,
        )

        if result.substituted_product_sku is not None:
            new_sub_units = result.substituted_quantity - item.substituted_quantity
            if new_sub_units > 0:
                return self.apply_substitution(
                    item=item,
                    substitute_product=result.substituted_product_sku,
                    reserved_quantity=new_sub_units,
                )

        # No eligible substitute found or preference is NONE: preserve existing fulfillment
        item.charged_unit_price = item.unit_price
        return SubstitutionResult(
            original_product_id=item.product_id,
            substituted_product_sku=item.substituted_product_sku,
            fulfilled_quantity=item.fulfilled_quantity,
            shortfall_quantity=max(0, item.quantity - item.fulfilled_quantity),
            charged_unit_price=item.unit_price,
            substituted_quantity=item.substituted_quantity,
        )

    def apply_reservation(
        self,
        item: OrderItem,
        reservation: Any,
        substitute_product: Product | Any | str | None = None,
    ) -> SubstitutionResult:
        """Convenience method to apply an inventory reservation result to an OrderItem."""
        return self.apply_substitution(
            item=item,
            reservation_result=(
                reservation if hasattr(reservation, "reserved_quantity") else None
            ),
            reserved_quantity=reservation if isinstance(reservation, int) else None,
            substitute_product=substitute_product,
        )
