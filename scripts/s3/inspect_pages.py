"""Read canonical source pages for manual labeling; no retrieval ranking."""
import argparse
import json
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--pages", required=True)
    args = parser.parse_args()
    corpus = json.loads((ROOT / "storage/s3/corpora/s3-semiconductor-equipment-ar-v1/extracted.json").read_text(encoding="utf-8"))
    entry = next(e for e in corpus["documents"] if e["manifest"]["ts_code"] == args.company and e["manifest"]["reporting_period"] == args.year)
    selected = {int(p) for p in args.pages.split(",")}
    for line in (ROOT / entry["snapshot"]["pages_path"]).read_text(encoding="utf-8").splitlines():
        page = json.loads(line)
        if page["pdf_page"] in selected:
            print(f"\nPDF PAGE {page['pdf_page']}\n{page['normalized_text']}")


if __name__ == "__main__":
    main()
