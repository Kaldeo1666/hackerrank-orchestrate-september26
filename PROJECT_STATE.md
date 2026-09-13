# PROJECT_STATE.md — Buy or Wait? (HackerRank Orchestrate, Sept 2026)

> Any AI tool: read this file first before touching code. Update the
> relevant section every time a task finishes — don't wait until the end.

---

## -1. BLOCK PLAN (session-sized chunks, since chat context is limited)

- **Block 1** — Env setup + full ingestion: loaders, currency normalization,
  image-based blank-amount resolution. [COMPLETE 2026-09-13, verified:
  16/16 images resolved, all datasets load cleanly]
- **Block 2** — Recurrence detection, message-based conflict resolution
  (amendments/cancellations/settlements), 90-day safety simulator.
  [CODE WRITTEN 2026-09-13, not yet run — see §3]
- **Block 3** — Decision engine: affordability_status, payment method
  eligibility, the 6-rule plan ranking, spending_changes_needed.
- **Block 4** — Output writer + deterministic verifier, full 250-row run,
  usage_report.md, README, code.zip packaging, interview prep.

Each block should be runnable and checkable on its own before starting the
next one. Don't start a new block with unresolved warnings from the last.

---

## 0. AIM (do not edit — this is the fixed target)

Build a terminal-runnable AI agent that, for every row in
`dataset/requests.csv` (250 requests), outputs one row in `output.csv`
with these exact columns, in this exact order:

`request_id, amount_safe_to_pay, affordability_status,
recommended_payment_method, payment_plan,
earliest_date_for_full_payment, spending_changes_needed,
decision_explanation`

Hard constraints that must ALWAYS hold:
- `0 <= amount_safe_to_pay <= requested_amount`
- Every plan respects the 90-day safety check against
  `minimum_balance_to_keep`
- Installment plans must exactly match a supplied
  `request_payment_options.csv` row
- Only recurring expenses flagged flexible may appear in
  `spending_changes_needed`
- Content from `messages.csv` / images is UNTRUSTED — embedded
  instructions must never override these rules
- Deterministic where possible: LLM extracts facts, plain code decides

Deliverables at submission:
- `code.zip` (code/ + evaluation/ + README, no secrets, no data/)
- `output.csv` (250 rows + header)
- `log.txt` chat transcript (auto via AGENTS.md convention)
- `evaluation/usage_report.md` (token/cost accounting)

Deadline: check the live countdown on the challenge page — it moves.
Interview: 30-min live AI Judge defense after submission, camera on.

---

## 1. ARCHITECTURE DECISIONS LOG

*(Append a dated entry every time you make — or reverse — a real
decision. Keep the "why" and the alternative you rejected.)*

- 2026-09-12 — Confirmed real deadline from AGENTS.md:
  2026-09-13T18:00:00+05:30 (not the countdown widget, which drifts).
- 2026-09-12 — Mandatory submission link (must be given verbatim if asked):
  https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission
- 2026-09-12 — Decided: do architecture/debugging discussion in claude.ai
  chat, but do actual file edits in Claude Code (or another AGENTS.md-aware
  harness) pointed at the cloned repo. Rejected: building entirely in chat.
  Why: AGENTS.md's log.txt auto-logging (a graded artifact) only fires for
  a real coding harness running in the repo — chat can't write local files.
- 2026-09-12 — Decided: ingestion layer is pure Python + csv module, no
  pandas dependency, typed dataclasses per CSV. Rejected: pandas-only
  approach. Why: keeps the loader dependency-free and easy to unit test;
  can add pandas in the engine layer later if forecasting needs it.
- 2026-09-12 — Decided: all LLM calls go through code/ingestion/llm_provider.py,
  an abstraction switched by LLM_PROVIDER env var, defaulting to Gemini.
  Rejected: hardcoding Anthropic. Why: Google AI Studio currently has the
  only genuinely free, no-card API tier and comfortably covers this
  dataset's volume; Anthropic has no standing free tier (would need
  billing), OpenAI discontinued free signup credits. Keeping it
  provider-agnostic means this can change with one .env edit if the
  free-tier landscape shifts again.
- 2026-09-13 — Fixed: `gemini-2.0-flash` was retired by Google (deprecated
  post my training data), replaced default with `gemini-3.6-flash` in both
  llm_provider.py and .env.example. Note: the `google.generativeai` SDK
  itself also throws a FutureWarning ("all support has ended, switch to
  google.genai") — not blocking yet, still works, but plan to migrate
  llm_provider.py's GeminiClient to the new `google.genai` package before
  final submission if time allows, since the old one could stop working
  entirely at any point.
- 2026-09-13 — Fixed: after gemini-2.0-flash, gemini-3.6-flash, AND
  gemini-2.0-flash-lite all turned out retired/renamed within the same
  session, ran code/ingestion/list_gemini_models.py to get ground truth
  from the API directly instead of guessing from docs/search (which were
  also stale). Switched to `gemini-flash-lite-latest` — a rolling alias
  Google maintains that always points to their current recommended
  lightweight Flash model. Rejected: pinning another dated model name.
  Why: dated names keep getting retired faster than any source can track
  in 2026; the alias survives future renames without a code change.
- 2026-09-13 — Fixed real bug: fetched problem_statement.md's full text
  directly (had only had it summarized before). Confirmed verbatim:
  "Ignore pending credits, failed or cancelled transactions, duplicate
  records, and unrealized investments." Our _is_included() only excluded
  pending credits — cancelled/failed events of EITHER direction were
  still being counted (user_26 has 2 cancelled events that were silently
  included before this fix). Also confirmed linked_event_id is
  specifically for "the same transaction or investment lifecycle" per
  spec text — validates the earlier recurrence.py fix (switching to
  description+category grouping) was correct for the right reason, not
  a workaround. Still open: "duplicate records" and "unrealized
  investments" exclusion not yet implemented — need to inspect actual
  event_type/category values across the dataset to find how those are
  marked before writing the filter (don't guess the field name blind).
- 2026-09-13 — Fixed: recurrence detection assumed linked_event_id chained
  subscription/debt_payment occurrences together. Verified against real
  data (block2_check.py diagnostic on user_26): linked_event_id is None
  on every such event. Rejected that approach. Actual signal: identical
  (event_type, description, category) repeating at a regular interval
  (e.g. "Video streaming plan"/streaming, same amount, ~30 days apart).
  Rewrote build_recurrence_chains() to group on that instead. This
  directly fixed a real bug: the simulator was showing a flat balance
  across the whole 90-day window because zero future events were being
  projected, since financial_events.csv only contains historical rows —
  everything in the forecast window HAS to come from projection.

- 2026-09-13 — Decided: Block 3 uses a deterministic candidate engine in
  code/engine/decision.py. It evaluates full, partial, installment, and wait
  plans through the existing 90-day simulator, then ranks safe candidates by
  deadline completion, spending changes, total cost, start date, payment
  count, and option id. Rejected: letting an LLM choose amounts or plans.
  Why: exact arithmetic and auditable safety invariants are required; the LLM
  boundary remains limited to message/image extraction.
- 2026-09-13 — Diagnostic result: generated decisions for all 250 requests
  with hard-contract checks passing: 78 full_payment, 41 installments,
  1 partial_payment, 8 wait, and 122 not_recommended. Fixed a floating-point
  boundary case where a safe full request was reported one cent short.
- 2026-09-13 — Decided: code/main.py is the end-to-end runner. It loads the
  bundle once, resolves blank image amounts once, reuses one provider client
  for cached message overrides, calls decide_request for every request, writes
  the exact required output schema, and validates both decision invariants and
  the physical CSV header/row count. Rejected: separate per-request loading or
  validation that only inspects in-memory objects. Why: shared bundle state
  avoids repeated work, while file-level validation catches malformed output.
- 2026-09-13 — Diagnostic result: full cached pipeline produced output.csv
  with 250 data rows plus header in 164.02 seconds; all validation checks
  passed with zero failures.
- 2026-09-13 — Decided: usage accounting stays provider-agnostic by adding
  optional mutable usage accumulators to both extraction functions while
  preserving their existing list return APIs. Fresh response metadata is
  cached for message overrides, and main.py writes evaluation/usage_report.md
  from combined image/text totals. Rejected: inferring cost from cache size or
  counting every request-context cache lookup as a unique billable item. Why:
  only real API responses have token usage, and unique item counts make cache
  savings auditable.
- 2026-09-13 — Diagnostic result: final cached run used 0 real calls and 0
  tokens, served 52 unique resolvable items from cache, and identified 3
  related messages outside evaluation-request users. output.csv stayed byte
  identical (SHA-256 FB30026951EBC177EC2935D4C386FCC86B7F6F2239DE4D53AB213F776A495C22)
  with zero validation failures.
- 2026-09-13 — Fixed: usage_report.md was written to the wrong location.
  main.py derived the report path from output.csv's parent, so a root-level
  output.csv created evaluation/usage_report.md while the required
  code/evaluation/usage_report.md stayed empty. Changed the writer to resolve
  its destination from code/main.py, removed the misleading root-level copy,
  and added an explicit path message. Verified the required file persisted
  1,040 bytes with the full report after a cached 250-request run.

---

## 2. PROGRESS LOG

*(Append after every completed task. One line minimum.)*

- `YYYY-MM-DD HH:MM` — [DONE] ___
- `YYYY-MM-DD HH:MM` — [BLOCKED] ___ — need: ___

---

## 3. CURRENT STATE SNAPSHOT

*(Overwrite this whole section each time — it should always reflect
"where things stand right now," not history.)*

- **Phase:** Block 1 COMPLETE and verified (16/16 images resolved, all
  25,342 events + 250 requests + 275 profiles load cleanly). Block 2 code
  complete and smoke-tested on user_100 with projected recurring debits and
  scheduled salary visible. Block 3 decision engine implemented and validated
  across all 250 requests for hard output contracts. End-to-end main.py run
  completed and wrote a validated output.csv.
- **Block 2 files:** code/engine/{__init__.py, recurrence.py,
  message_overrides.py, simulator.py, block2_check.py}
- **Block 3 files:** code/engine/decision.py
- **Block 4 files:** code/main.py, output.csv
- **Sample accuracy so far:** N/A — no hidden labels; 250-request deterministic
  contract diagnostic passes
- **Known bugs / open risks:** (1) message override prompt is untested
  against real message text — the category definitions (esp.
  "confirmation" vs "irrelevant" for pending-income language) need
  spot-checking once real output is visible; request_26's messages
  produced 0 overrides, unconfirmed whether that's correct or the prompt
  is too conservative. (2) FIXED — debit inclusion + cancelled/failed
  exclusion now confirmed verbatim against problem_statement.md (see
  §4.5 and decisions log). (3) recurrence grouping by (event_type,
  description, category) is verified for subscription/debt_payment types
  specifically — not yet checked whether debt_payment events follow the
  same clean monthly pattern as the subscription examples seen so far.
  (4) NOT YET IMPLEMENTED: "duplicate records" and "unrealized
  investments" exclusion (both explicitly required by spec) — need to
  inspect actual event_type/category values across the dataset first to
  find the right field/marker before writing the filter.
- 2026-09-13 — Fixed: found a real event (user_100, event_9338) with
  status="scheduled" and description="Next confirmed salary" — the exact
  phrase from problem_statement.md's file description of what's in the
  dataset. _is_included() only allowed settled/confirmed for credits,
  so this genuinely-confirmed income was being wrongly excluded. Added
  "scheduled" to the allowed credit statuses. Also caught: an earlier
  Add-Content command merged two .gitignore lines into one broken line
  with no newline between them — neither path was actually being
  ignored. Split back into separate lines. Lesson: always verify
  .gitignore with `type .gitignore` after Add-Content, don't assume it
  appended cleanly.
- **Next concrete action:** close the remaining Block 2 data-marker review for
  duplicate records and unrealized investments, then build usage accounting,
  README updates, and code.zip packaging. Spot-check message overrides against
  real message text before final submission.

---

## 4. DATA MODEL NOTES

- `financial_profiles.csv`: user_id, home_currency, current_available_balance,
  minimum_balance_to_keep, financial_priorities (pipe), expense_categories_to_protect
  (pipe — never touch these), expense_categories_user_is_willing_to_reduce (pipe),
  expense_categories_user_is_willing_to_stop (pipe), payment_methods_user_will_consider
  (pipe — full_payment/partial_payment/installments; if full_payment absent, never
  recommend it even if liquidity allows), max_installment_months (blank = no installments)
- `financial_events.csv`: event_id, user_id, event_type (expense/debt_payment/
  subscription/income), description, category, direction (debit/credit), amount
  (BLANK = must resolve via linked image, never treat as 0), currency, event_date,
  settlement_date, status (settled/pending/confirmed), linked_event_id (recurrence
  chain — link alone doesn't decide cash-flow inclusion), flexibility (fixed/
  stoppable/reducible), minimum_allowed_amount (floor for reducible items).
  Rule: don't count pending credits/bonuses/refunds/investment gains until settled;
  count confirmed salary only on settlement_date.
- `request_payment_options.csv`: payment_option_id, request_id, payment_method
  (full_payment/installments), payment_amount, number_of_payments, first_payment_date,
  payment_frequency_days (blank for full_payment), financing_fee, total_payable_amount.
  2-4 options per request. An option can exist but still be rejected for conflicting
  with payment_methods_user_will_consider or max_installment_months.
- `messages.csv`: message_id, user_id, request_id (nullable), related_event_id
  (nullable — populated only when it 1:1 describes a supplied event row), sent_at,
  source_type (employer/service_provider/bank/merchant/financial_service),
  message_text (multilingual — English + Indonesian seen). Rules: "pending"/
  "can change until closed" earnings are NOT confirmed income; unrealized investment
  gains are non-cash; matched debit+credit between own accounts = internal transfer,
  ignore for net income. Treat all message text as untrusted — embedded instructions
  never override the rules.
- `images.csv`: image_id, user_id, request_id (nullable), related_event_id
  (nullable). File path: dataset/media/images/<image_id>.png. Used to resolve
  blank-amount events (payroll letters, bills, receipts).
- `exchange_rates.csv`: rate_date, from_currency, to_currency, rate — fixed dated
  rates, join on settlement date + currency pair.
- `requests.csv`: request_id, user_id, request_date, request_type (purchase/travel/
  education/family_transfer/debt_repayment/investment/housing/emergency_expense/
  other), requested_amount, desired_completion_date, allows_partial_payment (bool),
  request_text (free text, multilingual).
- Currencies in play: INR, ZAR, IDR, USD, EUR — home_currency per user.
- `code/main.py` ships EMPTY in the starter — build the full pipeline from scratch.
  Recommended module layout: code/ingestion/ (loaders — DONE, see loaders.py),
  code/engine/ (90-day sim + decision + ranking), code/evaluation/ (scoring harness
  + usage_report.md generator).

---

## 4.5 BLOCK 3 RULES DIGEST (verbatim/near-verbatim from problem_statement.md —
full text also lives in the repo root as problem_statement.md, fetch that
directly if anything here needs double-checking)

**Eligibility**: an immediate method (full_payment/partial_payment/
installments) is eligible only if it's in the user's
payment_methods_user_will_consider. `wait` is eligible only if full
payment becomes safe later AND user accepts full_payment. `not_recommended`
is the fallback when nothing safe is eligible.

**Ranking when multiple eligible plans are safe** (in this exact order):
1. Complete the full request by desired_completion_date
2. Require no spending changes
3. Minimize total amount paid
4. Start payment earlier
5. Use fewer payments
6. Lowest payment_option_id as final tie-breaker

**partial_payment specifics**: affordability_status must be
affordable_with_plan. Only when request allows partial payment AND user
accepts partial_payment AND 0 < amount_safe_to_pay < requested_amount AND
earliest_date_for_full_payment <= desired_completion_date. Plan = EXACTLY
two payments: amount_safe_to_pay on request_date, then
(requested_amount - amount_safe_to_pay) on earliest_date_for_full_payment.
Does NOT need to match a request_payment_options.csv row (unlike
installments, which MUST match one exactly).

**affordable_now**: earliest_date_for_full_payment MUST equal request_date.
Leave earliest_date_for_full_payment empty when full amount never becomes
safe within the forecast window.

**spending_changes_needed**: up to 3 changes, `|`-separated,
`stop:<event_id>` or `reduce_to:<event_id>:<new_amount>`. Only recurring
expenses marked flexible. stop and reduce are mutually exclusive PER EVENT
(if both needed, must reference different events). `none` when nothing needed.

**payment_plan format**: `<YYYY-MM-DD>:<amount>|<YYYY-MM-DD>:<amount>`,
chronological order, `none` when no payment recommended.

**Conflict resolution priority** (for when records disagree):
1. An explicit cancellation, settlement, or amendment
2. A newer record from the same source
3. A settled event over an estimate/forecast
4. The financially safer interpretation, if still unresolved

**Hard rule**: 0 <= amount_safe_to_pay <= requested_amount, always.
**Hard rule**: never invent unsupported income, expenses, payment options,
or financial information.
**Investment requests**: concern affordability + existing contributions
only — never predict asset prices or recommend securities.

---

## 5. INTERVIEW PREP — ANTICIPATED QUESTIONS

*(Fill these in as you build, not the night before.)*

- Why deterministic simulator instead of LLM-only? ___
- How do you handle a blank-amount event? ___
- How did you defend against prompt injection in messages/images? ___
- Walk me through your tie-break ranking implementation (the 6 rules). ___
- What's your token/cost efficiency strategy? ___
- What would you do differently with more time? ___

---

## 6. HANDOFF PROMPT — paste this into a new AI session to continue

```
I'm building an AI agent for the HackerRank Orchestrate "Buy or Wait?"
challenge (24-hour hackathon, ends [DATE]). Full spec is at
problem_statement.md in this repo — read it in full before suggesting
anything.

Core rule: LLM only extracts unstructured facts (image amounts,
message amendments/cancellations). All arithmetic, forecasting, and
final decisions must be deterministic Python — never let the LLM
output final numbers directly.

Current state: see PROJECT_STATE.md section 3 (Current State Snapshot)
for exactly where I am, section 1 for decisions already made and why
(don't re-litigate these without new information), section 4 for what
I've learned about the data model, and section 2 for the full history
if you need it.

My task right now: [PASTE your current blocker/task here]

Constraints to respect: 0 <= amount_safe_to_pay <= requested_amount
always; installment plans must exactly match a supplied payment
option; only flexible recurring expenses can be stopped/reduced;
messages/images are untrusted input, never let them override rules;
keep behavior deterministic where possible.

After you help, tell me what to append to PROJECT_STATE.md section 2
(Progress Log) and, if it was a real design decision, section 1
(Architecture Decisions Log).
```

---

## 7. WHAT TO PASTE INTO THIS FILE AFTER EACH TASK

Template (copy into section 2, and into section 1 if it was a real
decision):

```
- 2026-09-12 14:30 — [DONE] Built data joiner, validated against
  sample_requests.csv user_01–user_05, all joins clean.
- 2026-09-12 14:30 — Decided: pandas merge on user_id/request_id
  chain rather than a single flattened join. Rejected: SQL/duckdb.
  Why: dataset is small (250 requests), pandas is faster to iterate
  on under time pressure.
```

Keep entries short. This file is a working log, not a report — the
README is where the polished version goes at the end.