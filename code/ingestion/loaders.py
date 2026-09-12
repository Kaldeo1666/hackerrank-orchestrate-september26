"""
Ingestion layer for "Buy or Wait?" (HackerRank Orchestrate, Sept 2026).

Loads every file in dataset/ into typed, joinable structures. This module
does NOT make decisions — it only loads, types, and bundles data so the
engine layer (code/engine/) can consume it without touching raw CSVs.

Design principle (per PROJECT_STATE.md §1): this layer is 100% deterministic.
No LLM calls happen here except image amount extraction, which is isolated
in extract_amount_from_image() so it's easy to swap/mock/cache.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Typed records — one dataclass per CSV, field names match columns exactly
# ---------------------------------------------------------------------------

@dataclass
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: list[str]                      # pipe-delimited
    expense_categories_to_protect: list[str]              # pipe-delimited
    expense_categories_user_is_willing_to_reduce: list[str]
    expense_categories_user_is_willing_to_stop: list[str]
    payment_methods_user_will_consider: list[str]
    max_installment_months: Optional[int]


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str            # expense | debt_payment | subscription | income
    description: str
    category: str
    direction: str              # debit | credit
    amount: Optional[float]     # None when blank -> must resolve via image
    currency: str
    event_date: str             # YYYY-MM-DD
    settlement_date: str        # YYYY-MM-DD
    status: str                 # settled | pending | confirmed
    linked_event_id: Optional[str]
    flexibility: str            # fixed | stoppable | reducible
    minimum_allowed_amount: Optional[float]


@dataclass
class ExchangeRate:
    rate_date: str
    from_currency: str
    to_currency: str
    rate: float


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str          # full_payment | installments
    payment_amount: float
    number_of_payments: int
    first_payment_date: str
    payment_frequency_days: Optional[int]
    financing_fee: float
    total_payable_amount: float


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: str
    source_type: str    # employer | service_provider | bank | merchant | financial_service
    message_text: str


@dataclass
class ImageEvidence:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]

    @property
    def path(self) -> Path:
        return Path("dataset/media/images") / f"{self.image_id}.png"


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: str
    request_type: str
    requested_amount: float
    desired_completion_date: str
    allows_partial_payment: bool
    request_text: str


# ---------------------------------------------------------------------------
# Bundle — everything loaded, plus fast per-user / per-request lookups
# ---------------------------------------------------------------------------

@dataclass
class DataBundle:
    profiles: dict[str, FinancialProfile] = field(default_factory=dict)
    events_by_user: dict[str, list[FinancialEvent]] = field(default_factory=dict)
    exchange_rates: list[ExchangeRate] = field(default_factory=list)
    payment_options_by_request: dict[str, list[PaymentOption]] = field(default_factory=dict)
    messages_by_user: dict[str, list[Message]] = field(default_factory=dict)
    images_by_user: dict[str, list[ImageEvidence]] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipe_list(raw: str) -> list[str]:
    return [x.strip() for x in raw.split("|") if x.strip()] if raw else []


def _opt_float(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    return float(raw) if raw else None


def _opt_int(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def _opt_str(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    return raw if raw else None


def _bool(raw: str) -> bool:
    return str(raw).strip().lower() in ("true", "1", "yes")


def _read_rows(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


# ---------------------------------------------------------------------------
# Per-file loaders
# ---------------------------------------------------------------------------

def load_profiles(data_dir: Path) -> dict[str, FinancialProfile]:
    out = {}
    for row in _read_rows(data_dir / "financial_profiles.csv"):
        p = FinancialProfile(
            user_id=row["user_id"],
            home_currency=row["home_currency"],
            current_available_balance=float(row["current_available_balance"]),
            minimum_balance_to_keep=float(row["minimum_balance_to_keep"]),
            financial_priorities=_pipe_list(row["financial_priorities"]),
            expense_categories_to_protect=_pipe_list(row["expense_categories_to_protect"]),
            expense_categories_user_is_willing_to_reduce=_pipe_list(
                row["expense_categories_user_is_willing_to_reduce"]
            ),
            expense_categories_user_is_willing_to_stop=_pipe_list(
                row["expense_categories_user_is_willing_to_stop"]
            ),
            payment_methods_user_will_consider=_pipe_list(
                row["payment_methods_user_will_consider"]
            ),
            max_installment_months=_opt_int(row.get("max_installment_months", "")),
        )
        out[p.user_id] = p
    return out


def load_events(data_dir: Path) -> dict[str, list[FinancialEvent]]:
    out: dict[str, list[FinancialEvent]] = {}
    for row in _read_rows(data_dir / "financial_events.csv"):
        e = FinancialEvent(
            event_id=row["event_id"],
            user_id=row["user_id"],
            event_type=row["event_type"],
            description=row["description"],
            category=row["category"],
            direction=row["direction"],
            amount=_opt_float(row["amount"]),   # None => needs image resolution
            currency=row["currency"],
            event_date=row["event_date"],
            settlement_date=row["settlement_date"],
            status=row["status"],
            linked_event_id=_opt_str(row.get("linked_event_id", "")),
            flexibility=row["flexibility"],
            minimum_allowed_amount=_opt_float(row.get("minimum_allowed_amount", "")),
        )
        out.setdefault(e.user_id, []).append(e)
    return out


def load_exchange_rates(data_dir: Path) -> list[ExchangeRate]:
    out = []
    for row in _read_rows(data_dir / "exchange_rates.csv"):
        out.append(ExchangeRate(
            rate_date=row["rate_date"],
            from_currency=row["from_currency"],
            to_currency=row["to_currency"],
            rate=float(row["rate"]),
        ))
    return out


def load_payment_options(data_dir: Path) -> dict[str, list[PaymentOption]]:
    out: dict[str, list[PaymentOption]] = {}
    for row in _read_rows(data_dir / "request_payment_options.csv"):
        opt = PaymentOption(
            payment_option_id=row["payment_option_id"],
            request_id=row["request_id"],
            payment_method=row["payment_method"],
            payment_amount=float(row["payment_amount"]),
            number_of_payments=int(row["number_of_payments"]),
            first_payment_date=row["first_payment_date"],
            payment_frequency_days=_opt_int(row.get("payment_frequency_days", "")),
            financing_fee=float(row["financing_fee"]),
            total_payable_amount=float(row["total_payable_amount"]),
        )
        out.setdefault(opt.request_id, []).append(opt)
    return out


def load_messages(data_dir: Path) -> dict[str, list[Message]]:
    out: dict[str, list[Message]] = {}
    for row in _read_rows(data_dir / "messages.csv"):
        m = Message(
            message_id=row["message_id"],
            user_id=row["user_id"],
            request_id=_opt_str(row.get("request_id", "")),
            related_event_id=_opt_str(row.get("related_event_id", "")),
            sent_at=row["sent_at"],
            source_type=row["source_type"],
            message_text=row["message_text"],
        )
        out.setdefault(m.user_id, []).append(m)
    return out


def load_images(data_dir: Path) -> dict[str, list[ImageEvidence]]:
    out: dict[str, list[ImageEvidence]] = {}
    for row in _read_rows(data_dir / "images.csv"):
        img = ImageEvidence(
            image_id=row["image_id"],
            user_id=row["user_id"],
            request_id=_opt_str(row.get("request_id", "")),
            related_event_id=_opt_str(row.get("related_event_id", "")),
        )
        out.setdefault(img.user_id, []).append(img)
    return out


def load_requests(data_dir: Path) -> list[Request]:
    out = []
    for row in _read_rows(data_dir / "requests.csv"):
        out.append(Request(
            request_id=row["request_id"],
            user_id=row["user_id"],
            request_date=row["request_date"],
            request_type=row["request_type"],
            requested_amount=float(row["requested_amount"]),
            desired_completion_date=row["desired_completion_date"],
            allows_partial_payment=_bool(row["allows_partial_payment"]),
            request_text=row["request_text"],
        ))
    return out


def load_all(data_dir: str | Path) -> DataBundle:
    data_dir = Path(data_dir)
    return DataBundle(
        profiles=load_profiles(data_dir),
        events_by_user=load_events(data_dir),
        exchange_rates=load_exchange_rates(data_dir),
        payment_options_by_request=load_payment_options(data_dir),
        messages_by_user=load_messages(data_dir),
        images_by_user=load_images(data_dir),
        requests=load_requests(data_dir),
    )


# ---------------------------------------------------------------------------
# Per-request context bundle — everything the engine needs for one decision
# ---------------------------------------------------------------------------

@dataclass
class RequestContext:
    request: Request
    profile: FinancialProfile
    events: list[FinancialEvent]                # all events for this user
    payment_options: list[PaymentOption]         # options for this request
    messages: list[Message]                      # all messages for this user
    images: list[ImageEvidence]                  # all images for this user
    events_needing_image_resolution: list[FinancialEvent]  # amount is None


def get_context(bundle: DataBundle, request_id: str) -> RequestContext:
    request = next(r for r in bundle.requests if r.request_id == request_id)
    profile = bundle.profiles[request.user_id]
    events = bundle.events_by_user.get(request.user_id, [])
    return RequestContext(
        request=request,
        profile=profile,
        events=events,
        payment_options=bundle.payment_options_by_request.get(request_id, []),
        messages=bundle.messages_by_user.get(request.user_id, []),
        images=bundle.images_by_user.get(request.user_id, []),
        events_needing_image_resolution=[e for e in events if e.amount is None],
    )


# ---------------------------------------------------------------------------
# Smoke test — run this file directly to sanity-check the dataset loads
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dataset")
    bundle = load_all(data_dir)

    print(f"Profiles:        {len(bundle.profiles)}")
    print(f"Users w/ events: {len(bundle.events_by_user)}")
    print(f"Exchange rates:  {len(bundle.exchange_rates)}")
    print(f"Requests:        {len(bundle.requests)}")
    print(f"Payment options: {sum(len(v) for v in bundle.payment_options_by_request.values())}")
    print(f"Messages:        {sum(len(v) for v in bundle.messages_by_user.values())}")
    print(f"Images:          {sum(len(v) for v in bundle.images_by_user.values())}")

    blank_amount_events = sum(
        1 for evs in bundle.events_by_user.values() for e in evs if e.amount is None
    )
    print(f"Events with blank amount (need image resolution): {blank_amount_events}")

    if bundle.requests:
        ctx = get_context(bundle, bundle.requests[0].request_id)
        print(f"\nSample context for {ctx.request.request_id}:")
        print(f"  user: {ctx.request.user_id} | home_currency: {ctx.profile.home_currency}")
        print(f"  events: {len(ctx.events)} | payment_options: {len(ctx.payment_options)}")
        print(f"  messages: {len(ctx.messages)} | images: {len(ctx.images)}")
        print(f"  needs image resolution: {len(ctx.events_needing_image_resolution)}")
