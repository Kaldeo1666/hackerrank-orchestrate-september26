"""Run the complete Buy or Wait? prediction pipeline."""

from __future__ import annotations

import csv
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .engine.decision import Decision, decide_request
from .engine.message_overrides import extract_overrides
from .ingestion.evidence_resolver import resolve_blank_amounts
from .ingestion.llm_provider import get_client
from .ingestion.loaders import DataBundle, get_context, load_all


OUTPUT_COLUMNS = [
	"request_id",
	"amount_safe_to_pay",
	"affordability_status",
	"recommended_payment_method",
	"payment_plan",
	"earliest_date_for_full_payment",
	"spending_changes_needed",
	"decision_explanation",
]
ALLOWED_STATUSES = {
	"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable",
}
ALLOWED_METHODS = {
	"full_payment", "partial_payment", "installments", "wait", "not_recommended",
}


def _parse_plan(raw: str) -> list[tuple[date, float]]:
	if raw == "none":
		return []
	payments = []
	for entry in raw.split("|"):
		day_text, amount_text = entry.split(":", 1)
		payments.append((date.fromisoformat(day_text), float(amount_text)))
	return payments


def _validation_failures(bundle: DataBundle, decisions: list[Decision]) -> list[str]:
	failures: list[str] = []
	requests_by_id = {request.request_id: request for request in bundle.requests}
	if len(decisions) != len(bundle.requests):
		failures.append(f"decision count {len(decisions)} != request count {len(bundle.requests)}")

	for decision in decisions:
		request = requests_by_id.get(decision.request_id)
		if request is None:
			failures.append(f"{decision.request_id}: missing request")
			continue
		prefix = decision.request_id
		if not 0 <= decision.amount_safe_to_pay <= request.requested_amount:
			failures.append(f"{prefix}: amount_safe_to_pay outside [0, requested_amount]")
		if decision.affordability_status not in ALLOWED_STATUSES:
			failures.append(f"{prefix}: invalid affordability_status")
		if decision.recommended_payment_method not in ALLOWED_METHODS:
			failures.append(f"{prefix}: invalid recommended_payment_method")

		try:
			payments = _parse_plan(decision.payment_plan)
		except (ValueError, IndexError):
			failures.append(f"{prefix}: malformed payment_plan")
			payments = []
		if any(payments[index][0] > payments[index + 1][0] for index in range(len(payments) - 1)):
			failures.append(f"{prefix}: payment_plan is not chronological")
		if decision.recommended_payment_method == "partial_payment":
			total = sum(amount for _, amount in payments)
			if len(payments) != 2 or abs(total - request.requested_amount) > 0.01:
				failures.append(f"{prefix}: partial payment plan does not sum to requested amount")
			if payments and abs(payments[0][1] - decision.amount_safe_to_pay) > 0.01:
				failures.append(f"{prefix}: partial payment first amount mismatches safe amount")

		if decision.recommended_payment_method == "installments":
			options = bundle.payment_options_by_request.get(request.request_id, [])
			option_plans = []
			for option in options:
				if option.payment_method != "installments":
					continue
				first = date.fromisoformat(option.first_payment_date)
				frequency = option.payment_frequency_days or 0
				option_plans.append([
					(first.fromordinal(first.toordinal() + frequency * index), option.payment_amount)
					for index in range(option.number_of_payments)
				])
			if not any(
				len(payments) == len(option_plan)
				and all(day == option_day and abs(amount - option_amount) <= 0.01
						for (day, amount), (option_day, option_amount) in zip(payments, option_plan))
				for option_plan in option_plans
			):
				failures.append(f"{prefix}: installment plan does not match a supplied option")

		if decision.spending_changes_needed != "none":
			context = get_context(bundle, request.request_id)
			events = {event.event_id: event for event in context.events}
			protected = set(context.profile.expense_categories_to_protect)
			for change in decision.spending_changes_needed.split("|"):
				parts = change.split(":")
				event = events.get(parts[1]) if len(parts) > 1 else None
				if event is None:
					failures.append(f"{prefix}: spending change references unknown event")
				elif (
					event.event_type not in {"subscription", "debt_payment"}
					or event.flexibility not in {"stoppable", "reducible", "reducible_or_stoppable"}
					or event.category in protected
				):
					failures.append(f"{prefix}: spending change references ineligible event")
	return failures


def _output_file_failures(output_path: Path, expected_rows: int) -> list[str]:
	failures: list[str] = []
	with output_path.open(newline="", encoding="utf-8") as output_file:
		reader = csv.DictReader(output_file)
		if reader.fieldnames != OUTPUT_COLUMNS:
			failures.append("output header does not match the required column order")
		rows = list(reader)
	if len(rows) != expected_rows:
		failures.append(f"output data rows {len(rows)} != expected {expected_rows}")
	return failures


def run(data_dir: Path, output_path: Path) -> tuple[list[Decision], list[str], float]:
	started = time.perf_counter()
	bundle = load_all(data_dir)
	client = get_client()
	resolve_blank_amounts(bundle, data_dir, api_client=client)

	decisions: list[Decision] = []
	for request in bundle.requests:
		context = get_context(bundle, request.request_id)
		overrides = extract_overrides(context.messages, api_client=client)
		decisions.append(decide_request(context, bundle.exchange_rates, overrides))

	with output_path.open("w", newline="", encoding="utf-8") as output_file:
		writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
		writer.writeheader()
		writer.writerows(decision.__dict__ for decision in decisions)

	failures = _validation_failures(bundle, decisions)
	failures.extend(_output_file_failures(output_path, len(bundle.requests)))
	elapsed = time.perf_counter() - started
	return decisions, failures, elapsed


def main() -> None:
	load_dotenv()
	data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dataset")
	output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output.csv")
	decisions, failures, elapsed = run(data_dir, output_path)
	print(f"Processed {len(decisions)} requests in {elapsed:.2f} seconds.")
	print(f"Wrote {output_path}.")
	if failures:
		print(f"Validation failures ({len(failures)}):")
		for failure in failures:
			print(f"- {failure}")
		raise SystemExit(1)
	print("Validation failures: 0")


if __name__ == "__main__":
	main()
