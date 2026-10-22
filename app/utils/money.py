"""Money helpers: exact decimals, per-currency precision and the gateway's smallest-unit integers."""
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from typing import Any

ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}
THREE_DECIMAL_CURRENCIES = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}

ROUNDING_MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "up": ROUND_UP, "down": ROUND_DOWN}


def default_decimals(code: str) -> int:
    code = (code or "").upper()
    if code in ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


def to_decimal(value: Any, default: Decimal | None = None) -> Decimal:
    """A finite Decimal from a number or string. Floats go through str() so 0.1 stays 0.1."""
    if isinstance(value, Decimal):
        number = value
    else:
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, AttributeError):
            if default is not None:
                return default
            raise ValueError(f"not a number: {value!r}")
    if not number.is_finite():
        if default is not None:
            return default
        raise ValueError(f"not a finite number: {value!r}")
    return number


def quantize(amount: Decimal, decimals: int, mode: str = "half_up") -> Decimal:
    """Round to the currency's decimals with the named rule (half_up, half_even, up, down)."""
    step = Decimal(1).scaleb(-decimals)
    return to_decimal(amount).quantize(step, rounding=ROUNDING_MODES.get(mode, ROUND_HALF_UP))


def to_minor(amount: Decimal, decimals: int) -> int:
    """The gateway's integer: cents, paise, yen. The amount must already be rounded to the currency."""
    return int((quantize(amount, decimals) * (Decimal(10) ** decimals)).to_integral_value())


def from_minor(value: int, decimals: int) -> Decimal:
    return (Decimal(int(value)) / (Decimal(10) ** decimals)).quantize(Decimal(1).scaleb(-decimals))


def money_str(amount: Decimal, decimals: int) -> str:
    """12345.5 -> '12,345.50' (always the currency's decimals)."""
    return f"{quantize(amount, decimals):,.{decimals}f}"
