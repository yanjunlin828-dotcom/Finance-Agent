"""Import registered official PDFs; extraction alone does not mark READY."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.ingestion.pdf_pages import PDFPageIngestor, load_verified_pages
from finresearch.retrieval.corpus import read_jsonl, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-id", required=True)
    args = parser.parse_args()
    entries = []
    ingestor = PDFPageIngestor(ROOT, ROOT / "storage/s3/snapshots", snapshot_prefix="s3")
    for manifest in read_jsonl(ROOT / "storage/s3/document_manifest.jsonl"):
        print(f"Extracting {manifest['document_id']}", flush=True)
        result = ingestor.import_document(manifest)
        snapshot = {
            "snapshot_id": result.snapshot_id, "document_id": result.document_id,
            "page_count": result.page_count, "pages_sha256": result.pages_sha256,
            "pages_path": result.pages_path.relative_to(ROOT).as_posix(),
            "snapshot_manifest_path": result.snapshot_manifest_path.relative_to(ROOT).as_posix(),
        }
        pages = load_verified_pages(ROOT, snapshot, manifest)
        entries.append({"status": "EXTRACTED", "manifest": manifest, "snapshot": snapshot,
                        "content_checks": [], "nonempty_pages": sum(bool(p.normalized_text) for p in pages)})
        print(json.dumps({"document_id": result.document_id, "pages": len(pages), "nonempty": entries[-1]["nonempty_pages"], "reused": result.reused_existing}), flush=True)
    write_json(ROOT / "storage/s3/corpora" / args.snapshot_id / "extracted.json",
               {"corpus_snapshot_id": args.snapshot_id, "status": "EXTRACTED", "documents": entries})


if __name__ == "__main__":
    main()
