import pytest
from finresearch.retrieval.corpus import file_sha256
from finresearch_evals.gates import verify_artifact_set


@pytest.mark.parametrize('kind',['tamper','traversal','missing'])
def test_gate_rejects_artifact_corruption_and_outside_paths(tmp_path,kind):
    folder=tmp_path/'eval';folder.mkdir()
    file=folder/'summary.json';file.write_text('{}')
    publication={'files':{'summary.json':file_sha256(file)}}
    verify_artifact_set(folder,publication)
    if kind=='tamper':file.write_text('{"forged":true}')
    elif kind=='traversal':publication={'files':{'../outside.json':'x'}}
    else:file.unlink()
    with pytest.raises(ValueError):verify_artifact_set(folder,publication)
