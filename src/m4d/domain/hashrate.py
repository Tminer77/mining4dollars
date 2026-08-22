"""Hashrate and electrical power.

Stored internally as hashes per second and watts so ranking never depends on
which marketing unit (MH/s, GH/s, TH/s) a pool dashboard happened to print.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from m4d.domain.errors import ValidationError
from m4d.domain.primitives import parse_decimal

__all__ = ["Hashrate", "PowerWatts"]


@dataclass(frozen=True, slots=True, order=True, init=False)
class Hashrate:
    """Hashes per second. Zero is allowed (a rig that hashrate-dropped)."""

    hps: Decimal

    def __init__(self, hps: Decimal | int | str) -> None:
        parsed = parse_decimal(hps, name="hashrate")
        if parsed < 0:
            raise ValidationError(
                "Hashrate cannot be negative.", field="hashrate", value=str(parsed)
            )
        object.__setattr__(self, "hps", parsed)

    def ratio_to(self, reference: Hashrate) -> Decimal:
        """How many times this hashrate is of ``reference``.

        Used to scale a market quote that was published for a reference speed
        onto this worker's actual speed.
        """
        if reference.hps == 0:
            raise ValidationError(
                "A market quote's reference hashrate must be greater than zero.",
                field="reference_hashrate_hps",
            )
        return self.hps / reference.hps

    def as_str(self) -> str:
        """Canonical wire form."""
        return format(self.hps, "f")


@dataclass(frozen=True, slots=True, order=True, init=False)
class PowerWatts:
    """Instantaneous electrical draw in watts."""

    watts: Decimal

    def __init__(self, watts: Decimal | int | str) -> None:
        parsed = parse_decimal(watts, name="power_watts")
        if parsed < 0:
            raise ValidationError(
                "Power draw cannot be negative.", field="power_watts", value=str(parsed)
            )
        object.__setattr__(self, "watts", parsed)

    def kilowatts(self) -> Decimal:
        """Draw in kW, for electricity-cost arithmetic."""
        return self.watts / Decimal(1000)

    def as_str(self) -> str:
        """Canonical wire form."""
        return format(self.watts, "f")
