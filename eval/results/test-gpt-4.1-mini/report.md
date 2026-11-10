# Eval: `test` split, 150 documents, `gpt-4.1-mini`

Run 2026-11-09T22:35:26+00:00 -> 2026-11-09T22:37:20+00:00 (UTC). Prices: $0.4/1M input, $1.6/1M output tokens.

## Headline

| System | Routing accuracy | Anomaly recall | False-review rate | Docs fully correct | LLM calls/doc | Cost/doc | Mean latency |
|---|---|---|---|---|---|---|---|
| Baseline (1 LLM call) | 67.3% [59.5, 74.3] | 43.3% [31.6, 55.9] | 16.7% [10.4, 25.7] | 95.3% | 1.00 | $0.0025 | 2.8s |
| Agent, no repair loop | 100.0% [97.5, 100.0] | 100.0% [94.0, 100.0] | 0.0% [0.0, 4.1] | 100.0% | 1.00 | $0.0011 | n/a |
| Agent (full graph) | 100.0% [97.5, 100.0] | 100.0% [94.0, 100.0] | 0.0% [0.0, 4.1] | 99.3% | 1.21 | $0.0013 | 3.0s |

Brackets are 95% Wilson intervals. Run errors: Baseline (1 LLM call): 0, Agent, no repair loop: 0, Agent (full graph): 0.

## Paired tests (exact McNemar)

| Comparison | Only first correct | Only second correct | p-value |
|---|---|---|---|
| `agent_vs_baseline_routing` | 49 | 0 | 0.0000 |
| `agent_vs_baseline_doc_exact` | 7 | 1 | 0.0703 |
| `agent_vs_agent_no_repair_routing` | 0 | 0 | 1.0000 |
| `agent_vs_agent_no_repair_doc_exact` | 0 | 1 | 1.0000 |

## Field-level extraction accuracy

| Field | Baseline (1 LLM call) | Agent, no repair loop | Agent (full graph) |
|---|---|---|---|
| document_type | 100.0% | 100.0% | 100.0% |
| invoice_number | 100.0% | 100.0% | 100.0% |
| invoice_date | 99.3% | 100.0% | 100.0% |
| due_date | 100.0% | 100.0% | 99.3% |
| currency | 100.0% | 100.0% | 100.0% |
| seller_name | 100.0% | 100.0% | 100.0% |
| seller_vat_id | 100.0% | 100.0% | 100.0% |
| buyer_name | 100.0% | 100.0% | 100.0% |
| po_number | 100.0% | 100.0% | 100.0% |
| iban | 96.0% | 100.0% | 100.0% |
| subtotal | 100.0% | 100.0% | 100.0% |
| tax_total | 100.0% | 100.0% | 100.0% |
| total | 100.0% | 100.0% | 100.0% |
| line_items | 100.0% | 100.0% | 100.0% |
| tax_lines | 100.0% | 100.0% | 100.0% |

## Anomalies caught with the right reason

Routed to review AND the matching code was flagged.

| Anomaly | Baseline (1 LLM call) | Agent, no repair loop | Agent (full graph) |
|---|---|---|---|
| line_sum_mismatch | 1/8 | 8/8 | 8/8 |
| vat_miscalculated | 3/8 | 8/8 | 8/8 |
| total_mismatch | 2/8 | 8/8 | 8/8 |
| due_before_issue | 8/8 | 8/8 | 7/8 |
| invalid_iban | 0/7 | 7/7 | 7/7 |
| unknown_vendor | 0/7 | 7/7 | 7/7 |
| bank_account_changed | 0/7 | 7/7 | 7/7 |
| duplicate_invoice | 3/7 | 7/7 | 7/7 |

## Confusion (human_review = positive)

| System | TP | FN | FP | TN | Tokens in | Tokens out | Total cost |
|---|---|---|---|---|---|---|---|
| Baseline (1 LLM call) | 26 | 34 | 15 | 75 | 708,050 | 54,307 | $0.370 |
| Agent, no repair loop | 60 | 0 | 0 | 90 | 209,150 | 49,679 | $0.163 |
| Agent (full graph) | 60 | 0 | 0 | 90 | 266,292 | 57,620 | $0.199 |
