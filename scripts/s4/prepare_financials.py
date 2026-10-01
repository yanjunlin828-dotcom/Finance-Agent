"""Prepare new observations without changing S2 seeds, database or old runs."""
import argparse
from datetime import date
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.retrieval.corpus import ResearchCorpus, write_json, file_sha256
from finresearch.finance.research_tables import extract_annual_observations
from finresearch.storage.metric_store import MetricStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--as-of-date", required=True)
    args = parser.parse_args()
    if not args.attempt_id.replace("-", "").isalnum():
        raise ValueError("attempt-id不安全")
    paths = {"corpus": ROOT / "storage/s3/corpora/s3-semiconductor-equipment-ar-v1/ready.json",
             "mapping": ROOT / "configs/s4/financial_source_map.json", "dictionary": ROOT / "configs/s2/metric_dictionary.json"}
    payloads = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in paths.items()}
    corpus = ResearchCorpus(ROOT, payloads["corpus"])
    observations, evidence, checks = extract_annual_observations(corpus, payloads["mapping"], payloads["dictionary"], date.fromisoformat(args.as_of_date))
    run = ROOT / "runs/s4" / args.attempt_id
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / "inputs.lock.json", {"as_of_date": args.as_of_date, "inputs": {k: {"path": p.relative_to(ROOT).as_posix(), "sha256": file_sha256(p)} for k, p in paths.items()}})
    write_json(run / "observations.json", [o.model_dump(mode="json") for o in observations])
    write_json(run / "evidence.json", [e.model_dump(mode="json") for e in evidence])
    write_json(run / "source_validation.json", checks)
    store = MetricStore(ROOT / "storage/s4/runtime" / args.attempt_id / "metrics.sqlite")
    store.save_observations(observations)
    write_json(run / "summary.json", {"status": "FINANCIAL_INPUTS_PREPARED_NOT_G4", "observation_count": len(observations), "source_checks_passed": all(c["passed"] for c in checks), "companies": sorted({o.company_id for o in observations})})
    print(json.dumps({"observations": len(observations), "checks_passed": all(c["passed"] for c in checks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
