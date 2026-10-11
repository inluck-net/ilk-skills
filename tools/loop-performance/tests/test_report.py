import importlib.util
import json
from pathlib import Path

MODULE = Path(__file__).parents[1] / 'report.py'
spec = importlib.util.spec_from_file_location('loop_cost_report', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_parallel_clocks_and_partial_turns_are_not_added(tmp_path):
    p = tmp_path / 'iter-01.log.jsonl'
    rows = [
        {'type':'system','subtype':'init','model':'actual-pro','session_id':'root'},
        {'type':'assistant','timestamp':'2026-10-11T00:00:00Z','message':{'id':'turn-1','content':[{'type':'tool_use','id':'t1','name':'Bash','input':{'command':'pytest targeted.py'}}]}},
        {'type':'assistant','timestamp':'2026-10-11T00:00:01Z','message':{'id':'turn-1','content':[{'type':'tool_use','id':'t2','name':'Bash','input':{'command':'pytest other.py'}}]}},
        {'type':'assistant','parent_tool_use_id':'agent','message':{'id':'child','content':[{'type':'tool_use','id':'child-test','name':'Bash','input':{'command':'pytest all'}}]}},
        {'type':'user','timestamp':'2026-10-11T00:00:05Z','message':{'content':[{'type':'tool_result','tool_use_id':'t1'}]}},
        {'type':'user','timestamp':'2026-10-11T00:00:07Z','message':{'content':[{'type':'tool_result','tool_use_id':'t2'}]}},
        {'type':'system','subtype':'compact_boundary','compact_metadata':{'duration_ms':3000}},
        {'type':'result','duration_ms':10000,'duration_api_ms':18000},
    ]
    p.write_text('\n'.join(json.dumps(r) for r in rows))
    p.with_suffix('').write_text('[23:59:59] [thinking] 99 chars, 5s...\n[00:00:02] [thinking] 100 chars in 8.0s\n')
    r = mod.report(p)
    assert r['models_actual_init'] == ['actual-pro']
    assert r['root_assistant_turns'] == 1
    assert r['root_assistant_events'] == 2
    assert r['compaction_seconds'] == 3
    assert r['root_tool_calls'] == 2
    assert r['foreground_test_seconds'] == 7
    assert r['worker_elapsed_seconds'] == 10
    assert r['api_seconds_overlapping'] == 18
    assert r['thinking_completed_seconds'] == 8
    assert r['renderer_observed_seconds'] == 3


def test_truncated_stream_preserves_unknowns(tmp_path):
    p = tmp_path / 'iter-01.log.jsonl'
    p.write_text(json.dumps({'type':'assistant','message':{'id':'a','content':[{'type':'tool_use','id':'t','name':'Bash','input':{'command':'pytest case'}}]}})+'\n{"truncated')
    r = mod.report(p)
    assert not r['completed_result']
    assert r['malformed_lines'] == 1
    assert r['worker_elapsed_seconds'] is None
    assert r['thinking_completed_seconds'] is None
    assert r['foreground_test_seconds'] is None
    assert r['foreground_test_unmatched_calls'] == 1


def test_resume_counts_both_invocations_and_an_unfinished_one_stays_unknown(tmp_path):
    p=tmp_path/'iter.log.jsonl'
    init={'type':'system','subtype':'init','session_id':'same','model':'actual'}
    rows=[init,{'type':'result','duration_ms':7000,'duration_api_ms':5000},
          init,{'type':'result','duration_ms':11000,'duration_api_ms':9000}]
    p.write_text('\n'.join(json.dumps(row) for row in rows))
    observed=mod.report(p)
    assert observed['worker_elapsed_seconds']==18 and observed['api_seconds_overlapping']==14
    assert observed['completed_worker_invocations']==2 and observed['sessions']==['same']
    p.write_text(p.read_text()+'\n'+json.dumps(init))
    assert mod.report(p)['worker_elapsed_seconds'] is None


def test_bootstrap_retries_and_children_have_explicit_denominators(tmp_path):
    p=tmp_path/'iter-01.log.jsonl'
    rows=[
      {'type':'assistant','timestamp':'2026-10-11T00:00:00Z','message':{'id':'m1','content':[{'type':'tool_use','id':'scan','name':'Read','input':{'file_path':'plans/selected.md'}}]}},
      {'type':'system','subtype':'api_retry','retry_delay_ms':536},
      {'type':'system','subtype':'api_retry','retry_delay_ms':1204},
      {'type':'assistant','timestamp':'2026-10-11T00:00:05Z','message':{'id':'m2','content':[{'type':'tool_use','id':'edit','name':'Edit','input':{'file_path':'code.py'}}]}},
      {'type':'assistant','message':{'id':'m3','content':[{'type':'tool_use','id':'after','name':'Read','input':{'file_path':'plans/selected.md'}}]}},
      {'type':'assistant','parent_tool_use_id':'agent','message':{'id':'child','content':[{'type':'tool_use','id':'child-read','name':'Read','input':{'file_path':'plans/all.md'}}]}},
    ]
    p.write_text('\n'.join(json.dumps(r) for r in rows))
    r=mod.report(p)
    assert r['bootstrap_path_indicator_calls']==2
    assert r['bootstrap_indicator_before_first_edit']==1
    assert r['root_tool_span_before_first_edit_seconds']==5
    assert r['child_tool_calls']==1
    assert r['child_assistant_events']==1
    assert r['explicit_api_retry_delay_seconds']==1.74
    assert r['explicit_api_retry_events']==2


def test_broker_paths_do_not_turn_every_shell_call_into_bootstrap(tmp_path):
    import shlex
    p=tmp_path/'iter.log.jsonl'
    commands=['cat plans/selected.md','printf changed > code.py','pytest targeted.py']
    rows=[]
    for i,command in enumerate(commands):
        wrapped=shlex.join(['/usr/bin/python3','/private/.ilk-data/inputs/code/worker_shell.py',
            '--context','/private/.ilk-data/logs/ctx.json','--command',command])
        rows.append({'type':'assistant','message':{'id':str(i),'content':[
            {'type':'tool_use','id':str(i),'name':'Bash','input':{'command':wrapped}}]}})
    p.write_text('\n'.join(json.dumps(row) for row in rows))
    report=mod.report(p)
    assert report['bootstrap_path_indicator_calls']==1
    assert report['foreground_test_calls']==1
    assert not report['first_edit_observed']
    assert 'Bash edits are not inferred' in report['first_edit_observation_basis']
