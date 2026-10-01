"""Create a READY corpus only after explicit source checks, before scoring."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.ingestion.pdf_pages import load_verified_pages
from finresearch.retrieval.corpus import ResearchCorpus, write_json


def main():
    folder = ROOT / "storage/s3/corpora/s3-semiconductor-equipment-ar-v1"
    registry = json.loads((folder / "extracted.json").read_text(encoding="utf-8"))
    cases = json.loads((ROOT / "evals/dev/s3_cases.json").read_text(encoding="utf-8"))["cases"]
    for entry in registry["documents"]:
        pages = load_verified_pages(ROOT, entry["snapshot"], entry["manifest"])
        by_number = {p.pdf_page: p for p in pages}
        checks = []
        for case in cases:
            if case["document_id"] != entry["manifest"]["document_id"]:
                continue
            for expected in case["necessary_pages"]:
                if not all(x in by_number[expected["pdf_page"]].normalized_text for x in expected["excerpts"]):
                    raise ValueError(f"真值摘录与原文不一致: {case['case_id']},p{expected['pdf_page']}")
                checks.append(expected)
        if len(checks) < 5:
            raise ValueError("文档内容抽检不足")
        entry["content_checks"] = checks
        entry["status"] = "READY"
    registry["status"] = "READY"
    registry["label_set_path"] = "evals/dev/s3_cases.json"
    # Validate the new READY representation itself before publishing.
    corpus = ResearchCorpus(ROOT, registry)
    write_json(folder / "ready.json", registry)
    print(json.dumps({"status": "READY", "documents": len(corpus.documents), "pages": len(corpus.pages), "cases": len(cases)}))


if __name__ == "__main__":
    main()
