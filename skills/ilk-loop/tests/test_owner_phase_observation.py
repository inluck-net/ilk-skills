"""Unknown/overlapping phase clocks and repeat candidates cannot invent facts."""
from phase_observation import observe,interval


def test_missing_and_invalid_intervals_stay_unknown():
    for start,end in [(None,None),('',3),(4,2),(True,3),(float('nan'),3)]:
        result=interval('unknown',start,end)
        assert result['duration_sec'] is None and not result['observation_complete']
    assert observe({}, {})['identical_driver_gate_count'] is None


def test_overlapping_intervals_are_recorded_without_inventing_a_total():
    result=observe({'attempts':[{'started_monotonic':2,'ended_monotonic':5}]},
        {'integrity':[3,6],'cleanup':[5,6]})
    assert [row['duration_sec'] for row in result['intervals']]==[3,3,1]
    assert 'total_seconds' not in result and result['identical_driver_gate_count']==0


def test_same_command_tree_candidates_do_not_claim_identical_inputs():
    attempt={'working_tree':'same','check_observations':[{'command':'test whole-command'}]}
    result=observe({'attempts':[attempt,attempt]}, {},cwd='/fixture')
    assert result['command_tree_repeat_candidates']==1 and result['identical_driver_gate_count'] is None
    later=dict(attempt,working_tree='changed')
    result=observe({'attempts':[attempt,later]}, {},cwd='/fixture')
    assert result['command_tree_repeat_candidates']==0 and result['identical_driver_gate_count']==0
