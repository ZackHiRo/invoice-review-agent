# Eval: `dev` split, 40 documents, `gpt-4.1-mini`

Run 2026-11-09T22:32:15+00:00 -> 2026-11-09T22:32:44+00:00 (UTC). Prices: $0.4/1M input, $1.6/1M output tokens.

## Headline

| System | Routing accuracy | Anomaly recall | False-review rate | Docs fully correct | LLM calls/doc | Cost/doc | Mean latency |
|---|---|---|---|---|---|---|---|
| Baseline (1 LLM call) | 70.0% [54.6, 81.9] | 43.8% [23.1, 66.8] | 12.5% [4.3, 31.0] | 92.5% | 1.00 | $0.0024 | 2.6s |
| Agent, no repair loop | 100.0% [91.2, 100.0] | 100.0% [80.6, 100.0] | 0.0% [0.0, 13.8] | 100.0% | 1.00 | $0.0010 | n/a |
| Agent (full graph) | 100.0% [91.2, 100.0] | 100.0% [80.6, 100.0] | 0.0% [0.0, 13.8] | 100.0% | 1.20 | $0.0012 | 2.8s |

Brackets are 95% Wilson intervals. Run errors: Baseline (1 LLM call): 0, Agent, no repair loop: 0, Agent (full graph): 0.

## Paired tests (exact McNemar)

| Comparison | Only first correct | Only second correct | p-value |
|---|---|---|---|
| `agent_vs_baseline_routing` | 12 | 0 | 0.0005 |
| `agent_vs_baseline_doc_exact` | 3 | 0 | 0.2500 |
| `agent_vs_agent_no_repair_routing` | 0 | 0 | 1.0000 |
| `agent_vs_agent_no_repair_doc_exact` | 0 | 0 | 1.0000 |

## Field-level extraction accuracy

| Field | Baseline (1 LLM call) | Agent, no repair loop | Agent (full graph) |
|---|---|---|---|
| document_type | 100.0% | 100.0% | 100.0% |
| invoice_number | 100.0% | 100.0% | 100.0% |
| invoice_date | 97.5% | 100.0% | 100.0% |
| due_date | 100.0% | 100.0% | 100.0% |
| currency | 100.0% | 100.0% | 100.0% |
| seller_name | 100.0% | 100.0% | 100.0% |
| seller_vat_id | 100.0% | 100.0% | 100.0% |
| buyer_name | 100.0% | 100.0% | 100.0% |
| po_number | 100.0% | 100.0% | 100.0% |
| iban | 95.0% | 100.0% | 100.0% |
| subtotal | 100.0% | 100.0% | 100.0% |
| tax_total | 100.0% | 100.0% | 100.0% |
| total | 100.0% | 100.0% | 100.0% |
| line_items | 100.0% | 100.0% | 100.0% |
| tax_lines | 100.0% | 100.0% | 100.0% |

## Anomalies caught with the right reason

Routed to review AND the matching code was flagged.

| Anomaly | Baseline (1 LLM call) | Agent, no repair loop | Agent (full graph) |
|---|---|---|---|
| line_sum_mismatch | 0/2 | 2/2 | 2/2 |
| vat_miscalculated | 0/2 | 2/2 | 2/2 |
| total_mismatch | 0/2 | 2/2 | 2/2 |
| due_before_issue | 2/2 | 2/2 | 2/2 |
| invalid_iban | 0/2 | 2/2 | 2/2 |
| unknown_vendor | 0/2 | 2/2 | 2/2 |
| bank_account_changed | 1/2 | 2/2 | 2/2 |
| duplicate_invoice | 0/2 | 2/2 | 2/2 |

## Confusion (human_review = positive)

| System | TP | FN | FP | TN | Tokens in | Tokens out | Total cost |
|---|---|---|---|---|---|---|---|
| Baseline (1 LLM call) | 7 | 9 | 3 | 21 | 187,540 | 12,548 | $0.095 |
| Agent, no repair loop | 16 | 0 | 0 | 24 | 54,500 | 11,435 | $0.040 |
| Agent (full graph) | 16 | 0 | 0 | 24 | 69,130 | 13,534 | $0.049 |
