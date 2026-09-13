"""
Compares output.csv against dataset/sample_requests.csv's known-correct
answers. This is the ONE check that tells you whether your decisions are
actually right, not just correctly formatted (schema validation already
confirmed format is fine — this checks substance).

Run from repo root:
    python -m code.evaluation.compare_samples

Adapts automatically to whatever columns sample_requests.csv actually has
(prints them first) rather than assuming an exact schema, since that
wasn't independently verified before now.
"""

from __future__ import annotations

import csv
from pathlib import Path


def load_csv_by_request_id(path: Path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return {row["request_id"]: row for row in csv.DictReader(f)}


def main():
    output_path = Path("output.csv")
    sample_path = Path("dataset/sample_requests.csv")

    if not output_path.exists():
        print(f"ERROR: {output_path} not found. Run the full pipeline first.")
        return
    if not sample_path.exists():
        print(f"ERROR: {sample_path} not found — check the path.")
        return

    output_rows = load_csv_by_request_id(output_path)
    sample_rows = load_csv_by_request_id(sample_path)

    print(f"output.csv: {len(output_rows)} rows")
    print(f"sample_requests.csv: {len(sample_rows)} rows")

    sample_columns = list(next(iter(sample_rows.values())).keys())
    output_columns = list(next(iter(output_rows.values())).keys())
    print(f"\nsample_requests.csv columns: {sample_columns}")
    print(f"output.csv columns: {output_columns}\n")

    # Only compare columns that exist in both, minus request_id itself.
    compare_columns = [c for c in sample_columns if c in output_columns and c != "request_id"]
    if not compare_columns:
        print("WARNING: no matching column names found between the two files "
              "besides request_id — sample_requests.csv may use different "
              "column names (e.g. prefixed with 'expected_'). Check the "
              "column lists printed above and adjust manually.")
        return

    print(f"Comparing columns: {compare_columns}\n")
    print("=" * 100)

    match_counts = {c: 0 for c in compare_columns}
    total_compared = 0
    missing_in_output = []

    for request_id, expected in sample_rows.items():
        if request_id not in output_rows:
            missing_in_output.append(request_id)
            continue

        actual = output_rows[request_id]
        total_compared += 1
        row_has_mismatch = False
        mismatch_details = []

        for col in compare_columns:
            exp_val = (expected.get(col) or "").strip()
            act_val = (actual.get(col) or "").strip()
            if exp_val == act_val:
                match_counts[col] += 1
            else:
                row_has_mismatch = True
                mismatch_details.append(f"    {col}: expected={exp_val!r} actual={act_val!r}")

        if row_has_mismatch:
            print(f"{request_id}: MISMATCH")
            for line in mismatch_details:
                print(line)

    print("=" * 100)
    print(f"\nCompared {total_compared} sample rows against your output.csv")
    if missing_in_output:
        print(f"WARNING: {len(missing_in_output)} sample request_ids not found "
              f"in output.csv at all: {missing_in_output}")

    print("\nPer-column exact-match rate:")
    for col in compare_columns:
        rate = match_counts[col] / total_compared * 100 if total_compared else 0
        print(f"  {col}: {match_counts[col]}/{total_compared} ({rate:.0f}%)")


if __name__ == "__main__":
    main()
