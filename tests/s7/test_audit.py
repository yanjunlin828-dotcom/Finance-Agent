from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace
import pytest
from finresearch_evals.audit import freeze,validate_freeze,reviewer_packet,validate_human_review
from finresearch_evals.runner import public_b0_payload,finalize


def test_manifest_detects_source_and_label_drift(tmp_path):
    path=tmp_path/'evals/s7/gold.json';path.parent.mkdir(parents=True);path.write_text('{"value":1}')
    folder=tmp_path/'runs/eval';folder.mkdir(parents=True)
    contract={'dataset_role':'DEVELOPMENT','scope':{'company_ids':['002371.SZ']}}
    manifest=freeze(tmp_path,folder,{},contract,'OFFLINE')
    validate_freeze(tmp_path,manifest)
    path.write_text('{"value":2}')
    with pytest.raises(ValueError):validate_freeze(tmp_path,manifest)


def test_ai_review_cannot_close_human_gate():
    scores={'B1':{'semantic_review':{'items':[{'item_id':'q','text':'company quote','evidence':[],'decision':'PENDING','reviewer_type':None}]}}}
    packet=reviewer_packet(scores,{})
    submitted=deepcopy(packet)
    submitted['reviewer']={'type':'AI','name':'model','reviewed_at':'2026-10-01'}
    submitted['items'][0].update(decision='SUPPORTED_WITH_LIMITS',reason='exact quote')
    assert not validate_human_review(packet,submitted)
    submitted['reviewer']['type']='INDEPENDENT_HUMAN'
    assert validate_human_review(packet,submitted)
    submitted['items'][0]['item_sha256']='forged'
    assert not validate_human_review(packet,submitted)


def test_empty_or_incomplete_review_fails():
    assert not validate_human_review({'items':[]},{'items':[],'reviewer':{'type':'INDEPENDENT_HUMAN','name':'x','reviewed_at':'now'}})


def test_public_prompt_has_no_gold_values_or_labels():
    context=SimpleNamespace(as_of_date=date(2025,4,30),fiscal_years=(2023,2024))
    payload=public_b0_payload('002371.SZ',context,[])
    serialized=json.dumps(payload)
    for forbidden in ('gold','expected','29838069162','source_gold','financial_source_map','answer_labels'):
        assert forbidden not in serialized
    assert len(payload['formulas'])==8


def test_failed_branch_and_cumulative_parent_cost_retained(tmp_path):
    output=tmp_path/'eval';output.mkdir()
    manifest={'mode':'LIVE','dataset_role':'DEVELOPMENT','contract':{'budget_usd':'0.20'}}
    outcomes={'B0':{'status':'FAILED','calls':1,'estimated_cost_usd':'0.01'},'B1':{'status':'PARTIAL','calls':7,'estimated_cost_usd':'0.02','wall_seconds':10},
        'B2_LIVE':{'status':'PARTIAL','calls':4,'estimated_cost_usd':'0.005','wall_seconds':3}}
    score={'issues':[],'calculations':{'correct':24,'expected':24,'errors':[]},'observations':{'correct':18,'expected':18}}
    summary=finalize(tmp_path,output,manifest,outcomes,{'B1':score,'B2_LIVE':score})
    assert summary['failed_or_waiting_branches']==['B0']
    assert summary['new_model_calls_total']==12
    assert Decimal(summary['new_estimated_cost_usd_total'])==Decimal('0.035')
    assert outcomes['B2_LIVE']['cumulative_with_parent_wall_seconds']==13
    assert Decimal(outcomes['B2_LIVE']['cumulative_with_parent_estimated_cost_usd'])==Decimal('0.025')
    assert summary['full_release_gate'].startswith('NO_GO')
    assert summary['measurement_gate']=='OFFLINE_ONLY_OR_INCOMPLETE'
