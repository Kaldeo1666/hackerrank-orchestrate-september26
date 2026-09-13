# LLM Usage Report

Final full-dataset run for `250` requests.

## Provider and pricing

- Provider: `gemini`
- Model: `gemini-flash-lite-latest`
- Input rate: `$0.10` per million tokens
- Output rate: `$0.40` per million tokens
- Pricing source: https://ai.google.dev/gemini-api/docs/pricing

## Usage totals

| Metric | Total | Average per request |
|---|---:|---:|
| Real API calls | 0 | 0.00 |
| Input tokens | 0 | 0.00 |
| Output tokens | 0 | 0.00 |
| Total tokens | 0 | 0.00 |
| Estimated cost | $0.000000 | $0.000000 |

## Cache context

- Resolvable items discovered: 55 total (16 images + 39 messages)
- Fresh API calls/items: 0 calls for 0 unique items
- Unique items served from cache: 52
- Cache retrievals during request processing: 52
- Resolvable items not reached by an evaluation request: 3
- Image resolver calls: 0
- Message override calls: 0

Only real, non-cache API calls are included in token and cost totals. The
provider/model abstraction supplied token counts from each response.
Known limitation: "duplicate records" and "unrealized investments" exclusion (mentioned in the 90-Day Safety Check spec) is not yet implemented in this submission.