"""Deterministic Block 3 decision engine.

The LLM boundary ends before this module: callers may pass already extracted
message overrides, but all affordability, schedule, and ranking decisions are
plain Python over the typed ingestion records and simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from itertools import combinations
from math import ceil, floor
from typing import Iterable, Optional

from ..ingestion.loaders import (
    ExchangeRate,
    FinancialEvent,
    PaymentOption,
    RequestContext,
)
from .message_overrides import MessageOverride
from .simulator import simulate_90_day_forecast


@dataclass(frozen=True)
class SpendingChange:
    event_id: str
    action: str  # stop | reduce
    amount: Optional[float] = None

    def output(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{_money(self.amount or 0)}"


@dataclass(frozen=True)
class Payment:
    day: date
    amount: float


@dataclass
class Decision:
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


@dataclass(frozen=True)
class _Candidate:
    method: str
    status: str
    payments: tuple[Payment, ...]
    changes: tuple[SpendingChange, ...]
    total_paid: float
    option_id: str = ""

    @property
    def completes_by_deadline(self) -> bool:
        return bool(self.payments)

    @property
    def starts(self) -> date:
        return self.payments[0].day


def _parse_day(raw: str) -> date:
    return date.fromisoformat(raw)


def _money(value: float) -> str:
    rounded = round(value + 1e-9, 2)
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _payments_text(payments: Iterable[Payment]) -> str:
    return "|".join(f"{p.day.isoformat()}:{_money(p.amount)}" for p in payments)


def _event_key(event: FinancialEvent) -> tuple[str, str, str]:
    return event.event_type, event.description, event.category


def _eligible_changes(ctx: RequestContext) -> list[SpendingChange]:
    """Return one valid action per recurring event chain representative.

    A change targets the event id shown to the evaluator, while simulator
    inputs are transformed by the complete recurrence key so future projected
    occurrences receive the same change.
    """
    profile = ctx.profile
    protected = set(profile.expense_categories_to_protect)
    reduce_categories = set(profile.expense_categories_user_is_willing_to_reduce)
    stop_categories = set(profile.expense_categories_user_is_willing_to_stop)
    seen: set[tuple[str, str, str]] = set()
    changes: list[SpendingChange] = []

    for event in sorted(ctx.events, key=lambda item: item.event_id):
        if event.direction != "debit" or event.event_type not in {"subscription", "debt_payment"}:
            continue
        if event.category in protected or event.amount is None:
            continue
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)

        if event.flexibility in {"stoppable", "reducible_or_stoppable"} and event.category in stop_categories:
            changes.append(SpendingChange(event.event_id, "stop"))
        if event.flexibility in {"reducible", "reducible_or_stoppable"} and event.category in reduce_categories:
            floor_amount = event.minimum_allowed_amount
            if floor_amount is not None and floor_amount < event.amount:
                changes.append(SpendingChange(event.event_id, "reduce", floor_amount))
    return changes


def _changed_events(events: list[FinancialEvent], changes: tuple[SpendingChange, ...]) -> list[FinancialEvent]:
    by_id = {event.event_id: event for event in events}
    target_keys = { _event_key(by_id[c.event_id]): c for c in changes if c.event_id in by_id }
    changed: list[FinancialEvent] = []
    for event in events:
        change = target_keys.get(_event_key(event))
        if change is None:
            changed.append(event)
        elif change.action == "reduce":
            changed.append(replace(event, amount=change.amount))
        # A stopped recurrence is omitted so the simulator cannot project it.
    return changed


def _safe(
    ctx: RequestContext,
    exchange_rates: list[ExchangeRate],
    payments: tuple[Payment, ...],
    changes: tuple[SpendingChange, ...],
    overrides: list[MessageOverride],
) -> bool:
    request_day = _parse_day(ctx.request.request_date)
    payment_events = [
        FinancialEvent(
            event_id=f"request_payment_{index}",
            user_id=ctx.request.user_id,
            event_type="expense",
            description="Recommended request payment",
            category="request",
            direction="debit",
            amount=payment.amount,
            currency=ctx.profile.home_currency,
            event_date=payment.day.isoformat(),
            settlement_date=payment.day.isoformat(),
            status="settled",
            linked_event_id=None,
            flexibility="fixed",
            minimum_allowed_amount=None,
        )
        for index, payment in enumerate(payments)
    ]
    events = _changed_events(ctx.events, changes) + payment_events
    result = simulate_90_day_forecast(
        start_date=request_day,
        starting_balance=ctx.profile.current_available_balance,
        profile=ctx.profile,
        events=events,
        exchange_rates=exchange_rates,
        overrides=overrides,
    )
    return result.is_safe


def _amount_safe_today(
    ctx: RequestContext,
    exchange_rates: list[ExchangeRate],
    changes: tuple[SpendingChange, ...],
    overrides: list[MessageOverride],
) -> float:
    requested = ctx.request.requested_amount
    request_day = _parse_day(ctx.request.request_date)
    if _safe(ctx, exchange_rates, (Payment(request_day, requested),), changes, overrides):
        return requested
    low, high = 0.0, requested
    for _ in range(48):
        midpoint = (low + high) / 2
        if _safe(ctx, exchange_rates, (Payment(request_day, midpoint),), changes, overrides):
            low = midpoint
        else:
            high = midpoint
    cents = floor(low * 100 + 1e-7) / 100
    while cents > 0 and not _safe(
        ctx, exchange_rates, (Payment(request_day, cents),), changes, overrides
    ):
        cents = round(cents - 0.01, 2)
    return min(requested, max(0.0, cents))


def _first_safe_full_date(
    ctx: RequestContext,
    exchange_rates: list[ExchangeRate],
    amount: float,
    changes: tuple[SpendingChange, ...],
    overrides: list[MessageOverride],
) -> Optional[date]:
    start = _parse_day(ctx.request.request_date)
    deadline = min(_parse_day(ctx.request.desired_completion_date), start + timedelta(days=90))
    day = start
    while day <= deadline:
        if _safe(ctx, exchange_rates, (Payment(day, amount),), changes, overrides):
            return day
        day += timedelta(days=1)
    return None


def _option_payments(option: PaymentOption) -> tuple[Payment, ...]:
    first = _parse_day(option.first_payment_date)
    frequency = option.payment_frequency_days or 0
    return tuple(Payment(first + timedelta(days=frequency * index), option.payment_amount)
                 for index in range(option.number_of_payments))


def _within_user_installment_limit(ctx: RequestContext, option: PaymentOption) -> bool:
    maximum = ctx.profile.max_installment_months
    if maximum is None:
        return False
    first = _parse_day(option.first_payment_date)
    last = first + timedelta(days=(option.payment_frequency_days or 0) * (option.number_of_payments - 1))
    return last <= first + timedelta(days=ceil(maximum * 31))


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, int, float, date, int, str]:
    return (
        0 if candidate.completes_by_deadline else 1,
        len(candidate.changes),
        candidate.total_paid,
        candidate.starts,
        len(candidate.payments),
        candidate.option_id,
    )


def _change_sets(changes: list[SpendingChange]) -> Iterable[tuple[SpendingChange, ...]]:
    yield ()
    for size in range(1, min(3, len(changes)) + 1):
        yield from combinations(changes, size)


def decide_request(
    ctx: RequestContext,
    exchange_rates: list[ExchangeRate],
    overrides: list[MessageOverride] | None = None,
) -> Decision:
    """Choose the safest eligible plan for one request deterministically."""
    overrides = overrides or []
    request_day = _parse_day(ctx.request.request_date)
    desired_day = _parse_day(ctx.request.desired_completion_date)
    requested = ctx.request.requested_amount
    methods = set(ctx.profile.payment_methods_user_will_consider)
    changes = _eligible_changes(ctx)

    baseline_safe_today = _amount_safe_today(ctx, exchange_rates, (), overrides)
    earliest = _first_safe_full_date(ctx, exchange_rates, requested, (), overrides)
    candidates: list[_Candidate] = []

    if "full_payment" in methods and earliest == request_day:
        candidates.append(_Candidate("full_payment", "affordable_now", (Payment(request_day, requested),), (), requested))

    if "partial_payment" in methods and ctx.request.allows_partial_payment and 0 < baseline_safe_today < requested:
        for change_set in _change_sets(changes):
            safe_today = _amount_safe_today(ctx, exchange_rates, change_set, overrides)
            if not 0 < safe_today < requested:
                continue
            completion = _first_safe_full_date(ctx, exchange_rates, requested - safe_today, change_set, overrides)
            if completion is None or completion > desired_day:
                continue
            payments = (Payment(request_day, safe_today), Payment(completion, requested - safe_today))
            if _safe(ctx, exchange_rates, payments, change_set, overrides):
                candidates.append(_Candidate("partial_payment", "affordable_with_plan", payments, change_set, requested))

    if "installments" in methods:
        for option in ctx.payment_options:
            if option.payment_method != "installments" or not _within_user_installment_limit(ctx, option):
                continue
            payments = _option_payments(option)
            if not payments or payments[0].day < request_day or payments[-1].day > desired_day:
                continue
            for change_set in _change_sets(changes):
                if _safe(ctx, exchange_rates, payments, change_set, overrides):
                    candidates.append(_Candidate("installments", "affordable_with_plan", payments, change_set,
                                                option.total_payable_amount, option.payment_option_id))
                    break

    if candidates:
        chosen = min(candidates, key=_candidate_sort_key)
        return Decision(
            request_id=ctx.request.request_id,
            amount_safe_to_pay=baseline_safe_today,
            affordability_status=chosen.status,
            recommended_payment_method=chosen.method,
            payment_plan=_payments_text(chosen.payments),
            earliest_date_for_full_payment=earliest.isoformat() if earliest else "",
            spending_changes_needed="|".join(c.output() for c in chosen.changes) or "none",
            decision_explanation=(f"Safe amount today is {_money(baseline_safe_today)}; selected {chosen.method} "
                                  f"because its payments stay above the minimum balance."),
        )

    if earliest is not None and earliest > request_day and "full_payment" in methods:
        return Decision(
            request_id=ctx.request.request_id,
            amount_safe_to_pay=baseline_safe_today,
            affordability_status="affordable_later",
            recommended_payment_method="wait",
            payment_plan="none",
            earliest_date_for_full_payment=earliest.isoformat(),
            spending_changes_needed="none",
            decision_explanation=f"The full amount first passes the 90-day safety check on {earliest.isoformat()}; wait until then.",
        )

    return Decision(
        request_id=ctx.request.request_id,
        amount_safe_to_pay=baseline_safe_today,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan="none",
        earliest_date_for_full_payment=earliest.isoformat() if earliest else "",
        spending_changes_needed="none",
        decision_explanation="No eligible payment plan completes by the desired date while maintaining the minimum balance.",
    )
