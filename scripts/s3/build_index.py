"""Build a new locked index from the explicitly selected READY corpus."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.retrieval.corpus import ResearchCorpus, chunk_pages, write_json, file_sha256
from finresearch.retrieval.vector_index import LocalEmbedding, VectorIndex


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-name", required=True)
    args = parser.parse_args()
    if not args.index_name.replace("-", "").isalnum():
        raise ValueError("索引名不安全")
    config = json.loads((ROOT / "configs/s3/retrieval.json").read_text(encoding="utf-8"))
    ready_path = ROOT / "storage/s3/corpora" / config["corpus_snapshot_id"] / "ready.json"
    corpus = ResearchCorpus(ROOT, json.loads(ready_path.read_text(encoding="utf-8")))
    chunks = chunk_pages(corpus, config)
    folder = ROOT / "indexes/s3" / args.index_name
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "chunks.json", [c.model_dump(mode="json") for c in chunks])
    write_json(folder / "inputs.lock.json", {"corpus_sha256": file_sha256(ready_path), "retrieval_config_sha256": file_sha256(ROOT / "configs/s3/retrieval.json")})
    embedding = LocalEmbedding(ROOT, config)
    lengths = embedding.token_lengths([c.text for c in chunks])
    print(json.dumps({"chunks": len(chunks), "maximum_tokens": max(lengths), "maximum_characters": max(len(c.text) for c in chunks)}), flush=True)
    index = VectorIndex(folder, embedding, chunks, config, build=True)
    index.close()
    print(json.dumps({"index": args.index_name, "status": "BUILT"}), flush=True)


if __name__ == "__main__":
    main()
