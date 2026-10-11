"""Additive observed intervals; no gate authority or timeout decisions."""
from __future__ import annotations
import hashlib
import json
import math
from collections import Counter


def interval(name,start,end,**identity):
    def number(value):
        try:return float(value) if not isinstance(value,bool) else None
        except (TypeError,ValueError):return None
    start=number(start);end=number(end)
    valid=start is not None and end is not None and math.isfinite(start) and math.isfinite(end) and end>=start
    return dict(name=name,clock_basis='shared-host-monotonic',started_monotonic=start if valid else None,
        ended_monotonic=end if valid else None,duration_sec=end-start if valid else None,
        observation_complete=valid,**identity)


def observe(acceptance,boundaries,*,cwd=None):
    acceptance=acceptance or {};spans=[];candidates=Counter()
    for attempt in acceptance.get('attempts') or []:
        spans.append(interval('driver_gate',attempt.get('started_monotonic'),attempt.get('ended_monotonic'),
            attempt_id=attempt.get('attempt_id')))
        for check in attempt.get('check_observations') or []:
            if not check.get('command') or not attempt.get('working_tree'):continue
            key=json.dumps([check['command'],cwd,attempt['working_tree']],sort_keys=True)
            candidates[hashlib.sha256(key.encode()).hexdigest()]+=1
    attribution=acceptance.get('attribution_attempts')
    if attribution is None:
        attribution=[acceptance['progress_observation']] if isinstance(acceptance.get('progress_observation'),dict) else []
    for index,decision in enumerate(attribution if isinstance(attribution,list) else []):
        if not isinstance(decision,dict):continue
        spans.append(interval('driver_attribution',decision.get('attribution_started_monotonic'),
            decision.get('attribution_ended_monotonic'),attribution_attempt=index))
        for observation in decision.get('observations') or []:
            if not isinstance(observation,dict):continue
            spans.append(interval('driver_base_sample',observation.get('sample_started_monotonic'),
                observation.get('sample_ended_monotonic'),attribution_attempt=index,
                scope=observation.get('scope'),receipt=observation.get('receipt')))
    if 'commit_started_monotonic' in acceptance:
        spans.append(interval('driver_commit_and_hooks',acceptance.get('commit_started_monotonic'),
            acceptance.get('commit_ended_monotonic'),commit_count=len(acceptance.get('commits') or [])))
    for name,values in boundaries.items():
        spans.append(interval(name,*values))
    repeats=sum(count-1 for count in candidates.values()) if isinstance(acceptance.get('attempts'),list) else None
    return dict(schema_version=1,intervals=spans,command_tree_repeat_candidates=repeats,
        identical_driver_gate_count=0 if repeats==0 else None,
        repeat_basis='whole command + execution cwd + tree; unobserved environment/state inputs prevent claiming identity',
        limits='Intervals can overlap; do not sum or subtract them from thinking/API clocks. Missing observations stay unknown.')
