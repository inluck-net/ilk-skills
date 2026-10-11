"""Unknown blame does not establish a green gate or excuse a red shipment."""
import pytest
from ship_integrity import evaluate_ship,check_final_step_gate

@pytest.mark.parametrize('attribution',[{'verdict':'unmeasured'},{'verdict':'owned','owner_slug':None}])
def test_unknown_attribution_cannot_excuse_a_raw_red(attribution):
    result=evaluate_ship('shipped',[{'command':'focused gate'}],
        dict(all_passed=False,results=[dict(command='focused gate',passed=False,exit_code=1)],attribution=attribution),gate_slug='fixture')
    assert not result.ok


def test_unmeasured_node_attribution_is_not_a_final_pass():
    row=dict(slug='fixture',step=0,outcome='fail',
        attribution=dict(verdict='unmeasured',nodes=[dict(verdict='unmeasured')]))
    assert not check_final_step_gate([row],'fixture',0,None,dispatched=True)


def test_unmeasured_node_cannot_hide_under_preexisting_summary():
    row=dict(slug='fixture',step=0,outcome='fail',
        attribution=dict(verdict='pre-existing',nodes=[dict(verdict='pre-existing'),dict(verdict='unmeasured')]))
    assert not check_final_step_gate([row],'fixture',0,None,dispatched=True)


def test_unknown_node_cannot_excuse_integrity_red():
    attr=dict(verdict='pre-existing',nodes=[dict(verdict='unmeasured')])
    result=evaluate_ship('shipped',[dict(command='focused gate')],
        dict(all_passed=False,results=[dict(command='focused gate',passed=False,exit_code=1)],attribution=attr))
    assert not result.ok


def test_measured_green_does_not_require_ownership_attribution():
    result=evaluate_ship('shipped',[dict(command='focused gate')],
        dict(all_passed=True,results=[dict(command='focused gate',passed=True,exit_code=0)],
             attribution=dict(verdict='unmeasured')))
    assert result.ok and 'gate green' in result.reason


@pytest.mark.parametrize('verdict',['inherited','pre-existing'])
def test_known_nonowned_red_keeps_legacy_integrity_honesty(verdict):
    attr=dict(verdict=verdict,nodes=[dict(verdict=verdict)])
    result=evaluate_ship('shipped',[dict(command='focused gate')],
        dict(all_passed=False,attribution=attr))
    assert result.ok and 'gate green' not in result.reason
