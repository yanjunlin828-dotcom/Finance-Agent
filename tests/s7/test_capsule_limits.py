"""S7 source-preserving capsule regression at the configured size boundary."""
import json
import pytest
from finresearch.contracts.supplement import SupplementGap
from finresearch.workflow.supplement_session import supplement_capsule, supplement_followup
from finresearch.workflow.session_context import ContextTooLarge
from s6.test_session_context import state_fixture


def test_gap_defaults_roundtrip_without_losing_text_limits_or_source():
    state,ctx=state_fixture()
    gap=SupplementGap(gap_id='independent-review',company_id='002371.SZ',gap_type='INDEPENDENT_CONFIRMATION',
        severity='MATERIAL',description='公司否认该原因，需独立证据核对。',closing_condition='INDEPENDENT_REVIEW',
        status='LIMITED',resolution_note='原始反证必须保留',attempted_action_ids=['attempt-1'])
    action={'action_id':'attempt-1','gap_id':gap.gap_id,'tool':'SEARCH_DISCLOSURE','company_id':'002371.SZ',
        'as_of_date':ctx.as_of_date.isoformat(),'corpus_snapshot_id':ctx.corpus_snapshot_id,'arguments':{'question':'原文是否否认？'},'reason':'保留反证'}
    state['supplement']={'gaps':[gap.model_dump(mode='json')], 'history':[{'action':action}], 'rounds':1,'action_count':1}
    cap=supplement_capsule(state,ctx,{},1000000)
    compact=cap['payload']['supplement']['gaps'][0]
    assert 'metric_id' not in compact
    assert SupplementGap.model_validate(compact).model_dump(mode='json')==gap.model_dump(mode='json')
    assert cap['payload']['observations']==state['observations']
    assert cap['utf8_bytes']==len(json.dumps(cap['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    answer=supplement_followup(cap,ctx,'全部','all')
    assert answer['supplement']['gaps'][0]==gap.model_dump(mode='json')
    assert answer['supplement']['history'][0]['action']==action
    assert supplement_capsule(state,ctx,{},cap['utf8_bytes'])['payload_sha256']==cap['payload_sha256']
    with pytest.raises(ContextTooLarge):supplement_capsule(state,ctx,{},cap['utf8_bytes']-1)
