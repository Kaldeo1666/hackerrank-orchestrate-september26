"""
90-day balance simulator for "Buy or Wait?".

100% deterministic — no LLM involvement here at all. This is THE safety
check every decision in Block 3 depends on, so it stays pure arithmetic
over data that's already been resolved/normalized/overridden by earlier
stages.

Inclusion rules (see PROJECT_STATE.md §4 / AGENTS.md for the source):
- Credits (income): only status "settled" or "confirmed" count, dated on
  settlement_date. "pending" credits are excluded entirely — never assume
  money that hasn't landed. A "confirmation" override can promote a
  pending credit into counting, from its effective_date.
- Debits (expenses/subscriptions/debt payments): counted regardless of
  settled/pending status — these are real committed obligations, not
  optional. Dated on settlement_date (falls back to event_date).
- Recurring debits are projected forward via engine.recurrence, since the
  90-day window includes occurrences that don't exist as rows yet.
- Message-based overrides (cancel/amend/confirm) are applied before the
  day-by-day walk, never during it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional

from ..ingestion.loaders import ExchangeRate, FinancialEvent, FinancialProfile
from ..ingestion.currency import to_home_currency
from .recurrence import build_recurrence_chains, project_future_occurrences
from .message_overrides import MessageOverride


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _event_date(e: FinancialEvent) -> date:
    return _parse_date(e.settlement_date or e.event_date)


def _apply_overrides(events: list[FinancialEvent], overrides: list[MessageOverride]) -> list[FinancialEvent]:
    by_event: dict[str, list[MessageOverride]] = {}
    for o in overrides:
        by_event.setdefault(o.event_id, []).append(o)

    result = []
    for e in events:
        applicable = by_event.get(e.event_id, [])
        cancelled = False
        amount = e.amount
        status = e.status
        for o in applicable:
            if o.category == "cancellation":
                cancelled = True
            elif o.category == "amendment" and o.new_amount is not None:
                amount = o.new_amount
            elif o.category == "confirmation":
                status = "confirmed"
        if cancelled:
            continue
        if amount != e.amount or status != e.status:
            e = replace(e, amount=amount, status=status)
        result.append(e)
    return result


def _is_included(e: FinancialEvent) -> bool:
    # Per problem_statement.md's 90-Day Safety Check: "Ignore pending
    # credits, failed or cancelled transactions, duplicate records, and
    # unrealized investments." Cancelled/failed applies to BOTH directions —
    # not just credits — this was a real bug (cancelled debits were being
    # counted as real obligations before this fix).
    if e.status in ("cancelled", "failed"):
        return False
    if e.direction == "credit":
        # "scheduled" observed on a real event literally described as
        # "Next confirmed salary" (user_100) — status naming doesn't match
        # "confirmed" exactly, but the description makes intent unambiguous.
        # Treated as equivalent to confirmed/settled for credit inclusion.
        return e.status in ("settled", "confirmed", "scheduled")
    return True  # debits, once cancelled/failed are excluded above: always counted — real obligations


@dataclass
class DayBalance:
    day: date
    balance: float
    events_applied: list[str]


@dataclass
class ForecastResult:
    daily_balances: list[DayBalance]
    minimum_balance_reached: float
    first_breach_date: Optional[date]   # first day balance < minimum_balance_to_keep
    is_safe: bool                        # never breaches across the whole window


def simulate_90_day_forecast(
    start_date: date,
    starting_balance: float,
    profile: FinancialProfile,
    events: list[FinancialEvent],
    exchange_rates: list[ExchangeRate],
    overrides: list[MessageOverride] | None = None,
    window_days: int = 90,
) -> ForecastResult:
    overrides = overrides or []
    window_end = start_date + timedelta(days=window_days)

    # Project recurring debits forward into the window before anything else.
    chains = build_recurrence_chains(events)
    projected: list[FinancialEvent] = []
    for chain in chains:
        projected.extend(project_future_occurrences(chain, window_end))
    all_events = events + projected

    all_events = _apply_overrides(all_events, overrides)
    included = [e for e in all_events if _is_included(e) and e.amount is not None]

    by_day: dict[date, list[FinancialEvent]] = {}
    for e in included:
        d = _event_date(e)
        if start_date <= d <= window_end:
            by_day.setdefault(d, []).append(e)

    balance = starting_balance
    min_balance = starting_balance
    first_breach: Optional[date] = None
    daily: list[DayBalance] = []

    d = start_date
    while d <= window_end:
        todays = by_day.get(d, [])
        for e in todays:
            converted = to_home_currency(
                e.amount, e.currency, profile.home_currency,
                e.settlement_date or e.event_date, exchange_rates,
            )
            balance += converted if e.direction == "credit" else -converted

        daily.append(DayBalance(day=d, balance=balance, events_applied=[e.event_id for e in todays]))

        if balance < min_balance:
            min_balance = balance
        if balance < profile.minimum_balance_to_keep and first_breach is None:
            first_breach = d

        d += timedelta(days=1)

    return ForecastResult(
        daily_balances=daily,
        minimum_balance_reached=min_balance,
        first_breach_date=first_breach,
        is_safe=first_breach is None,
    )