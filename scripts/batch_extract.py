"""Batch-extract every PDF in a folder without starting the web server.

Useful for evaluating accuracy on a private dataset. Results are written to a
single JSON file; nothing is uploaded anywhere except the configured Gemini API.

Usage:
    .venv\\Scripts\\python.exe scripts\\batch_extract.py "C:\\path\\to\\pdfs" --out report.json

The output path defaults to ``data/batch-<timestamp>.json`` which is gitignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402
from app.services.extraction_service import ExtractionService  # noqa: E402
from app.services.gemini_service import GeminiService  # noqa: E402


async def extract_one(
    service: ExtractionService,
    path: Path,
    semaphore: asyncio.Semaphore,
    index: int,
    total: int,
) -> dict:
    async with semaphore:
        print(f"[{index}/{total}] {path.name} …", flush=True)
        started = datetime.now(timezone.utc)

        try:
            result = await service.process(
                job_id=uuid.uuid4().hex,
                file_path=str(path),
                filename=path.name,
                emit=_silent,
            )
            data = result.model_dump(mode="json")
            invoice = data["invoice"]
            total_value = invoice.get("total_amount")
            currency = invoice.get("currency") or ""
            print(
                f"    ok · {data['pdf_type']:8s} · {invoice.get('invoice_number') or '—':>20s} "
                f"· {currency} {total_value} · {data['timings']['total']:.1f}s",
                flush=True,
            )
            return {
                "filename": path.name,
                "ok": True,
                "started_at": started.isoformat(),
                "result": data,
            }

        except Exception as exc:  # noqa: BLE001 - the CLI reports every failure
            print(f"    FAILED · {type(exc).__name__}: {exc}", flush=True)
            return {
                "filename": path.name,
                "ok": False,
                "started_at": started.isoformat(),
                "error": {"type": type(exc).__name__, "message": str(exc)[:800]},
            }


async def _silent(stage: str, progress: int, message: str) -> None:
    return None


async def run(folder: Path, out: Path, concurrency: int) -> int:
    settings = Settings()
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not configured. Add it to .env first.", file=sys.stderr)
        return 2

    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {folder}", file=sys.stderr)
        return 2

    service = ExtractionService(settings, GeminiService(settings))
    semaphore = asyncio.Semaphore(max(1, concurrency))

    print(f"Processing {len(pdfs)} PDF(s) with concurrency={concurrency} …\n")
    tasks = [
        extract_one(service, pdf, semaphore, index, len(pdfs))
        for index, pdf in enumerate(pdfs, start=1)
    ]
    records = await asyncio.gather(*tasks)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "folder": str(folder),
        "model": settings.gemini_model,
        "count": len(records),
        "succeeded": sum(1 for record in records if record["ok"]),
        "failed": sum(1 for record in records if not record["ok"]),
        "invoices": records,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out}")
    print(f"Succeeded: {payload['succeeded']} · Failed: {payload['failed']}")
    return 0 if payload["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch invoice extraction from a folder of PDFs.")
    parser.add_argument("folder", type=Path, help="Folder containing PDF invoices")
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "data" / f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
        help="Where to write the JSON report (default: data/batch-<timestamp>.json)",
    )
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel extractions (default: 2)")
    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"Not a folder: {args.folder}", file=sys.stderr)
        return 2

    return asyncio.run(run(args.folder, args.out, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
