# Eval: `dev` split, 40 documents, `gpt-4.1-mini`

Run 2026-11-09T22:29:47+00:00 -> 2026-11-09T22:30:25+00:00 (UTC). Prices: $0.4/1M input, $1.6/1M output tokens.

## Headline

| System | Routing accuracy | Anomaly recall | False-review rate | Docs fully correct | LLM calls/doc | Cost/doc | Mean latency |
|---|---|---|---|---|---|---|---|
| Baseline (1 LLM call) | 72.5% [57.2, 83.9] | 43.8% [23.1, 66.8] | 8.3% [2.3, 25.8] | 70.0% | 1.00 | $0.0024 | 2.7s |
| Agent, no repair loop | 60.0% [44.6, 73.7] | 100.0% [80.6, 100.0] | 66.7% [46.7, 82.0] | 25.0% | 1.00 | $0.0010 | n/a |
| Agent (full graph) | 87.5% [73.9, 94.5] | 93.8% [71.7, 98.9] | 16.7% [6.7, 35.9] | 70.0% | 1.73 | $0.0018 | 4.2s |

Brackets are 95% Wilson intervals. Run errors: Baseline (1 LLM call): 0, Agent, no repair loop: 0, Agent (full graph): 0.

## Paired tests (exact McNemar)

| Comparison | Only first correct | Only second correct | p-value |
|---|---|---|---|
| `agent_vs_baseline_routing` | 11 | 5 | 0.2101 |
| `agent_vs_baseline_doc_exact` | 2 | 2 | 1.0000 |
| `agent_vs_agent_no_repair_routing` | 12 | 1 | 0.0034 |
| `agent_vs_agent_no_repair_doc_exact` | 18 | 0 | 0.0000 |

## Field-level extraction accuracy

| Field | Baseline (1 LLM call) | Agent, no repair loop | Agent (full graph) |
|---|---|---|---|
| document_type | 100.0% | 100.0% | 100.0% |
| invoice_number | 100.0% | 100.0% | 100.0% |
| invoice_date | 97.5% | 100.0% | 97.5% |
| due_date | 97.5% | 100.0% | 97.5% |
| currency | 100.0% | 100.0% | 100.0% |
| seller_name | 100.0% | 47.5% | 100.0% |
| seller_vat_id | 100.0% | 32.5% | 82.5% |
| buyer_name | 100.0% | 47.5% | 100.0% |
| po_number | 100.0% | 100.0% | 100.0% |
| iban | 92.5% | 90.0% | 90.0% |
| subtotal | 100.0% | 100.0% | 100.0% |
| tax_total | 100.0% | 100.0% | 100.0% |
| total | 100.0% | 100.0% | 100.0% |
| line_items | 100.0% | 100.0% | 100.0% |
| tax_lines | 80.0% | 80.0% | 80.0% |

## Anomalies caught with the right reason

Routed to review AND the matching code was flagged.

| Anomaly | Baseline (1 LLM call) | Agent, no repair loop | Agent (full graph) |
|---|---|---|---|
| line_sum_mismatch | 0/2 | 2/2 | 2/2 |
| vat_miscalculated | 0/2 | 2/2 | 2/2 |
| total_mismatch | 0/2 | 2/2 | 2/2 |
| due_before_issue | 2/2 | 2/2 | 1/2 |
| invalid_iban | 0/2 | 2/2 | 2/2 |
| unknown_vendor | 0/2 | 2/2 | 2/2 |
| bank_account_changed | 0/2 | 1/2 | 2/2 |
| duplicate_invoice | 0/2 | 1/2 | 2/2 |

## Confusion (human_review = positive)

| System | TP | FN | FP | TN | Tokens in | Tokens out | Total cost |
|---|---|---|---|---|---|---|---|
| Baseline (1 LLM call) | 7 | 9 | 2 | 22 | 185,980 | 12,521 | $0.094 |
| Agent, no repair loop | 16 | 0 | 16 | 8 | 52,940 | 11,443 | $0.039 |
| Agent (full graph) | 15 | 1 | 4 | 20 | 104,006 | 18,602 | $0.071 |
