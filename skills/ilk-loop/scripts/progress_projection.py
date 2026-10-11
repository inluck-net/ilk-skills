"""Candidate raw-fail projection of one exact measured-red progress transaction.

Legacy gate readers stay red/blocking. New loop/close/landing adapters must
explicitly consume progress acceptance; this is never green gate evidence.
"""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from iteration_context import ContextError,read_dispatch
from plan_status import parse_frontmatter
from plan_state import audit_context
from emit_jsonl_record import build_record,extract_failing_check
import progress_transaction as transaction


def project(context,result,*,deadline):
    with transaction._authority(context,deadline):
        path=transaction._path(context);row=json.loads(path.read_text())
        saved=row.get('result') or {}
        if (row.get('phase')!='advanced' or row.get('protocol')!='measured-red-progress-v1'
                or row.get('context_sha256')!=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
                or result.get('progress_accepted') is not True or result.get('accepted') is not False
                or result.get('green_accepted') is not False
                or any(result.get(key)!=value for key,value in saved.items())):
            raise ContextError('progress projection lacks exact advanced receipt')
        selected=read_dispatch(context);root=Path(context['exec_tree'])
        transaction._decision(context,result['attempts'][-1],result['progress_observation'])
        head=transaction.driver._transaction_head(context,result)
        if (transaction.driver.git(root,'rev-parse','HEAD')!=head
                or transaction.driver.git(root,'rev-parse','HEAD^{tree}')!=result['tree']
                or transaction.driver.working_tree(root)!=result['tree']):
            raise ContextError('progress projection tree changed')
        state=parse_frontmatter(Path(selected['subplan_path']).read_text())
        if (state.get('current_step')!=str(selected['step']+1) or state.get('status') not in {'in-progress','shipped'}
                or state.get('hold')=='human' or audit_context(context).get('explained') is not True):
            raise ContextError('progress projection state changed')
        if state.get('status')=='shipped':
            from progress_ship import verify_shipped
            verify_shipped(context,result,head)
        observations=result['progress_observation']['observations']
        reds=[dict(verdict='pre-existing',command=observation['command'],scope=observation['scope'],
            base_sha=selected['batch_base_sha'],iteration_base=context['base_commit'],
            node_ids=observation['node_ids'],measurement_receipt=observation['receipt'],
            measurement_sha256=observation['sha256']) for observation in observations]
        checks=result['attempts'][-1]['check_observations']
        data=dict(results=checks,head_sha=head)
        record=build_record(selected['slug'],selected['step'],'fail',1,extract_failing_check(data),data)
        record.update(driver_owned=True,driver_attempts=result['attempts'],
            run_id=context['run_id'],iteration=context['iteration'],
            progress_accepted=True,green_accepted=False,accepted=False,
            accepted_tree=None,progress_tree=result['tree'],progress_head=head,
            progress_protocol='measured-red-progress-v1',progress_receipt=str(path),
            progress_receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            pre_existing_red=reds,reason='measured pre-existing red; step progressed without green proof',
            attribution=dict(verdict='pre-existing',iteration_base=context['base_commit'],
                batch_base=selected['batch_base_sha'],nodes=[dict(node_id=node,verdict='pre-existing')
                for observation in observations for node in observation['node_ids']]))
        return record


def main():
    import argparse
    from iteration_context import read_context
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--accepted',type=Path,required=True)
    args=parser.parse_args();context=read_context(args.context)
    if type(context.get('measured_red_progress_protocol')) is not int or context['measured_red_progress_protocol'] != 1:
        raise ContextError('measured-red projection requires frozen opt-in')
    print(json.dumps(project(context,json.loads(args.accepted.read_text()),
        deadline=context['iteration_deadline_monotonic'])))


if __name__=='__main__':main()
