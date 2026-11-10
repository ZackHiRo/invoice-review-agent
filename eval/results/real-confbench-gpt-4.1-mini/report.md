# Real invoices: ConfBench FCC originals, `gpt-4.1-mini`

75 documents; 35 are scans without a text layer and are excluded (no OCR path); 40 digital invoices scored. Run errors: 0. Run 2026-11-10T16:30:24+00:00 -> 2026-11-10T16:31:00+00:00 (UTC).

## Field accuracy against the published labels

| Field | Single call (first read, no checks) | Agent (after checks + repair) |
|---|---|---|
| agency | 70.0% | 70.0% |
| advertiser | 82.5% | 82.5% |
| gross_total | 95.0% | 95.0% |
| agency_commission | 92.5% | 92.5% |
| net_amount_due | 87.5% | 87.5% |
| payment_terms | 92.5% | 92.5% |
| **all three amounts** | 80.0% | 82.5% |

## Do the checks catch the wrong ones?

An invoice counts as wrong if any of gross, commission or net disagrees with its label.

| | Amounts right | Amounts wrong |
|---|---|---|
| Sent to a human | 3 (false alarm) | 3 (caught) |
| Auto-approved | 30 | **4 (silent error)** |

- Review rate: 15.0%. Repairs: 7.
- Wrong invoices caught: 42.9% [15.8, 75.0].
- Silent errors (wrong amounts, auto-approved): single call 8, agent 4.
- Tokens in/out 256,030 / 3,963; cost $0.109.

## Every amount disagreement with the labels

`in doc` = the value appears literally in the document text.

| Document | Field | Agent | Label | Agent in doc | Label in doc | Route |
|---|---|---|---|---|---|---|
| `1b238269de` | agency_commission | None | 0.0 | no | yes | auto_approve |
| `5b79d718c8` | net_amount_due | 5886.5 | None | yes | no | auto_approve |
| `75986ca8ed` | gross_total | 3691.0 | 3900.0 | no | no | human_review |
| `75986ca8ed` | net_amount_due | 3115.0 | 3315.0 | no | no | human_review |
| `796ff0059c` | agency_commission | 745.26 | 1011.6 | yes | yes | human_review |
| `796ff0059c` | gross_total | 6756.0 | 6744.0 | yes | yes | human_review |
| `796ff0059c` | net_amount_due | 6756.0 | 4987.14 | yes | yes | human_review |
| `beedfe6996` | net_amount_due | 37972.9 | 37973.0 | yes | no | auto_approve |
| `cb4e0286d5` | agency_commission | 2318.45 | None | no | no | human_review |
| `fa1d6b1792` | net_amount_due | 13770.0 | None | yes | no | auto_approve |
