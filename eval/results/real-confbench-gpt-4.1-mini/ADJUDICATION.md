# Manual adjudication of the ConfBench run

The automatic report scores against the published labels. Afterwards, the document text was
read for every invoice that was either sent to a human or auto-approved with amounts that
disagree with its label. The verdicts below cite the evidence from the PDF text. They are a
manual reading, not an independent annotation, and they do not change `summary.json` or
`report.md`.

## Auto-approved, amounts disagree with the label (4 "silent errors")

| Document | Disagreement | What the document says | Verdict |
|---|---|---|---|
| `beedfe69` | net: agent 37,972.90, label 37,973.00 | `Net Amt $37,972.90` | Label rounded. **Agent right.** |
| `5b79d718` | net: agent 5,886.50, label none | `Totals 878 $5,886.50`, no commission shown | Label has no net; the agent used the printed total as net. Defensible, not a misread. |
| `fa1d6b17` | net: agent 13,770.00, label none | `CONTRACT TOTAL 13770.00`, no commission shown | Same as above. Defensible, not a misread. |
| `1b238269` | commission: agent none, label 0.0 | A $0.00 invoice | Equivalent in practice. |

No auto-approved invoice had an amount the agent clearly misread.

## Sent to a human (6)

| Document | Why it was flagged | Amounts vs label | Verdict |
|---|---|---|---|
| `796ff005` | `total_mismatch` | wrong (net extracted = gross) | **Real extraction error, caught.** |
| `cb4e0286` | `ungrounded_value` | wrong (commission) | First read: gross 8,900 and net 6,581.55 with no commission printed. The repair pass then *computed* a commission of 2,318.45 to make it add up. The grounding check caught the invented value. **Caught.** |
| `75986ca8` | `ungrounded_value` x3, `total_mismatch` | wrong | Neither the agent's nor the label's amounts appear in the text layer. The document needs a human either way. **Caught.** |
| `f3eb6504` | `ungrounded_value` | right | The PDF text runs fields together (`October 201610/17/1612016-1`) and the model returned invoice number `1202016-1`, which is not in the document. Invoice numbers are not labelled, so the report counts this as a false alarm, but **it is a real misread, caught**. |
| `3aac2d4d` | `total_mismatch` | right | Gross 105,000 and net 89,250 with no commission printed. A reviewer should confirm the 15,750 deduction. **Reasonable review.** |
| `aac03825` | `missing_field` | right | No gross or net total on the document (the labels have none either). **Reasonable review.** |

## Name fields

- **agency (70.0%)**: in 9 of the 12 mismatches the label names the *station that issued the invoice* (OnMedia, Viamedia, WTHI, KCRA, KATU, WBAY) as the agency. The agent's schema defines agency as the advertising agency being billed, and it returned that. 2 mismatches are labels with an empty agency. 1 (`3d239b34`) is a likely agent miss.
- **advertiser (82.5%)**: 2 invoices where the agent returned no advertiser (`08e2649c`, `7ec89578`) are agent misses. The rest are different spellings of the same committee (`NRA-ILA` vs `Nat'l Rifle Assoc Institute for Legislative Action`) or labels with an odd or empty value.

## Bottom line

On 40 real digital invoices: 6 were sent to a human, and on reading them 4 had a real
extraction problem and 2 were documents a person should check anyway. Of the 34
auto-approved, none had an amount the agent clearly misread. The weak spot is names, partly
a definitional mismatch with the labels and partly real misses. This is one run on a small
set, adjudicated by hand rather than by an independent annotator, and 35 scanned invoices could not be read at all.
