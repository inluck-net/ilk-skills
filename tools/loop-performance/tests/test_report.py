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
