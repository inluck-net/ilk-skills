"""Candidate final-step marker/CAS for exact unproven progress, never green proof."""
from __future__ import annotations
import hashlib,json,os,tempfile
from pathlib import Path
from iteration_context import ContextError,read_dispatch
from plan_status import parse_frontmatter
from plan_state import transition,_sync_parent,audit_context
from ship_audit import count_authored_steps
import progress_transaction as transaction


def _path(context):
    return transaction._path(context).with_name(transaction._path(context).name.replace('progress-acceptance-','progress-ship-'))


def _save(context,result,phase,head):
    target=_path(context)
    row=dict(schema_version=1,protocol='measured-red-ship-v1',phase=phase,
        context_sha256=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),
        accepted=False,green_accepted=False,progress_accepted=True,
        step_commit=result['head_commit'],tree=result['tree'],marker_head=head)
    with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(row,stream);stream.flush();os.fsync(stream.fileno())
    try:temporary.chmod(0o600);os.replace(temporary,target);_sync_parent(target)
    finally:temporary.unlink(missing_ok=True)


def verify_shipped(context,result,head):
    """Validate an exact in-flight/durable marker/status pair; no write."""
    row=json.loads(_path(context).read_text());selected=read_dispatch(context)
    path=Path(selected['subplan_path']);state=parse_frontmatter(path.read_text())
    authored=count_authored_steps(path.read_text())
    if (row.get('schema_version')!=1 or row.get('protocol')!='measured-red-ship-v1'
            or row.get('phase') not in {'marker-intent','shipped'}
            or row.get('context_sha256')!=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
            or row.get('accepted') is not False or row.get('green_accepted') is not False
            or row.get('progress_accepted') is not True or row.get('step_commit')!=result['head_commit']
            or row.get('tree')!=result['tree'] or not authored or authored!=list(range(len(authored)))
            or selected['step']+1!=len(authored) or state.get('current_step')!=str(len(authored))
            or state.get('status')!='shipped' or state.get('hold')=='human' or head==result['head_commit']
            or transaction.driver._transaction_head(context,result)!=head
            or row['phase']=='shipped' and row.get('marker_head')!=head):
        raise ContextError('unproven ship differs from exact marker intent/state')
    if audit_context(context).get('explained') is not True:raise ContextError('unproven ship state unexplained')
    return row


def complete(context,result,*,deadline):
    with transaction._authority(context,deadline):
        from progress_projection import project
        project(context,result,deadline=deadline)
        selected=read_dispatch(context);path=Path(selected['subplan_path']);root=Path(context['exec_tree'])
        # Transfer this exact transaction's fresh evidence, never another gate.
        transaction._decision(context,result['attempts'][-1],result['progress_observation'])
        authored=count_authored_steps(path.read_text());state=parse_frontmatter(path.read_text())
        if not authored or authored!=list(range(len(authored))):raise ContextError('unproven ship authored step inventory invalid')
        if selected['step']+1!=len(authored):return None # Intermediate progress stays in-progress.
        if state.get('status')=='shipped':
            head=transaction.driver._transaction_head(context,result);verify_shipped(context,result,head)
            _save(context,result,'shipped',head)
            return dict(shipped=True,proven=False,head=head,progress_accepted=True,green_accepted=False)
        if state.get('current_step')!=str(len(authored)) or state.get('status')!='in-progress' or state.get('hold')=='human':
            raise ContextError('unproven ship pointer or hold changed')
        if context.get('master_snapshot'):
            snapshot=context['master_snapshot'];master=parse_frontmatter(Path(snapshot['path']).read_text())
            if any(master.get(key)!=value for key,value in snapshot['owned_state'].items()):
                raise ContextError('unproven ship master state changed')
        target=_path(context)
        if target.exists():
            row=json.loads(target.read_text())
            if (row.get('protocol')!='measured-red-ship-v1' or row.get('phase')!='marker-intent'
                    or row.get('context_sha256')!=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
                    or row.get('step_commit')!=result['head_commit'] or row.get('tree')!=result['tree']):
                raise ContextError('unproven ship intent changed')
        else:_save(context,result,'marker-intent',None)
        head=transaction.driver._transaction_head(context,result)
        if head==result['head_commit']:
            transaction.driver.git(root,'commit','--allow-empty','-m',f"chore(plans): {selected['slug']} shipped [plan:{selected['slug']}#ship]")
        head=transaction.driver._transaction_head(context,result) # Marker hooks must leave the exact tree.
        guards={Path(context['master_snapshot']['path']):context['master_snapshot']['owned_state']} if context.get('master_snapshot') else {}
        transition(path,{'current_step':str(len(authored)),'status':'in-progress'},
            {'status':'shipped'},actor='driver',reason='exact completed measured-red progress; remains unproven',guards=guards)
        _save(context,result,'shipped',head);verify_shipped(context,result,head)
        return dict(shipped=True,proven=False,head=head,progress_accepted=True,green_accepted=False)
