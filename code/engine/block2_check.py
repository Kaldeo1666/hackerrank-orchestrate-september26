"""
Block 2 smoke test: for one sample request, extract message overrides,
project recurring events, run the 90-day simulator, and print a summary.

Run from repo root:
    python -m code.engine.block2_check dataset/
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from ..ingestion.loaders import load_all, get_context
from ..ingestion.evidence_resolver import resolve_blank_amounts
from .message_overrides import extract_overrides
from .simulator import simulate_90_day_forecast


def main():
    load_dotenv()
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dataset")

    print("=== Loading + resolving (Block 1 pipeline) ===")
    bundle = load_all(data_dir)
    resolve_blank_amounts(bundle, data_dir)  # cached from Block 1, should be instant

    if not bundle.requests:
        print("No requests found.")
        return

    # Change the index below to check a different request.
    # Pass a request_id as the 2nd arg to check a specific request;
    # otherwise defaults to the first one in the dataset.
    request_id = sys.argv[2] if len(sys.argv) > 2 else bundle.requests[0].request_id
    ctx = get_context(bundle, request_id)
    print(f"\n=== Block 2 check for {ctx.request.request_id} (user {ctx.request.user_id}) ===")

    # --- Diagnostic: is the flat balance real, or are events being filtered out? ---
    print(f"\n  DIAGNOSTIC: user has {len(ctx.events)} total events on record.")
    if ctx.events:
        event_dates = sorted(e.event_date for e in ctx.events)
        print(f"  Event date range: {event_dates[0]} to {event_dates[-1]}")
        print(f"  Request date: {ctx.request.request_date}")
        directions = {}
        statuses = {}
        for e in ctx.events:
            directions[e.direction] = directions.get(e.direction, 0) + 1
            statuses[e.status] = statuses.get(e.status, 0) + 1
        print(f"  By direction: {directions}")
        print(f"  By status: {statuses}")
        print("  First 5 events (id, type, direction, status, amount, event_date):")
        for e in ctx.events[:5]:
            print(f"    {e.event_id} | {e.event_type} | {e.direction} | {e.status} | {e.amount} | {e.event_date}")

        print("\n  All subscription/debt_payment events (candidates for recurrence):")
        recurring_candidates = [e for e in ctx.events if e.event_type in ("subscription", "debt_payment")]
        for e in sorted(recurring_candidates, key=lambda x: x.event_date):
            print(f"    {e.event_id} | {e.description!r} | {e.category} | {e.event_date} | "
                  f"amount={e.amount} | linked_event_id={e.linked_event_id!r}")
    # --- end diagnostic ---

    print("Extracting message-based overrides...")
    overrides = extract_overrides(ctx.messages)
    n_cancel = sum(1 for o in overrides if o.category == "cancellation")
    n_amend = sum(1 for o in overrides if o.category == "amendment")
    n_confirm = sum(1 for o in overrides if o.category == "confirmation")
    print(f"  {len(overrides)} actionable overrides "
          f"({n_cancel} cancellations, {n_amend} amendments, {n_confirm} confirmations)")
    for o in overrides:
        print(f"    [{o.event_id}] {o.category} — {o.note}")

    start = date.fromisoformat(ctx.request.request_date)
    print(f"\nRunning 90-day simulation from {start}...")
    result = simulate_90_day_forecast(
        start_date=start,
        starting_balance=ctx.profile.current_available_balance,
        profile=ctx.profile,
        events=ctx.events,
        exchange_rates=bundle.exchange_rates,
        overrides=overrides,
    )

    print(f"\n  Starting balance:        {ctx.profile.current_available_balance:.2f} {ctx.profile.home_currency}")
    print(f"  Minimum balance to keep: {ctx.profile.minimum_balance_to_keep:.2f} {ctx.profile.home_currency}")
    print(f"  Minimum balance reached: {result.minimum_balance_reached:.2f} {ctx.profile.home_currency}")
    print(f"  Safe across full 90 days: {result.is_safe}")
    if result.first_breach_date:
        print(f"  First breach date: {result.first_breach_date}")

    print("\n  First 10 days of balance:")
    for db in result.daily_balances[:10]:
        marker = f"  <- {db.events_applied}" if db.events_applied else ""
        print(f"    {db.day}: {db.balance:.2f}{marker}")


if __name__ == "__main__":
    main()
