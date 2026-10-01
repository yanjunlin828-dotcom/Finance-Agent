"""把一个DocumentManifest记录导入S1逐页快照，并更新小型登记文件。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from finresearch.ingestion import PDFPageIngestor  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    args = parser.parse_args()
    manifest_rows = read_jsonl(PROJECT_ROOT / "storage" / "s0" / "document_manifest.jsonl")
    matches = [row for row in manifest_rows if row["document_id"] == args.document_id]
    if len(matches) != 1:
        raise ValueError("document-id必须唯一对应一条DocumentManifest记录")
    storage_root = PROJECT_ROOT / "storage" / "s1" / "snapshots"
    result = PDFPageIngestor(PROJECT_ROOT, storage_root).import_document(matches[0])
    registry_path = PROJECT_ROOT / "storage" / "s1" / "snapshot_registry.json"
    registry = {
        "schema_version": "1.0.0",
        "active_snapshot_id": result.snapshot_id,
        "snapshots": [
            {
                "snapshot_id": result.snapshot_id,
                "document_id": result.document_id,
                "page_count": result.page_count,
                "pages_sha256": result.pages_sha256,
                "pages_path": str(result.pages_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "snapshot_manifest_path": str(result.snapshot_manifest_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            }
        ],
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "snapshot_id": result.snapshot_id,
                "page_count": result.page_count,
                "reused_existing": result.reused_existing,
                "pages_sha256": result.pages_sha256,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
