"""Bind immutable measurement and fresh engineering evidence without gate inflation."""
from pathlib import Path
from finresearch.retrieval.corpus import file_sha256
from finresearch.contracts import stable_sha256
from finresearch.storage.session_store import safe_task_id
from .audit import read_json,write_json,validate_freeze


def verify_artifact_set(folder: Path, publication: dict) -> None:
    """Verify all declared local artifacts; refuse traversal or missing files."""
    for relative,digest in publication['files'].items():
        path=(folder/relative).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file() or file_sha256(path)!=digest:
            raise ValueError('published evaluation artifact changed')


def bind_gate(root: Path, suite_id: str, repair_id: str, gate_id: str) -> dict:
    """Freeze a new decision; engineering GO is distinct from release NO_GO.

    Current development corpus has no independent holdout/human approval.
    Old results stay immutable; no caller flag can promote them to full release.
    """
    suite=root/'runs/s7'/safe_task_id(suite_id)
    repair=root/'runs/s7'/safe_task_id(repair_id)
    manifest=read_json(suite/'evaluation.lock.json')
    validate_freeze(root,manifest)
    publication=read_json(suite/'publication.json')
    verify_artifact_set(suite,publication)
    summary=read_json(suite/'summary.json')
    if stable_sha256(summary)!=publication['summary_sha256']:
        raise ValueError('evaluation summary hash changed')
    engineering=read_json(repair/'gate_report.json')
    for relative,digest in engineering['current_core_hashes'].items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or file_sha256(path)!=digest:
            raise ValueError('engineering qualification source drift')
    totals=engineering['test_totals']
    engineering_ok=(engineering['decision']=='GO' and engineering['all_checks_passed']
        and bool(engineering['checks']) and all(engineering['checks'].values())
        and totals['tests']>0 and all(totals[k]==0 for k in ('failures','errors','skipped')))
    allowed=(engineering_ok and summary['measurement_gate']=='PASS_WITH_LIMITATIONS'
        and summary['product_decision']=='LIMITED_RESEARCH_PROTOTYPE'
        and not summary['failed_or_waiting_branches'])
    result={'gate':'G7','decision':'GO_RESTRICTED_PROTOTYPE' if allowed else 'NO_GO',
        'engineering_gate':'GO' if engineering_ok else 'NO_GO', 'measurement_gate':summary['measurement_gate'],
        'full_release_gate':'NO_GO_INDEPENDENT_REVIEW_AND_HOLDOUT_MISSING',
        'default_workflow':'B1','b2_quality_advantage':'NOT_PROVEN','independent_holdout':False,
        'human_review_status':'PENDING', 's1_historical_obligations':'NOT_CLOSED',
        'test_totals':totals,'fault_counts':{'B1':engineering['b1_fault_count'],'B2':engineering['b2_fault_count']},
        'measurement_run':suite_id,'repair_run':repair_id,'summary':summary,
        'inputs':{p.relative_to(root).as_posix():file_sha256(p) for p in
            (suite/'evaluation.lock.json',suite/'publication.json',suite/'summary.json',repair/'gate_report.json')},
        'limits':['三公司两财年已见开发数据，不能声称独立泛化或语义准确率',
                  '费用为保守配置估算，不是供应商账单；历史未知另列',
                  '工程通过允许有限研究演示，不允许确认因果、投资决策或完整质量发布']}
    output=root/'runs/s7'/safe_task_id(gate_id)
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'gate_report.json',result)
    write_json(output/'publication.json',{'files':{'gate_report.json':file_sha256(output/'gate_report.json')}})
    return result
