"""CLI end to end with a scripted extractor: process -> pause -> queue -> review -> ledger.
Each command builds a fresh graph from the on-disk SQLite checkpoint, like separate runs."""

from typer.testing import CliRunner

from invoice_agent import cli
from tests.conftest import DATA, ScriptedExtractor, first


def test_cli_review_flow(tmp_path, monkeypatch, labels):
    row_clean = first(labels, None)
    row_bad = first(labels, "duplicate_invoice")
    by_text = {}

    class FakeLLM(ScriptedExtractor):
        def __init__(self, *a, **k):
            super().__init__()

        def extract(self, text, previous=None, issues=None):
            from invoice_agent.extract import ExtractionResult
            from invoice_agent.schemas import InvoiceData
            return ExtractionResult(InvoiceData(**by_text[text[:200]]),
                                    {"model": "fake", "input_tokens": 1, "output_tokens": 1})

    from invoice_agent.pdf import pdf_to_text
    for r in (row_clean, row_bad):
        by_text[pdf_to_text(DATA / r["file"])[:200]] = r["truth"]

    monkeypatch.setattr("invoice_agent.extract.LLMExtractor", FakeLLM)
    monkeypatch.setattr("invoice_agent.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("INVOICE_AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("INVOICE_AGENT_DATA_DIR", str(DATA))
    runner = CliRunner()

    res = runner.invoke(cli.app, ["process", str(DATA / row_clean["file"]),
                                  str(DATA / row_bad["file"])])
    assert res.exit_code == 0, res.output
    assert "POSTED" in res.output and "PAUSED for human review" in res.output

    res = runner.invoke(cli.app, ["queue"])
    assert "already posted" in res.output
    tid = cli.thread_id_for(DATA / row_bad["file"])

    res = runner.invoke(cli.app, ["review", tid, "--reject", "--note", "resubmitted invoice"])
    assert res.exit_code == 0, res.output
    assert "REJECTED" in res.output

    res = runner.invoke(cli.app, ["queue"])
    assert "empty" in res.output
    res = runner.invoke(cli.app, ["ledger"])
    assert "posted" in res.output and "rejected" in res.output

    # re-processing the same file is idempotent: same thread, no second LLM call
    res = runner.invoke(cli.app, ["process", str(DATA / row_clean["file"])])
    assert "already processed" in res.output
