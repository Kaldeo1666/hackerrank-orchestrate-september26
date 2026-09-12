"""
Block 1 smoke test: load everything, resolve blank amounts via images,
and print a normalized summary per user. This is NOT the final code/main.py
(that comes in Block 4) — it's here so you can verify Block 1 works before
Block 2 builds on top of it.

Run from repo root:
    python -m code.ingestion.block1_check dataset/
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from .loaders import load_all, get_context
from .evidence_resolver import resolve_blank_amounts
from .currency import to_home_currency, MissingExchangeRateError


def main():
    load_dotenv()

    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dataset")

    print("=== Loading all dataset files ===")
    bundle = load_all(data_dir)
    print(f"  {len(bundle.profiles)} profiles, {len(bundle.requests)} requests, "
          f"{sum(len(v) for v in bundle.events_by_user.values())} events")

    print("\n=== Resolving blank-amount events via images ===")
    records = resolve_blank_amounts(bundle, data_dir)
    resolved = sum(1 for r in records if r["amount"] is not None)
    print(f"  Resolved {resolved}/{len(records)} blank-amount events")

    unresolved = [r for r in records if r["amount"] is None]
    if unresolved:
        print(f"\n  Details on the {len(unresolved)} still unresolved:")
        for r in unresolved:
            print(f"    [{r['event_id']}] image={r['image_id']} note={r['note']!r}")

    print("\n=== Currency-normalizing one sample request's events ===")
    if bundle.requests:
        ctx = get_context(bundle, bundle.requests[0].request_id)
        home = ctx.profile.home_currency
        print(f"  Request {ctx.request.request_id} | user {ctx.request.user_id} | home currency {home}")
        conversion_failures = 0
        for event in ctx.events[:10]:  # sample first 10 to keep output short
            if event.amount is None:
                print(f"  [{event.event_id}] amount still unresolved — skipping conversion")
                continue
            try:
                converted = to_home_currency(
                    event.amount, event.currency, home, event.settlement_date, bundle.exchange_rates
                )
                print(f"  [{event.event_id}] {event.amount} {event.currency} -> {converted:.2f} {home}")
            except MissingExchangeRateError as e:
                conversion_failures += 1
                print(f"  [{event.event_id}] CONVERSION FAILED: {e}")
        if conversion_failures:
            print(f"\n  WARNING: {conversion_failures} conversions failed — "
                  f"check exchange_rates.csv coverage before Block 2.")

    print("\n=== Block 1 check complete ===")


if __name__ == "__main__":
    main()