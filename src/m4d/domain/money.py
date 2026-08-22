"""US-dollar amounts.

Mining profitability is a money problem. Using binary floats for dollars is how
you end up ranking the wrong coin by a rounding error; every dollar figure in
the domain is a :class:`Money` over :class:`~decimal.Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from m4d.domain.primitives import parse_decimal

__all__ = ["DOLLARS", "ZERO", "Money"]

#: Eight decimal places is enough for per-hash revenue without pretending we
#: have infinite precision. Quantising here means two Money values that print
#: the same way also compare equal.
DOLLARS = Decimal("0.00000001")


@dataclass(frozen=True, slots=True, order=True, init=False)
class Money:
    """A USD amount, quantised to eight decimal places.

    Negative values are allowed: a coin that does not cover electricity is
    still a ranking result, not a crash.
    """

    amount: Decimal

    def __init__(self, amount: Decimal | int | str) -> None:
        parsed = parse_decimal(amount, name="amount")
        object.__setattr__(self, "amount", parsed.quantize(DOLLARS, rounding=ROUND_HALF_UP))

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.amount - other.amount)

    def __neg__(self) -> Money:
        return Money(-self.amount)

    def scale(self, factor: Decimal | int | str) -> Money:
        """Return this amount multiplied by ``factor`` (hashrate ratios, hours)."""
        return Money(self.amount * parse_decimal(factor, name="factor"))

    @property
    def is_positive(self) -> bool:
        """Whether this amount is strictly greater than zero."""
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        """Whether this amount is strictly less than zero."""
        return self.amount < 0

    def as_str(self) -> str:
        """Canonical wire form: fixed-point decimal, no scientific notation."""
        return format(self.amount, "f")

    def __repr__(self) -> str:
        return f"Money({self.as_str()!r})"


ZERO = Money(0)
