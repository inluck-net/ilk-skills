#!/usr/bin/env python3
"""Observation-only worker costs; API/parallel clocks are never subtracted.

Input: iter-NN.log.jsonl (CLI stream) and sibling iter-NN.log (renderer).
Thinking time is completed renderer blocks, not progress or thinking tokens.
Test intervals use root foreground tool_use -> matching tool_result wall stamps.
Missing clocks are null. Bootstrap is a path-pattern indicator, not waste. Turns use unique message IDs;
root_assistant_events also records split message events for historical comparison.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shlex
from datetime import datetime
from pathlib import Path

PARSER_VERSION = 5
BOOTSTRAP = re.compile(r'\.claude[^/\s]*/|\.ilk-data|\.ilk/|plans/|MASTER-|loop_status|find ~')
TEST = re.compile(r'\b(?:pytest|unittest|vitest|jest|playwright)\b|\bnpm\s+(?:run\s+)?test\b')
THINK = re.compile(r'\[thinking\] \d+ chars in ([\d.]+)s')
STAMP = re.compile(r'^\[(\d\d):(\d\d):(\d\d)\]')


def observed_input(tool):
    """Exclude broker infrastructure paths from command-content indicators.

    Parse the known six-argument envelope only; never execute shell input.
    Malformed/unknown wrappers retain the original observation unchanged.
    """
    payload=tool.get('input',{})
    if tool.get('name')!='Bash':return payload
    raw=payload.get('command','')
    try:tokens=shlex.split(raw)
    except (TypeError,ValueError):return payload
    if len(tokens)==6 and Path(tokens[1]).name=='worker_shell.py' and tokens[2]=='--context' and tokens[4]=='--command':
        return dict(payload,command=tokens[5])
    return payload


def bootstrap_input(tool,exec_tree=None):
    # An edit is a write observation, not bootstrap discovery. Strip only an
    # explicitly supplied execution-root prefix; a repo living under .ilk-data
    # does not turn every native read into toolkit/bootstrap work.
    if tool.get('name') in {'Edit','Write','MultiEdit','NotebookEdit'}:return ''
    prefix=str(Path(exec_tree).resolve()).rstrip('/')+'/' if exec_tree is not None else None
    def relative(value):
        if isinstance(value,str):return value.replace(prefix,'') if prefix else value
        if isinstance(value,dict):return {key:relative(item) for key,item in value.items()}
        if isinstance(value,list):return [relative(item) for item in value]
        return value
    return json.dumps(relative(observed_input(tool)))


def report(path: Path, *, exec_tree=None) -> dict:
    rendered = Path(str(path).removesuffix('.jsonl'))
    text = rendered.read_text(errors='replace') if rendered.exists() else None
    thoughts = [float(x) for x in THINK.findall(text or '')]
    clocks = []
    pending_thought = False
    for line in (text or '').splitlines():
        stamp = STAMP.match(line)
        if stamp:
            t = int(stamp[1])*3600 + int(stamp[2])*60 + int(stamp[3])
            if clocks and t < clocks[-1] % 86400:
                t += (clocks[-1] // 86400 + 1)*86400
            elif clocks:
                t += clocks[-1] // 86400 * 86400
            clocks.append(t)
        if '[thinking]' in line:
            pending_thought = not bool(THINK.search(line))
    models, sessions, assistant_ids, tools = set(), set(), set(), {}
    results, malformed, root_messages, assistant_events = [], 0, 0, 0
    root_initializations=0
    starts, ends, compactions, compaction_durations = {}, {}, 0, []
    child_tools, child_messages, retry_delays = set(), 0, []
    root_tool_order = []
    def stamp(row):
        try:
            return datetime.fromisoformat(row.get("timestamp", "").replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return None
    for line in path.read_text(errors='replace').splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue
        # Agent task streams are not the root worker's turns/tools.
        if row.get('parent_tool_use_id'):
            if row.get('type') == 'assistant':
                child_messages += 1
                for block in (row.get('message') or {}).get('content', []):
                    if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('id'):
                        child_tools.add(block['id'])
            continue
        if row.get('type') == 'system' and row.get('subtype') == 'api_retry':
            delay = row.get('retry_delay_ms')
            if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay >= 0:
                retry_delays.append(delay/1000)
        if row.get('type') == 'system' and row.get('subtype') == 'init':
            root_initializations+=1
            if row.get('model'):
                models.add(row['model'])
            if row.get('session_id'):
                sessions.add(row['session_id'])
        if row.get('type') == 'system' and row.get('subtype') == 'compact_boundary':
            compactions += 1
            duration = (row.get("compact_metadata") or {}).get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                compaction_durations.append(duration/1000)
        if row.get('type') == 'user':
            for block in (row.get('message') or {}).get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    ends[block.get('tool_use_id')] = stamp(row)
        if row.get('type') == 'assistant':
            assistant_events += 1
            message = row.get('message') or {}
            identity = message.get('id') or row.get('uuid')
            if identity:
                assistant_ids.add(identity)
            else:
                root_messages += 1
            for block in message.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    if block['id'] not in tools:
                        root_tool_order.append(block['id'])
                    tools.setdefault(block['id'], block)
                    starts.setdefault(block['id'], stamp(row))
        if row.get('type') == 'result':
            results.append(row)
    def invocation_seconds(field):
        values=[row.get(field) for row in results]
        valid=[value for value in values if isinstance(value,(int,float)) and not isinstance(value,bool)
               and math.isfinite(value) and value>=0]
        complete=bool(results) and len(valid)==len(values) and len(results)>=root_initializations
        return sum(valid)/1000 if complete else None
    bootstrap_raw = sum(bool(BOOTSTRAP.search(json.dumps(observed_input(tool)))) for tool in tools.values())
    bootstrap_ids = {identity for identity, tool in tools.items()
                     if BOOTSTRAP.search(bootstrap_input(tool,exec_tree))}
    bootstrap = len(bootstrap_ids)
    first_edit = next((identity for identity in root_tool_order
                       if tools[identity].get('name') in {'Edit', 'Write', 'MultiEdit'}), None)
    preceding = root_tool_order[:root_tool_order.index(first_edit)] if first_edit else root_tool_order
    before_edit = len(bootstrap_ids.intersection(preceding))
    first_stamp = next((starts[x] for x in root_tool_order if starts.get(x) is not None), None)
    edit_stamp = starts.get(first_edit)
    tests = [t for t in tools.values() if t.get('name') == 'Bash' and
             TEST.search(str(observed_input(t).get('command', ''))) and
             not t.get('input', {}).get('run_in_background')]
    intervals = []
    unmatched = 0
    for tool in tests:
        begin, end = starts.get(tool['id']), ends.get(tool['id'])
        if begin is None or end is None or end < begin:
            unmatched += 1
        else:
            intervals.append((begin, end))
    merged = []
    for begin, end in sorted(intervals):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    test_seconds = round(sum(end-begin for begin, end in merged), 3)
    return {
        'parser_version': PARSER_VERSION, 'stream': str(path),
        'models_actual_init': sorted(models), 'sessions': sorted(sessions),
        'root_assistant_turns': len(assistant_ids) + root_messages,
        'root_assistant_events': assistant_events,
        'turn_identity_missing': root_messages,
        'root_tool_calls': len(tools), 'bootstrap_path_indicator_calls': bootstrap,
        'bootstrap_path_indicator_calls_raw':bootstrap_raw,
        'bootstrap_indicator_basis':'read/command path-pattern after broker-envelope removal and optional explicit execution-root prefix removal; not a waste verdict',
        'bootstrap_execution_root':str(Path(exec_tree).resolve()) if exec_tree is not None else None,
        'bootstrap_indicator_before_first_edit': before_edit,
        'first_edit_observed': first_edit is not None,
        'first_edit_observation_basis': 'native Edit/Write/MultiEdit tool use only; Bash edits are not inferred',
        'root_tool_span_before_first_edit_seconds': round(edit_stamp-first_stamp,3) if edit_stamp is not None and first_stamp is not None and edit_stamp >= first_stamp else None,
        'child_tool_calls': len(child_tools), 'child_assistant_events': child_messages,
        'explicit_api_retry_events': len(retry_delays),
        'explicit_api_retry_delay_seconds': round(sum(retry_delays),3),
        'foreground_test_calls': len(tests),
        'foreground_test_seconds': test_seconds if not unmatched else None,
        'foreground_test_matched_seconds_lower_bound': test_seconds,
        'foreground_test_unmatched_calls': unmatched,
        'thinking_completed_blocks': len(thoughts) if thoughts or '[thinking]' in (text or '') else None,
        'thinking_completed_seconds': round(sum(thoughts), 1) if thoughts or '[thinking]' in (text or '') else None,
        'thinking_progress_at_tail': pending_thought if '[thinking]' in (text or '') else None,
        'renderer_observed_seconds': clocks[-1]-clocks[0] if len(clocks) > 1 else None,
        'worker_elapsed_seconds': invocation_seconds('duration_ms'),
        'api_seconds_overlapping': invocation_seconds('duration_api_ms'),
        'completed_worker_invocations': len(results),
        'root_initializations': root_initializations,
        'worker_elapsed_basis': 'sum of completed CLI invocation result.duration_ms; missing invocation/capture is null',
        'completed_result': bool(results), 'malformed_lines': malformed,
        'compaction_events': compactions,
        'compaction_seconds': round(sum(compaction_durations), 3) if len(compaction_durations) == compactions else None,
        'limits': ['Thinking excludes unfinished blocks; renderer time includes output transit.',
                   'API, thinking and background-agent clocks overlap; do not sum or subtract.',
                   'Sequential resume invocations are summed within each clock; driver gate/commit time is recorded separately.',
                   'Bootstrap paths and foreground test calls are indicators, not wasted-time attribution.',
                   'Known broker envelopes are removed from path/command indicators; Bash-only edits leave native first-edit observation false.',
                   'Test seconds are the union of timestamped foreground tool intervals, including shell overhead; missing intervals remain null.'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('streams', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--exec-tree',type=Path)
    args = parser.parse_args()
    payload = json.dumps([report(p,exec_tree=args.exec_tree) for p in args.streams], indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
