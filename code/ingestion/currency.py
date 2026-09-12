"""
Currency conversion for "Buy or Wait?".

The spec says exchange rates are fixed and dated — matched by rate date and
currency pair, not looked up live. This module does exactly that lookup,
with a same-currency short-circuit and a clear error if a required rate
is genuinely missing (better to fail loudly than silently assume 1:1).
"""

from __future__ import annotations

from .loaders import ExchangeRate


class MissingExchangeRateError(Exception):
    pass


def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    on_date: str,
    rates: list[ExchangeRate],
) -> float:
    """Convert `amount` from `from_currency` to `to_currency` using the
    fixed rate dated `on_date`. Tries the direct pair first, then the
    inverse pair (1/rate) if only that direction is supplied."""
    if from_currency == to_currency:
        return amount

    for r in rates:
        if r.rate_date == on_date and r.from_currency == from_currency and r.to_currency == to_currency:
            return amount * r.rate

    for r in rates:
        if r.rate_date == on_date and r.from_currency == to_currency and r.to_currency == from_currency:
            return amount / r.rate

    raise MissingExchangeRateError(
        f"No rate for {from_currency}->{to_currency} on {on_date}. "
        f"Do not invent a rate — this must be investigated, not defaulted."
    )


def to_home_currency(
    amount: float,
    amount_currency: str,
    home_currency: str,
    on_date: str,
    rates: list[ExchangeRate],
) -> float:
    """Convenience wrapper: convert any amount into the user's home_currency."""
    return convert(amount, amount_currency, home_currency, on_date, rates)
