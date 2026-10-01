from decimal import Decimal
import json
from pathlib import Path
import pytest
from finresearch.model.persistent_budget import PersistentBudgetGuard
from finresearch.model.probe_guard import ProbeBudgetExceeded


def config():
    config=json.loads((Path(__file__).resolve().parents[2]/"configs/s1/model.json").read_text(encoding="utf-8"))
    config.update(online_probe_ids=["call"],maximum_total_calls=2,maximum_attempts_per_probe=2,currency_limit="0.02")
    return config


def test_restart_and_second_instance_cannot_reset_budget(tmp_path):
    a=PersistentBudgetGuard(config(),tmp_path/"budget.sqlite")
    b=PersistentBudgetGuard(config(),tmp_path/"budget.sqlite")
    reservation=a.reserve_call("call",Decimal("0.01"))
    a.settle_call(reservation,None)
    second=b.reserve_call("call",Decimal("0.01"))
    b.settle_call(second,None)
    reopened=PersistentBudgetGuard(config(),tmp_path/"budget.sqlite")
    assert reopened.total_calls==2 and reopened.reserved_cost==Decimal("0.02")
    with pytest.raises(ProbeBudgetExceeded):
        reopened.reserve_call("call",Decimal("0.01"))


def test_known_usage_settles_and_config_cannot_change(tmp_path):
    guard=PersistentBudgetGuard(config(),tmp_path/"budget.sqlite")
    reservation=guard.reserve_call("call",Decimal("0.01"))
    guard.settle_call(reservation,Decimal("0.003"))
    assert PersistentBudgetGuard(config(),tmp_path/"budget.sqlite").reserved_cost==Decimal("0.003")
    changed=config(); changed["maximum_total_calls"]=10
    with pytest.raises(ValueError,match="配置"):
        PersistentBudgetGuard(changed,tmp_path/"budget.sqlite")


def test_parallel_instances_cannot_over_reserve(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/"budget.sqlite"
    def reserve(_):
        guard=PersistentBudgetGuard(config(),path)
        try:
            guard.reserve_call("call",Decimal("0.01"))
            return True
        except ProbeBudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes=list(pool.map(reserve,range(8)))
    assert sum(outcomes)==2
    saved=PersistentBudgetGuard(config(),path)
    assert saved.total_calls==2 and saved.reserved_cost==Decimal("0.02")
