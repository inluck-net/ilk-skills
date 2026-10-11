"""Freeze named repository/ancestor instruction inputs under owned deadlines."""
from pathlib import Path
import hashlib
import re
from iteration_context import ContextError

NAMES=('AGENTS.md','CLAUDE.md','CLAUDE.local.md')


def import_paths(text, parent):
    """Observe documented @ paths outside Markdown code; grant no import access."""
    lines=[];fence=None
    for line in text.splitlines(keepends=True):
        marker=re.match(r'^\s*(`{3,}|~{3,})',line)
        if marker:
            token=marker.group(1)
            if fence is None:fence=token
            elif token[0]==fence[0] and len(token)>=len(fence):fence=None
            continue
        if fence is None:lines.append(line)
    plain=re.sub(r'(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)', '', ''.join(lines),flags=re.S)
    for match in re.finditer(r'''(?<![\w@])@((?:\\[ \t]|[^\s`"'<>])+)''',plain):
        raw=re.sub(r'\\([ \t])',r'\1',match.group(1))
        path=Path(raw).expanduser()
        yield path if path.is_absolute() else parent/path


def referenced_inventory(paths, *, deadline=None):
    """Freeze manually followed references; unique file/parent roles bound cycles.

    Owned print mode disables automatic memory sources. Its worker explicitly
    follows references, so Claude's automatic four-hop limit cannot bound this
    inventory. No new numeric limit or import authorization is introduced.
    """
    from collections import deque
    rows={};expanded=set();queue=deque((Path(path),False) for path in sorted(paths,key=str))
    while queue:
        if deadline is not None:
            from iteration_clock import monotonic
            if monotonic()>=deadline:raise ContextError('instruction inventory reached original iteration deadline')
        path,referenced=queue.popleft();path=path.absolute();key=str(path)
        if key in rows:continue
        if not path.is_file():
            if referenced:rows[key]=dict(path=key,resolved_path=str(path.resolve()),present=False)
            continue
        raw=path.read_bytes();resolved=str(path.resolve())
        rows[key]=dict(path=key,resolved_path=resolved,sha256=hashlib.sha256(raw).hexdigest())
        role=(resolved,str(path.parent.resolve()))
        if role in expanded:continue
        expanded.add(role)
        queue.extend((imported,True) for imported in import_paths(raw.decode('utf-8-sig',errors='replace'),path.parent))
    return [rows[key] for key in sorted(rows)]


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
    protocol=context.get('instruction_input_inventory_protocol')
    if protocol is not None and (type(protocol) is not int or protocol!=2):
        raise ContextError('unknown instruction input inventory protocol')
    if protocol==2:
        home=context.get('private_worker_home')
        if home:
            home=Path(home)
            paths.update(home/name for name in ('CLAUDE.md','CLAUDE.local.md'))
            paths.update(path for path in (home/'rules').rglob('*.md') if path.is_file())
        return referenced_inventory(paths,deadline=deadline)
    result=[]
    for path in sorted(paths,key=str):
        if deadline is not None:
            from iteration_clock import monotonic
            if monotonic()>=deadline:raise ContextError('instruction inventory reached original iteration deadline')
        if path.is_file():
            result.append(dict(path=str(path.absolute()),resolved_path=str(path.resolve()),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return result


def bootstrap_inventory(context):
    """Required manual roots/rules when the CLI's automatic sources are off."""
    rows={row['path']:row for row in context.get('instruction_input_inventory',[]) if row.get('sha256')}
    required=[];seen=set()
    def add(path):
        key=str(path)
        if key in rows and key not in seen:
            required.append(dict(path=key,sha256=rows[key]['sha256']));seen.add(key)
    for row in context.get('global_instruction_inventory',[]):add(Path(row['path']))
    root=Path(context['exec_tree']).resolve(strict=True)
    for parent in reversed((root,*root.parents)):
        for name in NAMES:add(parent/name)
        add(parent/'.claude/CLAUDE.md')
        directory=parent/'.claude/rules'
        for raw in sorted(rows):
            path=Path(raw)
            try:path.relative_to(directory)
            except ValueError:continue
            if path.suffix=='.md':add(path)
    return required


def verify(context):
    frozen=context.get('instruction_input_inventory')
    if frozen is not None and inventory(context)!=frozen:
        raise ContextError('instruction inputs changed after dispatch freeze')
