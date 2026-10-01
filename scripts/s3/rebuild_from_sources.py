"""Reparse the four original PDFs and rebuild embeddings/index from scratch."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finresearch.ingestion.pdf_pages import PDFPageIngestor
from finresearch.retrieval.corpus import ResearchCorpus, chunk_pages, write_json, file_sha256
from finresearch.retrieval.vector_index import LocalEmbedding, VectorIndex


def main():
    config = json.loads((ROOT / "configs/s3/retrieval.json").read_text(encoding="utf-8"))
    original = ROOT / "storage/s3/corpora" / config["corpus_snapshot_id"] / "ready.json"
    registry = json.loads(original.read_text(encoding="utf-8"))
    folder = ROOT / "storage/s3/corpora" / config["corpus_snapshot_id"] / "rebuild01"
    if folder.exists():
        raise FileExistsError("重建尝试已存在，不覆盖")
    ingestor = PDFPageIngestor(ROOT, folder / "snapshots", snapshot_prefix="s3rebuild")
    checks = []
    for entry in registry["documents"]:
        result = ingestor.import_document(entry["manifest"])
        matches = result.pages_sha256 == entry["snapshot"]["pages_sha256"]
        checks.append({"document_id": result.document_id, "pages_match": matches})
        if not matches:
            raise ValueError("从原PDF重提取的页文件不一致")
        entry["snapshot"] = dict(snapshot_id=result.snapshot_id, document_id=result.document_id, page_count=result.page_count,
                                 pages_sha256=result.pages_sha256, pages_path=result.pages_path.relative_to(ROOT).as_posix(),
                                 snapshot_manifest_path=result.snapshot_manifest_path.relative_to(ROOT).as_posix())
        print(json.dumps(checks[-1]), flush=True)
    write_json(folder / "ready.json", registry)
    corpus = ResearchCorpus(ROOT, registry)
    chunks = chunk_pages(corpus, config)
    index_path = ROOT / "indexes/s3/bge-rebuild01"
    index_path.mkdir(parents=True, exist_ok=False)
    embedding = LocalEmbedding(ROOT, config)
    index = VectorIndex(index_path, embedding, chunks, config, build=True)
    index.close()
    old_lock = json.loads((ROOT / "indexes/s3/bge-v1-attempt01/index.lock.json").read_text(encoding="utf-8"))
    new_lock = json.loads((index_path / "index.lock.json").read_text(encoding="utf-8"))
    write_json(folder / "rebuild_checks.json", {"documents": checks,
        "same_chunks": old_lock["chunk_payload_sha256"] == new_lock["chunk_payload_sha256"],
        "same_vectors": old_lock["vectors_sha256"] == new_lock["vectors_sha256"],
        "source_corpus_sha256": file_sha256(original), "rebuild_corpus_sha256": file_sha256(folder / "ready.json")})
    print("REBUILT", flush=True)


if __name__ == "__main__":
    main()
