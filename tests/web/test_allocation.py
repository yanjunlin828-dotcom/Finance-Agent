"""Grant accounting fixtures never launch a worker or transmit model requests."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from finresearch_web import service as module


@pytest.fixture
def accounting(monkeypatch):
    service = module.Service.__new__(module.Service)
    service.root = Path('.')
    monkeypatch.setattr(service, 'active', lambda *_: False)
    row = {'reservation': '0.10', 'phase': 'STOPPED', 'error': None, 'cancel': 0, 'task_id': 'closed'}
    core = {'actions': [('writer', 'DONE', 1)], 'events': [{'payload': {'event': 'MODEL_ATTEMPT', 'audit': {
        'status': 'PARSED', 'usage': {'model_input_tokens': 1000, 'model_output_tokens': 500}, 'estimated_cost_usd': '0.001'}}}]}
    budget = {'pending_reservation': '0', 'settled_estimate': '0.001', 'total_calls': 1}
    pub = {'result_available': True}
    monkeypatch.setattr(module, 'read_core', lambda *_: core)
    monkeypatch.setattr(module, 'budget_view', lambda *_: budget)
    monkeypatch.setattr(module, 'publication', lambda *_: (pub, None, None))
    return service, row, core, budget, pub


def test_verified_terminal_usage_releases_unused_cap_without_rewriting_records(accounting):
    service, row, core, budget, pub = accounting
    before = deepcopy((row, core, budget, pub))
    assert service.allocation(row) == Decimal('0.001')
    assert (row, core, budget, pub) == before


@pytest.mark.parametrize('field,value', [('phase', 'DISPATCHED'), ('error', 'Timeout'), ('cancel', 1)])
def test_uncertain_worker_keeps_original_cap(accounting, field, value):
    service, row, *_ = accounting
    row[field] = value
    assert service.allocation(row) == Decimal('0.10')


@pytest.mark.parametrize('case', ['active', 'unpublished', 'unknown_action', 'pending', 'missing_usage',
    'unparsed', 'count_mismatch', 'cost_mismatch', 'over_cap', 'nonfinite', 'read_error'])
def test_incomplete_or_inconsistent_accounting_never_releases_authorization(accounting, monkeypatch, case):
    service, row, core, budget, pub = accounting
    audit = core['events'][0]['payload']['audit']
    if case == 'active': monkeypatch.setattr(service, 'active', lambda *_: True)
    if case == 'unpublished': pub['result_available'] = False
    if case == 'unknown_action': core['actions'][0] = ('writer', 'UNKNOWN', 1)
    if case == 'pending': budget['pending_reservation'] = '0.001'
    if case == 'missing_usage': audit['usage']['model_output_tokens'] = None
    if case == 'unparsed': audit['status'] = 'FAILED'
    if case == 'count_mismatch': budget['total_calls'] = 2
    if case == 'cost_mismatch': budget['settled_estimate'] = '0.002'
    if case == 'over_cap': budget['settled_estimate'] = '0.2'
    if case == 'nonfinite': budget['settled_estimate'] = 'NaN'
    if case == 'read_error': monkeypatch.setattr(module, 'read_core', lambda *_: (_ for _ in ()).throw(OSError()))
    assert service.allocation(row) == Decimal('0.10')
