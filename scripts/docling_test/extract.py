"""Extract text from invoice PDFs with pypdf and with Docling (all local, no API calls).

venv/bin/python -I extract.py <out_dir> <pdf> [<pdf> ...]
Writes <out_dir>/{pypdf,docling}/<stem>.txt and <out_dir>/timings.jsonl (appends; skips done files).
"""
import json
import sys
import time
import traceback
from pathlib import Path

from pypdf import PdfReader

out = Path(sys.argv[1])
pdfs = [Path(p) for p in sys.argv[2:]]
for tool in ("pypdf", "docling"):
    (out / tool).mkdir(parents=True, exist_ok=True)
log = (out / "timings.jsonl").open("a")


def pypdf_text(path):
    reader = PdfReader(str(path))
    return "\n\n".join(f"[page {i}]\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages, 1)), len(reader.pages)


from docling.datamodel.base_models import InputFormat  # noqa: E402
from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: E402
from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: E402

opts = PdfPipelineOptions()  # defaults: OCR on, table structure on; nothing remote
opts.enable_remote_services = False
converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

for n, pdf in enumerate(pdfs, 1):
    stem = pdf.stem
    row = {"doc": stem, "file": str(pdf)}
    if not (out / "pypdf" / f"{stem}.txt").exists():
        t = time.perf_counter()
        text, pages = pypdf_text(pdf)
        row.update(pypdf_s=round(time.perf_counter() - t, 3), pages=pages)
        (out / "pypdf" / f"{stem}.txt").write_text(text)
    if not (out / "docling" / f"{stem}.txt").exists():
        t = time.perf_counter()
        try:
            res = converter.convert(str(pdf))
            doc = res.document
            (out / "docling" / f"{stem}.txt").write_text(doc.export_to_markdown())
            row.update(docling_s=round(time.perf_counter() - t, 2), docling_status=str(res.status),
                       tables=len(doc.tables), errors=[str(e)[:300] for e in (res.errors or [])])
        except Exception as e:  # noqa: BLE001 - record and keep going
            row.update(docling_s=round(time.perf_counter() - t, 2), docling_status="EXCEPTION",
                       errors=[traceback.format_exc()[-1500:]])
            (out / "docling" / f"{stem}.txt").write_text("")
    log.write(json.dumps(row) + "\n")
    log.flush()
    print(f"{n}/{len(pdfs)} {stem} {row.get('docling_s', '-')}s", flush=True)
