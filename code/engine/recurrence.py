"""
Recurrence detection for "Buy or Wait?".

IMPORTANT: linked_event_id turned out NOT to be the recurrence signal —
verified against real data, it's None on every subscription/debt_payment
event. The actual signal is simpler: a recurring bill repeats with an
identical description+category each cycle (e.g. "Video streaming plan" /
streaming, same amount, ~30 days apart). This module groups on that
instead. See build_recurrence_chains() docstring for the full story.

Future occurrences get projected into the 90-day forecast window as
status="pending" — deliberately, since they're genuinely not-yet-real
transactions. This matters most for credits (income): the simulator's
inclusion rule only counts settled/confirmed credits, so a projected
future paycheck is correctly excluded rather than assumed. Debits are
counted regardless of status (real obligations), so projected future
subscription payments DO count — which is the whole point of this
module existing.

Only event_type in {subscription, debt_payment} get projected forward.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from statistics import median
from typing import Optional

from ..ingestion.loaders import FinancialEvent

RECURRING_TYPES = {"subscription", "debt_payment"}


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def build_recurrence_chains(events: list[FinancialEvent]) -> list[list[FinancialEvent]]:
    """Groups events into ordered chains by (event_type, description,
    category) — NOT by linked_event_id.

    Verified against real data (via block2_check.py's diagnostic output):
    every subscription/debt_payment event in the sample dataset had
    linked_event_id=None. The actual recurrence signal is that a bill
    repeats with an identical description+category each cycle (e.g.
    "Video streaming plan" / streaming, same amount, ~30 days apart).
    linked_event_id may still be populated for OTHER relationships
    (e.g. linking an income event to a related expense) — just not this
    one — so this function only looks at subscription/debt_payment rows,
    where the description+category pattern is what actually recurs."""
    groups: dict[tuple[str, str, str], list[FinancialEvent]] = {}
    for e in events:
        if e.event_type not in RECURRING_TYPES:
            continue
        key = (e.event_type, e.description, e.category)
        groups.setdefault(key, []).append(e)

    return [sorted(g, key=lambda x: _parse_date(x.event_date)) for g in groups.values()]


def infer_interval_days(chain: list[FinancialEvent]) -> Optional[int]:
    """Median gap between consecutive occurrences, in days. None if the
    chain is too short to infer a pattern from (needs at least 2 events)."""
    if len(chain) < 2:
        return None
    dates = [_parse_date(e.event_date) for e in chain]
    diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    return round(median(diffs))


def project_future_occurrences(chain: list[FinancialEvent], window_end: date) -> list[FinancialEvent]:
    """Given a recurrence chain, generates synthetic future occurrences at
    the inferred interval, up to window_end. Synthetic events are marked
    status="pending" (they haven't happened yet) and get a distinct
    event_id suffix so they're traceable back to their source chain."""
    if not chain or chain[-1].event_type not in RECURRING_TYPES:
        return []

    interval = infer_interval_days(chain)
    if not interval or interval <= 0:
        return []

    last = chain[-1]
    last_date = _parse_date(last.settlement_date or last.event_date)

    projected: list[FinancialEvent] = []
    next_date = last_date + timedelta(days=interval)
    counter = 1
    while next_date <= window_end:
        synthetic = replace(
            last,
            event_id=f"{last.event_id}_projected_{counter}",
            event_date=next_date.isoformat(),
            settlement_date=next_date.isoformat(),
            status="pending",
            linked_event_id=last.event_id,
        )
        projected.append(synthetic)
        next_date += timedelta(days=interval)
        counter += 1

    return projected
