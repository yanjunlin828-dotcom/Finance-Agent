"""Local ONNX embeddings and a locked, persistent Qdrant collection."""
from __future__ import annotations
import hashlib
import json
import uuid
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models
from tokenizers import Tokenizer

from finresearch.contracts import stable_sha256
from finresearch.contracts.retrieval import RetrievalChunk, RetrievalQuery
from .corpus import file_sha256, write_json


class LocalEmbedding:
    """Encode public text locally and freeze the actual downloaded model bytes."""
    def __init__(self, root: Path, config: dict) -> None:
        self.config = config
        self.encoder = TextEmbedding(config["embedding_model"], cache_dir=str(root / "storage/s3/models"),
                                     threads=2, providers=["CPUExecutionProvider"], local_files_only=True)
        # FastEmbed 0.8.0 exposes this model path; locked dependency contract.
        directory = Path(self.encoder.model._model_dir)
        self.model_lock = {"model_name": config["embedding_model"], "dimension": config["embedding_dimension"],
                           "revision": directory.name, "query_mode": "query_embed", "document_mode": "embed",
                           "files": {p.relative_to(directory).as_posix(): file_sha256(p) for p in sorted(directory.rglob("*")) if p.is_file()}}
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

    def token_lengths(self, texts: list[str]) -> list[int]:
        return [len(item.ids) for item in self.tokenizer.encode_batch(texts)]

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts or max(self.token_lengths(texts)) > 512:
            raise ValueError("Embedding文本为空或超过512 token，拒绝截断")
        result = np.asarray(list(self.encoder.embed(texts, batch_size=32)), dtype=np.float32)
        self._validate(result)
        return result

    def query(self, text: str) -> list[float]:
        if self.token_lengths([text])[0] > 512:
            raise ValueError("Embedding查询超过512 token")
        result = np.asarray(list(self.encoder.query_embed(text)), dtype=np.float32)
        self._validate(result)
        return result[0].tolist()

    def _validate(self, vectors: np.ndarray) -> None:
        if vectors.ndim != 2 or vectors.shape[1] != self.config["embedding_dimension"] or not np.isfinite(vectors).all():
            raise ValueError("Embedding维度或数值无效")
        if np.any(np.linalg.norm(vectors, axis=1) < 1e-8):
            raise ValueError("Embedding返回零向量")


class VectorIndex:
    """Index payload never becomes evidence; hits resolve into trusted chunks."""
    collection = "research_chunks"

    def __init__(self, path: Path, embedding: LocalEmbedding, chunks: list[RetrievalChunk], config: dict, *, build: bool = False) -> None:
        self.path = path
        self.embedding = embedding
        self.by_id = {c.chunk_id: c for c in chunks}
        if len(self.by_id) != len(chunks):
            raise ValueError("块ID重复")
        expected = {"schema_version": "1.0.0", "model_lock": embedding.model_lock,
                    "chunk_payload_sha256": stable_sha256([c.model_dump(mode="json") for c in chunks]),
                    "config_sha256": stable_sha256(config), "chunk_count": len(chunks),
                    "collection": self.collection, "distance": "Cosine"}
        manifest_path = path / "index.lock.json"
        self.client = QdrantClient(path=str(path / "qdrant"))
        try:
            if build:
                if manifest_path.exists() or self.client.collection_exists(self.collection):
                    raise FileExistsError("索引存在；使用新路径重建，不覆盖旧索引")
                print(f"Embedding {len(chunks)} chunks", flush=True)
                vectors = embedding.embed([c.text for c in chunks])
                np.save(path / "vectors.npy", vectors, allow_pickle=False)
                self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=config["embedding_dimension"], distance=models.Distance.COSINE))
                for start in range(0, len(chunks), 128):
                    points = []
                    for index in range(start, min(start + 128, len(chunks))):
                        chunk = chunks[index]
                        points.append(models.PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                                                        vector=vectors[index].tolist(), payload={
                            "chunk_id": chunk.chunk_id, "company_id": chunk.company_id,
                            "reporting_year": chunk.reporting_year, "published_on": chunk.published_on.isoformat(),
                            "corpus_snapshot_id": chunk.corpus_snapshot_id, "document_status": chunk.document_status,
                            "document_type": chunk.document_type,
                        }))
                    self.client.upsert(self.collection, points=points, wait=True)
                expected["vectors_sha256"] = file_sha256(path / "vectors.npy")
                write_json(manifest_path, expected)
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            if any(saved.get(k) != v for k, v in expected.items()):
                raise ValueError("索引输入、模型或配置与当前请求不一致")
            if file_sha256(path / "vectors.npy") != saved["vectors_sha256"]:
                raise ValueError("保存向量摘要不一致")
            if self.client.count(self.collection, exact=True).count != len(chunks):
                raise ValueError("索引点集合不完整")
            params = self.client.get_collection(self.collection).config.params.vectors
            if params.size != config["embedding_dimension"] or params.distance != models.Distance.COSINE:
                raise ValueError("索引集合维度或距离与配置不一致")
            self._verify_points(chunks, np.load(path / "vectors.npy", allow_pickle=False))
            self.lock = saved
        except Exception:
            self.client.close()
            raise

    def _verify_points(self, chunks, vectors) -> None:
        """Check the database itself, not only the separate vector file hash.

        Local Qdrant may return normalized vectors in memory and original
        vectors after reopening. Verify their cosine direction in either case
        with float32 tolerance; magnitude does not affect cosine ranking.
        """
        if vectors.shape != (len(chunks), self.embedding.config["embedding_dimension"]) or not np.isfinite(vectors).all():
            raise ValueError("持久化向量形状或数值无效")
        norms = np.linalg.norm(vectors, axis=1)
        if np.any(norms < 1e-8):
            raise ValueError("持久化向量包含零向量")
        expected = {str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)): (c, vectors[i] / norms[i]) for i, c in enumerate(chunks)}
        offset = None
        seen = set()
        while True:
            points, offset = self.client.scroll(self.collection, limit=128, offset=offset, with_payload=True, with_vectors=True)
            for point in points:
                identity = str(point.id)
                if identity not in expected or identity in seen:
                    raise ValueError("索引点身份集合不一致")
                seen.add(identity)
                chunk, vector = expected[identity]
                payload = {"chunk_id": chunk.chunk_id, "company_id": chunk.company_id,
                    "reporting_year": chunk.reporting_year, "published_on": chunk.published_on.isoformat(),
                    "corpus_snapshot_id": chunk.corpus_snapshot_id, "document_status": chunk.document_status,
                    "document_type": chunk.document_type}
                actual = np.asarray(point.vector, dtype=np.float32)
                actual_norm = np.linalg.norm(actual)
                if (actual.shape != vector.shape or not np.isfinite(actual).all() or actual_norm < 1e-8
                    or point.payload != payload or not np.allclose(actual / actual_norm, vector, rtol=1e-5, atol=1e-6)):
                    raise ValueError("索引载荷或向量与锁定块不一致")
            if offset is None:
                break
        if set(expected) != seen:
            raise ValueError("索引点身份集合不完整")

    def search(self, query: RetrievalQuery, eligible_ids: set[str], limit: int) -> list[tuple[str, float]]:
        matches = [models.FieldCondition(key=key, match=models.MatchValue(value=value)) for key, value in {
            "company_id": query.company_id, "reporting_year": query.reporting_year,
            "corpus_snapshot_id": query.corpus_snapshot_id, "document_status": "READY", "document_type": query.document_type,
        }.items()]
        matches.append(models.HasIdCondition(has_id=[str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)) for c in self.by_id.values() if c.document_id in eligible_ids and c.published_on <= query.as_of_date]))
        response = self.client.query_points(self.collection, query=self.embedding.query(query.question),
                                            query_filter=models.Filter(must=matches), limit=limit, with_payload=True)
        hits = []
        for point in response.points:
            chunk_id = point.payload["chunk_id"]
            chunk = self.by_id.get(chunk_id)
            if chunk is None or chunk.document_id not in eligible_ids or chunk.published_on > query.as_of_date:
                raise ValueError("向量结果违反锁定语料过滤")
            if str(point.id) != str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)):
                raise ValueError("向量点身份与块不一致")
            hits.append((chunk_id, float(point.score)))
        return hits

    def close(self) -> None:
        self.client.close()
