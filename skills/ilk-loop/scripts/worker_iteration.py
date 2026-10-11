#!/usr/bin/env python3
"""Code-only worker turns and authoritative driver gates within one deadline.

The Bash caller owns shipping, integrity, landing and final proof projection.
This helper never chooses another plan or extends an iteration's budget.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import tempfile
from iteration_clock import monotonic
from iteration_context import read_context,read_dispatch,ContextError
from worker_dispatch import binding
from worker_shell import canonical_command
from run_registry import register_child
from driver_step import accept_step,recover_acceptance
from recovery import read_retry_state,update_retry_state,save_notice
from gate_retry import decide_gate_retry


def phase(context,name):
    """Existing phase artifact shape, with the verified runner identity."""
    target=Path(context['run_registry_dir'])/'phase.json';selected=read_dispatch(context)
    payload=dict(phase=name,slug=selected['slug'],step=selected['step'],run_id=context['run_id'],
                 iteration=context['iteration'],started_at=int(time.time()),pid=context['runner_owner_pid'],
                 toolkit_sha=context['toolkit_sha'])
    with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(payload,stream);stream.flush();os.fsync(stream.fileno())
    try:temporary.chmod(0o600);os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)


def instructions(context,path):
    selected=read_dispatch(context)
    inventory=context.get('root_instruction_inventory')
    reads=[selected['subplan_path']]+[row['path'] for row in inventory or []]
    bootstrap=canonical_command(context,path,'cat -- '+' '.join(shlex.quote(item) for item in reads))
    return '\n'.join([
        'ILK CODE-ONLY DISPATCH: the driver owns gates, commits, pointers and shipping.',
        f"Execution worktree: {context['exec_tree']}",
        'All relative implementation paths in the selected step refer to this execution worktree.',
        'The pinned wrapper and toolkit root are driver infrastructure, not the implementation target.',
        'Read the named implementation files and relevant tests together where independent; then make the scoped change.',
        f"Read this exact subplan: {selected['subplan_path']}",
        ('Root instruction files were inventoried by the driver: '+json.dumps([row['path'] for row in inventory])
         if inventory is not None else 'Root instruction inventory is unknown; read repository instructions.'),
        'First group these required independent reads in ONE Bash call:',
        bootstrap,
        'Follow referenced instructions and any nested instructions relevant to files you edit.',
        'After the required reads, implement this step; no queue or toolkit bootstrap discovery is needed.',
        f"Implement only step {selected['step']}; leave the code diff for driver acceptance.",
        'Do not commit, push, change plans/state/proof, or run authoritative gates.',
        'Every Bash tool command MUST use this pinned wrapper. Working example for pwd:',
        canonical_command(context,path,'pwd'),
        'Working example to read the selected plan (copy this command exactly):',
        canonical_command(context,path,'cat '+shlex.quote(selected['subplan_path'])),
        'Keep the interpreter, wrapper and context arguments exactly as shown.',
        'Replace only the final --command argument with your shell command as ONE POSIX single-quoted argument.',
        'Do not put backslashes before quote delimiters. Do not add outer shell operators or expansions.',
        ('Native Read, Grep, Glob, Edit and Write are available inside the same OS boundary; '
         'prefer them for ordinary reads/edits to avoid shell quoting. Use absolute paths in the execution worktree.'
         if context.get('worker_tools','shell')=='native' else
         'Use the wrapper for reading files and editing code. Other tools are unavailable.'),
        'Do not invoke /ilk or rediscover a batch. Do not run a full test suite.',
        'Run diagnostic tests only when needed to locate or repair a selected-step defect; use the smallest relevant failing check.',
        'The driver runs the authored gates after your edit. Do not repeat them solely to report green, or expand a focused step into broad adjacent/repository test runs without a driver diagnostic requiring it.',
        'Report which diagnostics you actually ran; leave unrun verification to the driver.',
        'Report findings in your final text; the driver imports them after acceptance.',
    ])


def worker_turn(context,path,prompt,*,claude,model,budget,mcp,session=None):
    bound,manifest=binding(path,with_manifest=True)
    from provider_control import session as provider_session
    with provider_session(context,path,manifest,bound['settings_path'],model) as control:
        outcome=_worker_turn(context,path,prompt,claude=claude,model=model,budget=budget,
                             mcp=mcp,session=session,bound=bound,manifest=manifest,control=control)
        outcome['provider_control']=control['observation']
        return outcome


def _tool_names(context,manifest):
    profile=context.get('worker_tools','shell')
    if profile!=manifest.get('worker_tools','shell') or profile not in {'shell','native'}:
        raise ContextError('worker tools contradict frozen launch inputs')
    return 'Bash,Read,Grep,Glob,Edit,Write' if profile=='native' else 'Bash'


def _worker_turn(context,path,prompt,*,claude,model,budget,mcp,session,bound,manifest,control):
    deadline=context['iteration_deadline_monotonic']
    from run_inputs import verify_code_snapshot
    verify_code_snapshot(context['worker_inputs_dir'],expected=context.get('toolkit_code_inventory_sha256'),deadline=deadline)
    import hashlib
    from instruction_inputs import verify
    verify(context)
    for row in context.get('root_instruction_inventory',[]):
        if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256']:
            raise ContextError('repository instructions changed after dispatch freeze')
    pinned=[row for row in manifest['executables'] if row['name']=='claude']
    if len(pinned)!=1 or Path(claude).resolve()!=Path(pinned[0]['path']):
        raise ContextError('worker executable contradicts frozen launch inputs')
    if mcp is None or Path(mcp).resolve()!=Path(manifest['mcp']['path']):
        raise ContextError('worker MCP configuration contradicts frozen launch inputs')
    args=[claude,'-p','--dangerously-skip-permissions','--output-format','stream-json',
          '--verbose','--include-partial-messages','--tools',_tool_names(context,manifest),'--settings',control['settings_path'],
          '--setting-sources','',
          '--append-system-prompt',instructions(context,path)]
    if model:args+=['--model',model]
    if budget:args+=['--max-budget-usd',str(budget)]
    if mcp:args+=['--mcp-config',str(mcp),'--strict-mcp-config']
    if session:args+=['--resume',session]
    args.append(prompt)
    env=dict(os.environ,ILK_WORKER_SESSION='',ILK_ITER_RECORD_STAGE='',TMPDIR=bound['worker_tmp_dir'],
             CLAUDE_CODE_TMPDIR=bound['worker_tmp_dir'],CLAUDE_TMPDIR=bound['worker_tmp_dir'])
    env.update(control['env'])
    cli_started=monotonic()
    from owned_command import wrap_command
    process=subprocess.Popen(wrap_command(['/usr/bin/sandbox-exec','-f',bound['sandbox_path'],*args],context,worker=True),
        cwd=context['exec_tree'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    events=[];read_errors=[]
    def copy(stream,target,collect):
        try:
            for line in stream:
                target.write(line);target.flush()
                if collect:
                    try:events.append(json.loads(line))
                    except ValueError:read_errors.append('malformed worker JSON')
        except Exception as exc:read_errors.append(type(exc).__name__)
    threads=[threading.Thread(target=copy,args=(process.stdout,sys.stdout,True)),
             threading.Thread(target=copy,args=(process.stderr,sys.stderr,False))]
    handlers={}
    def interrupted(sig,_):
        raise ContextError('worker iteration interrupted by signal '+str(sig))
    try:
        for sig in (signal.SIGTERM,signal.SIGINT):
            handlers[sig]=signal.signal(sig,interrupted)
        for thread in threads:thread.start()
        process.wait(timeout=max(deadline-monotonic(),0))
    finally:
        # The exact owned session is closed even if its leader exits while a
        # descendant still holds stdout. No process-name search is involved.
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait()
        for thread in threads:
            if thread.ident is not None:thread.join()
        for sig,handler in handlers.items():signal.signal(sig,handler)
        from run_registry import retire_child
        if not retire_child(context,process.pid):raise ContextError('worker group cleanup is incomplete')
    cli_ended=monotonic()
    roots=[row for row in events if isinstance(row,dict) and not row.get('parent_tool_use_id')]
    ids={row['session_id'] for row in roots if row.get('type')=='system' and row.get('subtype')=='init' and row.get('session_id')}
    results=[row for row in roots if row.get('type')=='result']
    completed=(process.returncode==0 and not read_errors and len(results)==1
               and results[0].get('subtype')=='success' and not results[0].get('is_error')
               and results[0].get('terminal_reason') not in {'budget_exhausted','quota_exhausted'})
    texts=[]
    for row in roots:
        if row.get('type')=='assistant':
            text=[block.get('text','') for block in (row.get('message') or {}).get('content',[]) if isinstance(block,dict) and block.get('type')=='text']
            if text:texts=['\n'.join(text)]
    return dict(completed=completed,session_id=next(iter(ids)) if len(ids)==1 else None,
                findings=texts[-1] if texts else None,exit_code=process.returncode,
                cost_usd=results[0].get('total_cost_usd') if len(results)==1 else None,
                terminal_reason=results[0].get('terminal_reason') if len(results)==1 else None,
                cli_started_monotonic=cli_started,cli_ended_monotonic=cli_ended,
                actual_models=sorted({row['model'] for row in roots if row.get('type')=='system'
                                      and row.get('subtype')=='init' and row.get('model')}))


def run(context,path,prompt,*,claude,model,budget,mcp,gate_timeout,turn=worker_turn):
    selected=read_dispatch(context);key=f"{selected['slug']}:{selected['step']}"
    recovery=Path(context['recovery_dir']);counts=recovery/'gate-red-counts.json';ids_path=recovery/'gate-red-ids.json'
    # Validate both durable histories BEFORE any provider call. Corruption
    # cannot reset the retry budget or be ignored on a first green result.
    read_retry_state(counts,'count');read_retry_state(ids_path,'ids')
    from driver_step import read_gate_history
    read_gate_history(context)
    recovered=recover_acceptance(context,deadline=context['iteration_deadline_monotonic'])
    if recovered:
        recovered.update(worker_turns=[])
        return recovered
    session=None;attempts=[];turns=[];remaining_budget=budget
    from run_local_checks import step_gate_fence
    fence=step_gate_fence(Path(selected['subplan_path']).read_text(),selected['step']).fence_text or ''
    if re.search(r'^\s*gate_first:\s*(true|yes|1)\s*$',fence,re.MULTILINE|re.IGNORECASE):
        phase(context,'gate')
        first=accept_step(context,default_timeout=gate_timeout,deadline=context['iteration_deadline_monotonic'])
        attempts.extend(first['attempts'])
        if first['accepted']:
            first.update(worker_turns=[],gate_first=True)
            return first
        if attempts[-1].get('verdict') in {'flaky', 'inconclusive'}:
            return dict(accepted=False, attempts=attempts, worker_turns=[],
                        gate_first=True, stop_reason='local_checks_inconclusive')
        prompt+='\nPre-dispatch driver gate is red; implement this step using this diagnostic evidence:\n'+json.dumps(first,indent=2)
    while monotonic()<context['iteration_deadline_monotonic']:
        phase(context,'agent')
        outcome=turn(context,path,prompt,claude=claude,model=model,budget=remaining_budget,mcp=mcp,session=session)
        turns.append({key:value for key,value in outcome.items() if key!='findings'})
        if not outcome['completed']:
            return dict(accepted=False,attempts=attempts,worker_turns=turns,
                        stop_reason='budget_exhausted' if outcome.get('terminal_reason')=='budget_exhausted' else 'code_only_worker_incomplete')
        if session and outcome['session_id']!=session:raise ContextError('resume changed provider session identity')
        session=outcome['session_id']
        phase(context,'gate')
        accepted=accept_step(context,default_timeout=gate_timeout,deadline=context['iteration_deadline_monotonic'],prior_attempts=attempts)
        attempts=accepted['attempts']
        if accepted['accepted']:
            if outcome.get('findings'):
                from plan_state import append_findings
                append_findings(Path(selected['subplan_path']),
                    f"Driver-imported worker findings ({context['run_id']}, step {selected['step']}):\n"+
                    '\n'.join('> '+line for line in outcome['findings'].splitlines()),
                    reason='import final diagnostic text after driver acceptance')
            accepted.update(attempts=attempts,worker_turns=turns,findings=outcome.get('findings'))
            return accepted
        if accepted.get('commits'):
            return dict(accepted=False,attempts=attempts,worker_turns=turns,stop_reason='code_only_commit_hook_red')
        if attempts[-1].get('verdict') in {'flaky','inconclusive'}:
            return dict(accepted=False,attempts=attempts,worker_turns=turns,
                        stop_reason='local_checks_inconclusive')
        latest=attempts[-1];checks=latest['check_observations'];failures=set()
        # Full capture, never a display tail, supplies retry identity. Missing
        # IDs stay unknown rather than proving progress.
        for check in checks:
            if check['passed']:continue
            for channel in ('stdout','stderr'):
                output=check.get(channel+'_path')
                if output:failures.update(re.findall(r'FAILED\s+(\S+)',Path(output).read_text(errors='replace')))
        previous=read_retry_state(ids_path,'ids').get(key,[])
        count=read_retry_state(counts,'count').get(key,0)
        decision=decide_gate_retry(red_count=count,is_no_commits=latest['working_tree']==context['base_tree'],
            is_environment_fault=any(str(check.get('reason','')).startswith('environment-fault:') for check in checks),
            same_failing_set=bool(failures) and sorted(failures)==previous,progress_known=bool(failures and previous))
        update_retry_state(counts,key,'count');update_retry_state(ids_path,key,'ids',sorted(failures))
        detail='Driver gate red; pointer unchanged. Repair only this step, leave a diff.\n'+json.dumps(latest,indent=2)
        source=path.parent/(path.stem+'-red.txt');source.write_text(detail);save_notice(context,selected['slug'],selected['step'],source)
        if decision.should_stop or not session:
            return dict(accepted=False,attempts=attempts,worker_turns=turns,
                        stop_reason=decision.stop_reason or 'code_only_resume_unavailable')
        if budget:
            cost=outcome.get('cost_usd')
            if not isinstance(cost,(int,float)) or isinstance(cost,bool) or not math.isfinite(cost) or cost<0:
                return dict(accepted=False,attempts=attempts,worker_turns=turns,stop_reason='code_only_budget_unknown')
            remaining_budget-=cost
            if remaining_budget<=0:
                return dict(accepted=False,attempts=attempts,worker_turns=turns,stop_reason='budget_exhausted')
        # Same private home and session. The caller's original deadline is
        # unchanged. Cross-launch diagnostics never supply green proof.
        prompt=detail
    return dict(accepted=False,attempts=attempts,worker_turns=turns,stop_reason='timeout')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--prompt',required=True)
    parser.add_argument('--claude',required=True)
    parser.add_argument('--model',default='')
    parser.add_argument('--budget',type=float,default=0)
    parser.add_argument('--mcp',type=Path)
    parser.add_argument('--gate-timeout',type=int,required=True)
    args=parser.parse_args();context=read_context(args.context)
    destination=args.context.parent/(args.context.stem+'-acceptance.json')
    try:
        result=run(context,args.context,args.prompt,claude=args.claude,model=args.model,budget=args.budget,
                   mcp=args.mcp,gate_timeout=args.gate_timeout)
    except subprocess.TimeoutExpired:
        # communicate's bound is the remaining original iteration deadline.
        # Its exception includes the entire provider argv/prompt; retain a
        # diagnostic without copying that content into the acceptance record.
        result=dict(accepted=False,stop_reason='timeout',
                    error='TimeoutExpired: worker reached the original iteration deadline')
    except Exception as exc:
        expired=monotonic()>=context['iteration_deadline_monotonic']
        result=dict(accepted=False,stop_reason='timeout' if expired else 'code_only_acceptance_failed',
                    error=type(exc).__name__+': '+str(exc))
    result.update(run_id=context['run_id'],iteration=context['iteration'])
    with destination.open('x') as stream:
        json.dump(result,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    destination.chmod(0o600)
    return 0 if result['accepted'] else 1


if __name__=='__main__':raise SystemExit(main())
