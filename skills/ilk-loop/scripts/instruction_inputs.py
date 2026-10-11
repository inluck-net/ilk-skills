"""Freeze named repository/ancestor instruction inputs under owned deadlines."""
from pathlib import Path
import hashlib
from iteration_context import ContextError

NAMES=('AGENTS.md','CLAUDE.md','CLAUDE.local.md')


def inventory(context):
    import driver_step
    root=Path(context['exec_tree']).resolve(strict=True)
    specs=list(NAMES)+[':(glob)**/'+name for name in NAMES]+[
        '.claude/rules', '.claude/rules/**', ':(glob)**/.claude/rules', ':(glob)**/.claude/rules/**']
    names=set()
    deadline=driver_step._deadline.get()
    if deadline is None:deadline=context.get('iteration_deadline_monotonic')
    token=driver_step._deadline.set(deadline) if driver_step._deadline.get() is None and deadline is not None else None
    owner=driver_step._operation_context.set(context)
    try:
        for options in (('--cached','--others','--exclude-standard'),('--others','--ignored','--exclude-standard')):
            names.update(name for name in driver_step.git(root,'ls-files','-z',*options,'--',*specs).split('\0') if name)
    finally:
        driver_step._operation_context.reset(owner)
        if token is not None:driver_step._deadline.reset(token)
    paths={root/name for name in names}
    paths.update(parent/name for parent in (root,*root.parents) for name in NAMES)
    paths.update(parent/'.claude/CLAUDE.md' for parent in (root,*root.parents))
    rule_dirs={parent/'.claude/rules' for parent in (root,*root.parents)}
    rule_dirs.update(path for path in paths if path.is_dir())
    for directory in rule_dirs:
        if directory.is_dir():
            for path in directory.rglob('*'):
                if deadline is not None:
                    from iteration_clock import monotonic
                    if monotonic()>=deadline:raise ContextError('instruction inventory reached original iteration deadline')
                if path.is_file():paths.add(path)
    result=[]
    for path in sorted(paths,key=str):
        if deadline is not None:
            from iteration_clock import monotonic
            if monotonic()>=deadline:raise ContextError('instruction inventory reached original iteration deadline')
        if path.is_file():
            result.append(dict(path=str(path.absolute()),resolved_path=str(path.resolve()),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return result


def verify(context):
    frozen=context.get('instruction_input_inventory')
    if frozen is not None and inventory(context)!=frozen:
        raise ContextError('instruction inputs changed after dispatch freeze')
