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
PRICING = {
	"gemini": (0.10, 0.40, "https://ai.google.dev/gemini-api/docs/pricing"),
	"anthropic": (1.00, 5.00, "https://www.anthropic.com/pricing#api"),
	"openai": (0.15, 0.60, "https://openai.com/api/pricing/"),
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


def _write_usage_report(
	output_path: Path,
	request_count: int,
	client,
	evidence_usage: dict,
	message_usage: dict,
	bundle: DataBundle,
	resolvable_images: set[str],
) -> None:
	input_tokens = evidence_usage["input_tokens"] + message_usage["input_tokens"]
	output_tokens = evidence_usage["output_tokens"] + message_usage["output_tokens"]
	calls = evidence_usage["calls"] + message_usage["calls"]
	total_tokens = input_tokens + output_tokens
	input_rate, output_rate, pricing_url = PRICING.get(client.provider, (0.0, 0.0, "pricing unavailable"))
	cost = input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate
	resolvable_messages = {
		message.message_id
		for messages in bundle.messages_by_user.values()
		for message in messages
		if message.related_event_id
	}
	resolvable_items = len(resolvable_images) + len(resolvable_messages)
	fresh_item_ids = evidence_usage["fresh_item_ids"] | message_usage["fresh_item_ids"]
	cached_item_ids = evidence_usage["cached_item_ids"] | message_usage["cached_item_ids"]
	unprocessed_items = resolvable_items - len(fresh_item_ids) - len(cached_item_ids)
	report_path = Path(__file__).resolve().parent / "evaluation" / "usage_report.md"
	report_path.parent.mkdir(parents=True, exist_ok=True)
	report_path.write_text(
		f"""# LLM Usage Report

Final full-dataset run for `{request_count}` requests.

## Provider and pricing

- Provider: `{client.provider}`
- Model: `{client.model}`
- Input rate: `${input_rate:.2f}` per million tokens
- Output rate: `${output_rate:.2f}` per million tokens
- Pricing source: {pricing_url}

## Usage totals

| Metric | Total | Average per request |
|---|---:|---:|
| Real API calls | {calls} | {calls / request_count:.2f} |
| Input tokens | {input_tokens:,} | {input_tokens / request_count:,.2f} |
| Output tokens | {output_tokens:,} | {output_tokens / request_count:,.2f} |
| Total tokens | {total_tokens:,} | {total_tokens / request_count:,.2f} |
| Estimated cost | ${cost:.6f} | ${cost / request_count:.6f} |

## Cache context

- Resolvable items discovered: {resolvable_items} total ({len(resolvable_images)} images + {len(resolvable_messages)} messages)
- Fresh API calls/items: {calls} calls for {len(fresh_item_ids)} unique items
- Unique items served from cache: {len(cached_item_ids)}
- Cache retrievals during request processing: {evidence_usage['cached_items'] + message_usage['cached_items']}
- Resolvable items not reached by an evaluation request: {unprocessed_items}
- Image resolver calls: {evidence_usage['calls']}
- Message override calls: {message_usage['calls']}

Only real, non-cache API calls are included in token and cost totals. The
provider/model abstraction supplied token counts from each response.
""",
		encoding="utf-8",
	)


def run(data_dir: Path, output_path: Path) -> tuple[list[Decision], list[str], float]:
	started = time.perf_counter()
	bundle = load_all(data_dir)
	client = get_client()
	resolvable_images = {
		image.image_id
		for images in bundle.images_by_user.values()
		for image in images
		if image.related_event_id
		and any(event.event_id == image.related_event_id and event.amount is None
				for event in bundle.events_by_user.get(image.user_id, []))
	}
	evidence_usage = {}
	message_usage = {}
	resolve_blank_amounts(bundle, data_dir, api_client=client, usage=evidence_usage)

	decisions: list[Decision] = []
	for request in bundle.requests:
		context = get_context(bundle, request.request_id)
		overrides = extract_overrides(context.messages, api_client=client, usage=message_usage)
		decisions.append(decide_request(context, bundle.exchange_rates, overrides))

	with output_path.open("w", newline="", encoding="utf-8") as output_file:
		writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
		writer.writeheader()
		writer.writerows(decision.__dict__ for decision in decisions)

	failures = _validation_failures(bundle, decisions)
	failures.extend(_output_file_failures(output_path, len(bundle.requests)))
	_write_usage_report(
		output_path, len(bundle.requests), client, evidence_usage, message_usage,
		bundle, resolvable_images,
	)
	print(f"Wrote usage report {Path(__file__).resolve().parent / 'evaluation' / 'usage_report.md'}.")
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
