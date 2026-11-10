# invoice-review-agent

**An accounts-payable agent built with LangGraph.** It reads supplier invoices (PDF), extracts them into typed data with OpenAI structured outputs, runs deterministic financial and fraud checks, re-reads once when it suspects its own misread, and **pauses for a human** (LangGraph `interrupt`) whenever money should not move automatically. Paused runs live in a SQLite checkpoint and resume from the CLI or a Streamlit review console, even after a restart.

The point of the project is the evaluation: on a held-out set of 150 invoices, I compare the agent to the obvious alternative, a single LLM call that extracts *and* reviews with the vendor master file and ledger in its prompt.

| Held-out test set, 150 invoices, `gpt-4.1-mini` | Baseline: 1 LLM call | **Agent (LangGraph)** |
|---|---|---|
| Routing accuracy (auto-post vs. human review) | 67.3% [59.5, 74.3] | **100.0%** [97.5, 100.0] |
| Anomalies sent to a human (recall, 60 anomalous docs) | 43.3% | **100.0%** |
| Clean invoices needlessly sent to a human (90 clean docs) | 16.7% | **0.0%** [0.0, 4.1] |
| Invoices with every field extracted correctly | 95.3% | 99.3% |
| Cost per invoice | $0.0025 | **$0.0013** |
| Mean latency | 2.8 s | 3.0 s |

95% Wilson intervals in brackets. Paired exact McNemar on routing: the agent was right on 49 documents where the baseline was wrong, and the reverse happened on 0 (p < 1e-14). Full report: [`eval/results/test-gpt-4.1-mini/report.md`](eval/results/test-gpt-4.1-mini/report.md). Every prediction is in `predictions.jsonl` next to it.

**Read the caveats before the numbers.** The invoices are synthetic, and I wrote both the generator and the checks, so the checks cover the eight anomaly types by construction. The 100% says the extraction stayed accurate enough for deterministic rules to do their job on this data. It does not say the agent would score 100% on real invoices. See [Limitations](#limitations).

---

## Why this design

Extracting an invoice with a good LLM is mostly solved. The hard part in accounts payable is **controls**: does the arithmetic hold, is the IBAN valid, is this the bank account we have on file, did we already pay this invoice? Asking the LLM to judge those in the same call is tempting. The eval shows why that fails:

- An LLM can't run a mod-97 checksum or add 30 line items reliably in its head. The baseline caught **0 of 7** invalid IBANs and **1 of 8** subtotal mismatches.
- It is confidently inconsistent. It flagged `bank_account_changed` on clean invoices with reasons like *"differs from the vendor master file … (actually they are the same …) no flag needed"*: the flag contradicts the explanation in the same structured output.
- It costs more: the vendor master and ledger ride along in every prompt.

So the LLM does what it is good at (reading messy, out-of-order PDF text in three languages), and plain Python does what it is good at (arithmetic, checksums, lookups). LangGraph holds the control flow, the retries, the audit trail and the human pause.

## Architecture

```mermaid
flowchart TD
    S([PDF]) --> load[load<br/><i>pypdf text</i>]
    load --> extract[extract<br/><i>gpt-4.1-mini, strict JSON schema<br/>+ IBAN snapping</i>]
    extract --> validate[validate<br/><i>deterministic checks</i>]
    validate -- "misread suspected<br/>(1 retry max)" --> extract
    validate -- clean --> post[post to ledger]
    validate -- "anomaly / still inconsistent" --> human[human_review<br/><i>interrupt()</i>]
    human -- approve --> post
    human -- reject --> reject[reject]
    human -- "correct a field" --> validate
    post --> E([done])
    reject --> E
```

| Piece | How it is built |
|---|---|
| **Typed state** | `InvoiceState` (`TypedDict`) with append-only reducers (`Annotated[list, operator.add]`) for the audit trail and per-call token usage. |
| **Structured output** | `ChatOpenAI.with_structured_output(InvoiceData, method="json_schema", strict=True, include_raw=True)`. Pydantic models in [`schemas.py`](src/invoice_agent/schemas.py), token usage read from the raw message. |
| **Dependency injection** | Extractor, vendor master and ledger are passed through LangGraph's runtime `context` (`StateGraph(..., context_schema=Deps)`), so the tests run the *real* graph with a scripted extractor. |
| **Self-repair** | If a check suggests a misread (value not in the document, arithmetic off), `extract` runs once more with its previous output and the failed checks. A guard stops the repair from deleting a value that was printed. |
| **Human-in-the-loop** | `interrupt(payload, response_schema=ReviewDecision)`: the resume value is validated, so a malformed decision raises instead of posting. The node routes with `Command(goto=...)` to `post`, `reject` or back to `validate` after a correction. |
| **Durability** | `SqliteSaver` checkpoints, so a paused invoice survives restarts. Thread ID = hash of the PDF bytes, so re-processing a file is idempotent, and ledger writes are upserts keyed on the thread. |
| **Resilience** | `RetryPolicy` on `extract` for transient OpenAI errors (rate limits, timeouts, 5xx). |
| **Observability** | Every node appends to `trail`. Set `LANGSMITH_TRACING=true` to send traces to LangSmith. |

### The checks ([`checks.py`](src/invoice_agent/checks.py))

| Code | Kind | Rule |
|---|---|---|
| `ungrounded_value` | extraction | Invoice number, IDs, IBAN, dates and every amount must literally appear in the document text (any number/date format). Catches misreads. |
| `seller_is_buyer` | extraction | The "seller" VAT ID is our own: seller and buyer were swapped. |
| `line_math`, `line_sum_mismatch` | consistency | qty × unit = amount; line items sum to the subtotal. |
| `tax_lines_mismatch`, `vat_miscalculated` | consistency | Tax lines add up; each tax amount = base × rate. |
| `total_mismatch` | consistency | subtotal + tax = total. |
| `due_before_issue` | consistency | Due date is not before the invoice date. |
| `invalid_iban` | document | ISO 13616 mod-97 checksum. |
| `unknown_vendor` | policy | Seller matched in the vendor master by VAT ID, then fuzzy name. |
| `bank_account_changed` | policy | IBAN differs from the one on file: a classic payment-fraud signal. |
| `duplicate_invoice` | policy | Same vendor + invoice number already in the ledger. |

*Extraction* and *consistency* errors earn one re-read. Anything still failing, plus every *document* or *policy* error, goes to a human.

## Evaluation

### Data

[`synth/`](src/invoice_agent/synth) renders invoices to PDF with reportlab and keeps the printed values as ground truth. It is seeded, and `invoice-agent generate` rebuilds `data/` byte for byte. It aims at the things that actually break extraction:

- **4 layouts × 3 label languages** (English, French, German) and 6 vendor countries, with matching number formats (`1,234.56` / `1 234,56` / `1.234,56`) and date formats (`03/04/2026` means different days in the US and the UK).
- **Text out of reading order:** layout blocks are drawn in shuffled order, so the PDF text stream puts totals before items, or the buyer before the seller. Two-column layouts interleave seller and buyer lines, *including both VAT IDs*.
- **Distractors:** order and delivery dates, "Amount paid / Balance due", carried-forward page subtotals on 2-page invoices, credit notes that reference another invoice number, mixed VAT rates, discounts, shipping lines, US invoices with an EIN and no VAT.
- **Eight injected anomalies** with labels: line-sum, VAT and total mismatches, due before issue, invalid IBAN, unknown vendor, changed bank account, duplicate invoice.

| Split | Docs | Anomalous | Use |
|---|---|---|---|
| `dev` | 40 | 16 (2 per type) | Prompt and check development. I looked at every error here. |
| `test` | 150 | 60 (7–8 per type) | Held out. Run once, after the code was frozen. |

A test ([`test_checks.py`](tests/test_checks.py)) runs the checks on the *ground truth* of every dev document: with perfect extraction, clean invoices raise no errors and every anomaly raises its code. So any agent error has to come from the LLM's reading.

### Systems

- **Baseline:** one `gpt-4.1-mini` call with the same extraction rules plus review instructions, the vendor master file and the ledger history in the prompt. It returns the invoice, anomaly flags and `needs_review`. Same model and same structured-output setup as the agent: only the architecture differs.
- **Agent:** the graph above.
- **Agent without repair (ablation):** the agent's *first* extraction scored by the same checks, i.e. the graph with `max_attempts=1`, paired with the full agent on the same LLM output.

### Results on the held-out test set

| Anomaly (n) | Baseline: caught with the right reason | Agent: caught with the right reason |
|---|---|---|
| line_sum_mismatch (8) | 1 | 8 |
| vat_miscalculated (8) | 3 | 8 |
| total_mismatch (8) | 2 | 8 |
| due_before_issue (8) | 8 | 7 * |
| invalid_iban (7) | 0 | 7 |
| unknown_vendor (7) | 0 | 7 |
| bank_account_changed (7) | 0 | 7 |
| duplicate_invoice (7) | 3 | 7 |

\* Sent to a human, but for a different reason. See finding 3.

| | Baseline | Agent, no repair | Agent |
|---|---|---|---|
| LLM calls per invoice | 1.00 | 1.00 | 1.21 |
| Tokens in / out (150 docs) | 708,050 / 54,307 | 209,150 / 49,679 | 266,292 / 57,620 |
| Cost for the run | $0.370 | $0.163 | $0.199 |

Prices: $0.40 / 1M input and $1.60 / 1M output tokens, set in `.env`. The whole held-out run (baseline + agent) cost **$0.57**.

### What I learned

1. **Deterministic checks, not a smarter prompt, made the difference.** The baseline extracted fields almost as well as the agent (95.3% vs 99.3% of invoices fully correct, McNemar p = 0.07, not significant), but it routed a third of the invoices wrong. Most of the gain is in the *review* step.
2. **The re-read (repair) loop did not earn its cost on this data.** Routing was identical with and without it (100% / 100%). On the test set it fired 32 times, every time on a genuine document anomaly, never on a misread it could fix. That is 21% more LLM calls for nothing.
3. **And once it made things worse.** On `test-050` (due date before issue date), the repair pass *changed the printed due date* to make the invoice look consistent, despite the prompt telling it not to. The grounding check caught the invented date (`ungrounded_value`), so the invoice still went to a human, but with the wrong reason. The "no silent drops" guard came from the same failure on dev (the model deleted the due date). Models really want to make documents consistent.
4. **The cheap fix beat the clever one.** On dev, the model kept dropping or repeating a 4-character group when copying 27-character French IBANs. A wrong IBAN that still looks plausible is how money goes to the wrong account: the baseline misread the IBAN on 6 test invoices and auto-approved 2 of them. Instead of prompting harder, the agent *snaps* the IBAN to the exact IBAN-shaped string printed after an "IBAN" label (`snap.py`). That corrected the IBAN on 10 test invoices. The checksum and vendor checks still run on the snapped value.
5. **Telling the model who "we" are matters.** In the first dev run ([`dev-v0-before-fixes`](eval/results/dev-v0-before-fixes/report.md)), the agent swapped seller and buyer on 21 of 40 first reads, because nothing in the prompt said which company was the buyer. The baseline got that implicitly from the vendor list. Both systems now get the buyer's name and VAT ID, as a real AP system would.

Agent routing on dev went from 87.5% to 100% after these changes: IBAN snapping, the buyer hint, the no-silent-drops guard, and two labelling clarifications that applied to both systems (a US EIN counts as the seller's tax ID; a printed "Sales tax 0.00" line is equivalent to no tax lines). All of them were made on dev, before the single test run.

Next step, **not evaluated yet**: skip the re-read when every number involved is grounded. If all printed values are in the document and they still don't add up, the document is wrong, not the read. That would remove all 32 wasted calls on the test set and the `test-050` regression.

### Real invoices: ConfBench (40 digital FCC invoices)

The synthetic set can't tell you how the agent behaves on documents nobody designed for it. So the same graph also runs on [Amazon ConfBench](https://huggingface.co/datasets/amazon/ConfBench): US TV and cable stations' invoices for political ads, public records filed with the FCC, with human-verified labels (CC BY-NC 4.0, downloaded by `invoice-agent fetch-confbench`, not redistributed here).

These invoices have no VAT or IBAN, so they get their own **profile**: same graph, a different schema (gross airtime, agency commission, other deductions, net due) and checks (every amount must appear in the text; gross − commission − deductions = net). Before spending anything, I found that **35 of the 75 "originals" are scans with no text layer**. This agent has no OCR, so they are excluded and 40 are scored.

| 40 digital invoices, `gpt-4.1-mini` | Single call (no checks) | Agent |
|---|---|---|
| Gross, commission and net all match the label | 80.0% | 82.5% |
| Sent to a human | 0% (auto-approves everything) | 15.0% (6 invoices) |
| Auto-approved with an amount that disagrees with the label | 8 | 4 |

Cost of the run: $0.11. Full report: [`report.md`](eval/results/real-confbench-gpt-4.1-mini/report.md).

Scoring against labels isn't the whole story here: the labels have their own errors. So I read the document text for every flagged invoice and every auto-approved disagreement ([`ADJUDICATION.md`](eval/results/real-confbench-gpt-4.1-mini/ADJUDICATION.md)):

- **The 4 auto-approved "errors" are not misreads.** One is a label rounding a printed $37,972.90 to $37,973. Two are invoices the labels give no net amount for, where the agent used the printed total. One is a $0 invoice.
- **4 of the 6 flagged invoices had a real extraction problem**, including an invoice number the model garbled from run-together PDF text, and another case of the repair pass *calculating* a commission that isn't printed to make the numbers add up (caught by the grounding check, as on the synthetic set). The other 2 are documents a person should check anyway: one prints no totals, one deducts $15,750 without saying what for.
- **Names are the weak spot.** Agency matches the label on 70% of invoices, but in 9 of the 12 misses the label names the station that issued the invoice as the "agency". Advertiser: 82.5%, with 2 real misses.

Small sample, one run, adjudicated by hand: treat these as a sanity check that the approach transfers to real documents, not as a benchmark score.

## Run it

Requires Python 3.11+ and an OpenAI API key (only for processing new invoices and for the eval; tests need no key).

```bash
git clone https://github.com/ZackHiRo/invoice-review-agent && cd invoice-review-agent
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,ui]"
cp .env.example .env   # add OPENAI_API_KEY
```

Process two invoices: a clean one and a duplicate.

```bash
.venv/bin/invoice-agent process data/test/test-002.pdf data/test/test-001.pdf
```

Work the review queue (each command resumes from the SQLite checkpoint):

```bash
.venv/bin/invoice-agent queue
.venv/bin/invoice-agent show <thread-id>
.venv/bin/invoice-agent review <thread-id> --reject --note "resubmitted invoice"
.venv/bin/invoice-agent review <thread-id> --set total=1234.56   # correct a field and re-check
.venv/bin/invoice-agent ledger
```

Review console (queue, PDF side by side with the extraction, approve / reject / correct):

```bash
.venv/bin/streamlit run src/invoice_agent/ui.py
```

Tests (42 tests, no API key needed: the real graph runs with a scripted extractor):

```bash
.venv/bin/pytest -q
```

Reproduce the eval (about $0.60 on `gpt-4.1-mini`, 2 minutes):

```bash
.venv/bin/invoice-agent eval --split test
```

Real invoices (downloads about 21 MB from Hugging Face, then about $0.11):

```bash
.venv/bin/pip install -e ".[real]"
.venv/bin/invoice-agent fetch-confbench
.venv/bin/invoice-agent eval-real
```

Docker:

```bash
docker compose up ui                                               # review console on :8501
docker compose run --rm cli process data/test/test-001.pdf
```

Other: `invoice-agent graph` prints the compiled graph as Mermaid. `invoice-agent generate --seed 2026` rebuilds the dataset.

## Layout

```
src/invoice_agent/
  graph.py        LangGraph StateGraph: nodes, routing, interrupt, Command
  profiles.py     document profiles (schema + checks) the graph runs with
  extract.py      LLM extractor (strict structured output) and repair prompt
  checks.py       deterministic checks (grounding, arithmetic, IBAN, vendor, ledger)
  snap.py         IBAN snapping + "repair may not delete printed values" guard
  baseline.py     one-call LLM baseline
  store.py        vendor master + SQLite ledger and review queue
  cli.py, ui.py   Typer CLI and Streamlit review console
  synth/          seeded invoice generator (reportlab)
  evaluation/     runner, metrics (field accuracy, routing, Wilson CI, McNemar)
  real/           ConfBench downloader, broadcast-invoice profile, real-invoice eval
data/             generated PDFs, labels.jsonl, vendors.json, ledger_seed.json
eval/results/     summary.json, report.md and every prediction per run
tests/            checks, graph (HITL, durability, idempotency), CLI, UI, metrics
```

## Limitations

- **Synthetic data, made by the same person who wrote the checks.** The anomaly types and the checks were designed together, and the oracle test proves the checks are complete *for these eight types*. Real invoices have anomalies no rule here anticipates (wrong PO, price above contract, VAT rate wrong for the goods). The ConfBench check covers real documents, but only 40 of them, with no injected anomalies, and a different invoice type.
- **No OCR.** The agent reads the PDF's text layer. 35 of the 75 real ConfBench invoices are scans, and it can't read them at all.
- **One model, one run.** Only `gpt-4.1-mini` at temperature 0, one pass over the test set. I also started a `gpt-4o-mini` run to see whether the repair loop helps a weaker model, but the API account ran out of credits partway through. I deleted those partial results instead of reporting them.
- **The baseline could be stronger.** Its schema puts `reason` *after* `flags`, so it commits to flags before explaining them (visible in its self-contradicting reasons). A reason-first schema, a reasoning model, or giving the baseline a calculator tool would likely close part of the gap. This compares two reasonable designs, not the best possible single prompt.
- **Small per-type counts:** 7–8 test documents per anomaly type, so per-type rates have wide intervals. The headline intervals are in the report.
- **Dev was used for tuning.** The changes made after reading dev errors are listed under finding 5, and the before-state is in `eval/results/dev-v0-before-fixes`. The test set was run once, after that.
- **The Docker setup is untested.** The image build could not be verified on the machine this was written on (Docker Desktop failed with a disk I/O error from a full disk). Everything else was run locally.
- Every company, VAT ID and IBAN in `data/` is invented.

## License

MIT
