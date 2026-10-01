from types import SimpleNamespace
import uuid
import numpy as np
import pytest
from qdrant_client import QdrantClient, models
from finresearch.retrieval.vector_index import VectorIndex
from finresearch.retrieval.corpus import chunk_pages
from test_retrieval_contract import sample_corpus, config, query


class Encoder:
    config = {"embedding_dimension": 4}
    model_lock = {"test_encoder": "offline-not-live-model"}
    def embed(self, texts):
        return np.asarray([[1, 2, 3, 4]] * len(texts), dtype=np.float32)
    def query(self, text):
        return [1, 2, 3, 4]


def setup_index(tmp_path):
    chunks = chunk_pages(sample_corpus(), config())
    cfg = {"embedding_dimension": 4}
    index = VectorIndex(tmp_path, Encoder(), chunks, cfg, build=True)
    return index, chunks, cfg


def test_closed_index_reopens_with_same_rank(tmp_path):
    index, chunks, cfg = setup_index(tmp_path)
    first = index.search(query(), {"doc-test"}, 20)
    index.close()
    reopened = VectorIndex(tmp_path, Encoder(), chunks, cfg)
    try:
        after = reopened.search(query(), {"doc-test"}, 20)
        assert [i for i, _ in after] == [i for i, _ in first]
        assert np.allclose([s for _, s in after], [s for _, s in first], rtol=1e-6, atol=1e-6)
    finally:
        reopened.close()


@pytest.mark.parametrize("tamper", ["payload", "vector", "identity"])
def test_database_tampering_rejected_before_ranking(tmp_path, tamper):
    index, chunks, cfg = setup_index(tmp_path)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunks[0].chunk_id))
    if tamper == "payload":
        index.client.set_payload(index.collection, {"company_id": "688072.SH"}, [point_id])
    elif tamper == "vector":
        index.client.update_vectors(index.collection, [models.PointVectors(id=point_id, vector=[4, 3, 2, 1])])
    else:
        point = index.client.retrieve(index.collection, [point_id], with_vectors=True)[0]
        index.client.delete(index.collection, models.PointIdsList(points=[point_id]))
        index.client.upsert(index.collection, [models.PointStruct(id=str(uuid.uuid4()), payload=point.payload, vector=point.vector)])
    index.close()
    with pytest.raises(ValueError, match="索引"):
        VectorIndex(tmp_path, Encoder(), chunks, cfg)
