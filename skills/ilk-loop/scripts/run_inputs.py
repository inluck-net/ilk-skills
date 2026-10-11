#!/usr/bin/env python3
"""Prepare a private pinned toolkit/settings/hook home for a new run.

Candidate preparation API, not yet wired to every launcher. Credentials are
explicitly copied only to private settings; manifest omits their values. Unknown
shell hook wrappers refuse preparation instead of retaining live-home commands.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import tarfile
import sys
from provider_provenance import configured_provider


class InputPinError(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_script(command, *, root=None):
    tokens=shlex.split(command)
    if not tokens or any(token in {';','&&','||','|','>'} for token in tokens):
        raise InputPinError('unsupported shell hook wrapper')
    candidates=[]
    for index,token in enumerate(tokens):
        expanded=Path(os.path.expanduser(os.path.expandvars(token)))
        if root is not None and not expanded.is_absolute():
            expanded=Path(root)/expanded
        if index==0 and expanded.name in {'python','python3','bash','sh','pwsh','powershell','python.exe','powershell.exe'}:
            continue
        if expanded.is_file() and (index==0 or expanded.suffix in {'.py','.sh','.ps1'}):
            candidates.append((index,expanded.resolve()))
    if len(candidates)!=1:
        raise InputPinError('hook must name exactly one explicit script')
    return tokens,*candidates[0]


def _global_instruction_paths(home):
    home=Path(home)
    rules=home/'rules'
    if rules.is_symlink():raise InputPinError('symlinked user rules directory requires explicit pin adapter')
    entries=list(rules.rglob('*')) if rules.is_dir() else []
    if any(path.is_symlink() and path.is_dir() for path in entries):
        raise InputPinError('symlinked user rules directory requires explicit pin adapter')
    return sorted([home/name for name in ('CLAUDE.md','CLAUDE.local.md') if (home/name).is_file()]+
                  [path for path in entries if path.is_file() and path.suffix=='.md'],key=str)


def _merge_settings(lower, higher):
    """Supported non-managed settings: recursive objects, additive lists."""
    result=dict(lower)
    for key,value in higher.items():
        previous=result.get(key)
        if isinstance(previous,dict) and isinstance(value,dict):
            result[key]=_merge_settings(previous,value)
        elif isinstance(previous,list) and isinstance(value,list):
            result[key]=previous+[item for item in value if item not in previous]
        else:result[key]=value
    return result


def _settings_sources(worker_home, project):
    sources=[('user',worker_home/'settings.json',Path(project).resolve() if project else Path.cwd())]
    if project is not None:
        project=Path(project).resolve(strict=True)
        sources.append(('project',project/'.claude/settings.json',project))
        # Claude's local settings can live at the main checkout for worktrees.
        common=subprocess.check_output(['git','-C',str(project),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip()
        main=Path(common).parent if Path(common).name=='.git' else project
        sources.append(('local',main/'.claude/settings.local.json',main))
        if main!=project:
            if (main/'.claude/settings.local.json').exists() and (project/'.claude/settings.local.json').exists():
                raise InputPinError('two local settings sources require version-specific precedence adapter')
            sources.append(('local-worktree',project/'.claude/settings.local.json',project))
    effective={};observations=[]
    supported={'hooks','permissions','env','model','effortLevel','disableAllHooks'}
    for scope,path,root in sources:
        if not path.exists():
            observations.append({'scope':scope,'path':str(path),'present':False});continue
        raw=path.read_bytes();data=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data,dict):raise InputPinError('settings source must be an object')
        if scope!='user':
            if set(data)-supported:
                raise InputPinError('project setting requires explicit precedence adapter')
            if 'defaultMode' in data.get('permissions',{}):
                raise InputPinError('project permission mode requires scope-aware adapter')
            if any(key.startswith(('OTEL_','CLAUDE_CODE_ENABLE_TELEMETRY')) for key in data.get('env',{})):
                raise InputPinError('project telemetry settings require scope-aware adapter')
        for key in ('fallbackModel','modelPicker','modelSettings'):
            if key in data:raise InputPinError('model-specific settings require explicit precedence adapter')
        for entries in data.get('hooks',{}).values():
            for entry in entries:
                for hook in entry.get('hooks',[]):
                    if hook.get('type','command')!='command':
                        raise InputPinError('non-command hook requires an explicit pin adapter')
                    tokens,index,script=_command_script(hook.get('command',''),root=root)
                    tokens[index]=str(script);hook['command']=shlex.join(tokens)
        effective=_merge_settings(effective,data)
        if path.read_bytes()!=raw:raise InputPinError('settings changed during preparation')
        observations.append({'scope':scope,'path':str(path),'present':True,
                             'sha256':hashlib.sha256(raw).hexdigest()})
    return effective,observations


def _code_inventory(root,deadline=None):
    """Canonical snapshot files/links; generated Python caches are not inputs."""
    from iteration_clock import monotonic
    rows=[]
    for path in sorted(Path(root).rglob('*')):
        relative=path.relative_to(root)
        if '__pycache__' in relative.parts:continue
        if deadline is not None and monotonic()>=deadline:raise InputPinError('code inventory reached existing deadline')
        if path.is_symlink():rows.append(dict(path=str(relative),kind='symlink',target=os.readlink(path)))
        elif path.is_file():rows.append(dict(path=str(relative),kind='file',sha256=digest(path)))
    return rows


def _inventory_digest(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def verify_code_snapshot(destination,*,expected=None,deadline=None):
    manifest=json.loads((Path(destination)/'inputs.json').read_text())
    rows=manifest.get('code_inventory')
    if rows is None:
        if expected is not None:raise InputPinError('frozen code inventory unavailable')
        return None # Historical descriptor did not freeze this capability.
    fingerprint=_inventory_digest(rows)
    if fingerprint!=manifest.get('code_inventory_sha256') or expected is not None and fingerprint!=expected:
        raise InputPinError('frozen code inventory changed')
    if _code_inventory(Path(manifest['code_root']),deadline)!=rows:raise InputPinError('pinned toolkit files changed')
    return fingerprint


def prepare(repo: Path, worker_home: Path, destination: Path, *, revision='HEAD', observe_scope=False, project=None, worker_thinking='default', worker_tools='shell', measured_red_progress=False):
    repo=repo.resolve();worker_home=worker_home.resolve();destination=destination.resolve()
    source_root=repo
    marker=repo/'.ilk-release.json'
    if marker.exists():
        metadata=json.loads(marker.read_text())
        revision=metadata['sha'];repo=Path(metadata['source_repo']).resolve(strict=True)
        # Reconstruct the exact released commit, never source-repo HEAD.
    if destination.exists(): raise InputPinError('run input destination already exists')
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse',revision+'^{commit}'],text=True).strip()
    archive=subprocess.check_output(['git','-C',str(repo),'archive',sha])
    source,settings_sources=_settings_sources(worker_home,project)
    if worker_tools not in {'shell','native'}:raise InputPinError('unknown worker tool profile')
    if not isinstance(measured_red_progress,bool):raise InputPinError('explicit measured-red progress opt-in required')
    if measured_red_progress and sys.platform!='darwin':raise InputPinError('measured-red progress requires the checked Mac owner adapter')
    if worker_thinking not in {'default','disabled'}:raise InputPinError('unknown worker thinking profile')
    if worker_thinking=='disabled':
        from provider_control import validate_settings
        validate_settings(source,source.get('env',{}).get('ANTHROPIC_MODEL') or source.get('model'))
    pinned=json.loads(json.dumps(source));hooks=[]
    # Pre-validate all hook commands before creating any prepared home.
    for phase,entries in pinned.get('hooks',{}).items():
        for entry in entries:
            for hook in entry.get('hooks',[]):
                if hook.get('type','command')!='command':
                    raise InputPinError('non-command hook requires an explicit pin adapter')
                tokens,index,script=_command_script(hook.get('command',''))
                hooks.append((phase,hook,tokens,index,script))
    for key in ('apiKeyHelper','otelHeadersHelper'):
        if source.get(key): raise InputPinError('credential helper requires explicit provisioning adapter')
    destination.mkdir(parents=True,mode=0o700)
    code=destination/'code';code.mkdir(mode=0o700)
    home=destination/'worker-home';home.mkdir(mode=0o700)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as stream:
            for member in stream.getmembers():
                if not (code/member.name).resolve().is_relative_to(code):
                    raise InputPinError('archive path escapes snapshot')
                if member.issym() or member.islnk():
                    link=Path(member.linkname)
                    if link.is_absolute() or not (code/member.name).parent.joinpath(link).resolve().is_relative_to(code):
                        raise InputPinError('archive link escapes snapshot')
            stream.extractall(code)
        if worker_tools=='native':
            api=code/'skills/ilk-loop/scripts/worker_iteration.py'
            functions={node.name for node in ast.parse(api.read_text()).body if isinstance(node,ast.FunctionDef)} if api.exists() else set()
            if '_tool_names' not in functions:raise InputPinError('native tool adapter absent in pinned revision')
        if measured_red_progress:
            api=code/'skills/ilk-loop/scripts/worker_iteration.py'
            functions={node.name for node in ast.parse(api.read_text()).body if isinstance(node,ast.FunctionDef)} if api.exists() else set()
            loop=code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'
            # Refuse a worker-only pin: acceptance without close/terminal wiring
            # is not a launchable profile. Capability marker lands last.
            runtime_ready=loop.is_file() and '# measured-red-progress-runtime-v1' in loop.read_text()
            if not runtime_ready or '_accept_measured_progress' not in functions or any(not (code/'skills/ilk-loop/scripts'/name).is_file()
                    for name in ('base_samples.py','measured_progress.py','progress_transaction.py','progress_ship.py','progress_projection.py','progress_close.py')):
                raise InputPinError('measured-red progress adapter absent in pinned revision')
        if worker_thinking=='disabled' and not (code/'skills/ilk-loop/scripts/provider_control.py').is_file():
            raise InputPinError('thinking-control adapter absent in pinned revision')
        (home/'skills').symlink_to(code/'skills',target_is_directory=True)
        (home/'commands').symlink_to(code/'commands',target_is_directory=True)
        hookdir=home/'hooks';hookdir.mkdir(mode=0o700)
        observations=[];copied={}
        for phase,hook,tokens,index,script in hooks:
            if script not in copied:
                payload=script.read_bytes();source_hash=hashlib.sha256(payload).hexdigest()
                target=hookdir/(source_hash+'-'+script.name)
                target.write_bytes(payload);target.chmod(0o700);copied[script]=target
            target=copied[script];tokens[index]=str(target)
            hook['command']=shlex.join(tokens)
            if digest(script)!=digest(target):
                raise InputPinError('hook source changed during preparation')
            observations.append({'phase':phase,'source_sha256':digest(target),'pinned_sha256':digest(target),
                                 'pinned_script':str(target),'source_name':script.name})
        if observe_scope:
            observer=code/'hooks/worker-write-scope.py'
            if not observer.is_file(): raise InputPinError('scope observer missing in pinned revision')
            pinned.setdefault('hooks',{}).setdefault('PreToolUse',[]).append({
                'matcher':'Bash|Edit|Write|MultiEdit|NotebookEdit',
                'hooks':[{'type':'command','command':shlex.join(['python3',str(observer)])}]})
            observations.append({'phase':'PreToolUse','source_name':observer.name,
                                 'pinned_script':str(observer),'pinned_sha256':digest(observer)})
        pinned.setdefault('env',{})['ILK_SKILL_HOME']=str(code/'skills')
        pinned['env']['ILK_CODE_ROOT']=str(code)
        global_instructions=[]
        for source_instruction in _global_instruction_paths(worker_home):
            payload=source_instruction.read_bytes()
            from instruction_inputs import import_paths
            if list(import_paths(payload.decode('utf-8-sig',errors='replace'),source_instruction.parent)):
                raise InputPinError('global instruction imports require an explicit rebasing adapter')
            target=home/source_instruction.relative_to(worker_home)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(payload);target.chmod(0o400)
            if digest(source_instruction)!=digest(target):raise InputPinError('global instruction changed during preparation')
            global_instructions.append(dict(source_path=str(source_instruction),path=str(target),sha256=digest(target)))
        code_inventory=_code_inventory(code)
        settings=home/'settings.json';settings.write_text(json.dumps(pinned,indent=2)+'\n');settings.chmod(0o600)
        # Exclude credentials and arbitrary env values from public provenance.
        safe={key:value for key,value in pinned.items() if key!='env'}
        env=pinned.get('env',{})
        drain_api=code/'skills/ilk-loop/scripts/upgrade_drain.py'
        drain_functions={node.name for node in ast.parse(drain_api.read_text()).body if isinstance(node,ast.FunctionDef)} if drain_api.exists() else set()
        manifest={'schema_version':1,'toolkit_sha':sha,'code_root':str(code),'worker_home':str(home),
                  'upgrade_drain_protocol':1 if {'boundary','activation','consume'}<=drain_functions else None,
                  'source_root':str(source_root),'source_repo':str(repo),'source_worker_home':str(worker_home),
                  'settings_non_env_sha256':hashlib.sha256(json.dumps(safe,sort_keys=True).encode()).hexdigest(),
                  'provider_configured':configured_provider(env.get('ANTHROPIC_BASE_URL')),
                  'env_keys':sorted(env),'requested_model':env.get('ANTHROPIC_MODEL') or pinned.get('model'),
                  'hooks':observations,'credentials_provisioned':'private settings copy; no values in manifest',
                  'scope_observation_only':observe_scope,
                  'settings_sources':settings_sources,'cli_setting_sources':'',
                  'worker_thinking':worker_thinking,'worker_tools':worker_tools,
                  'measured_red_progress_protocol':1 if measured_red_progress else None,
                  'global_instruction_inputs':global_instructions,'global_instruction_inventory_protocol':1,'code_inventory':code_inventory,
                  'code_inventory_sha256':_inventory_digest(code_inventory),
                  'python_cache_pinning':'generated __pycache__ excluded; source files pinned, external cache tamper requires explicit adapter',
                  'managed_settings':'remain enforced by Claude; not overridden or claimed pinned',
                  'remaining':'launcher wiring, model/effort resolution, MCP/plan-script adapters and shell enforcement'}
        (destination/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
        return manifest
    except BaseException:
        shutil.rmtree(destination);raise


def pin_launch_resources(destination, *, project, mcp=None, executables=None):
    """Freeze MCP bytes and record explicit runtime/dependency inventories.

    Executable paths and hashes are checked before dispatch; installed system
    runtimes are observed, not copied or claimed immutable during host upgrades.
    """
    destination=Path(destination);manifest_path=destination/'inputs.json'
    manifest=json.loads(manifest_path.read_text())
    source=Path(mcp) if mcp else Path(project)/'.mcp.json'
    raw=source.read_bytes() if source.exists() else b'{"mcpServers":{}}\n'
    if not isinstance(json.loads(raw),dict):raise InputPinError('MCP configuration must be an object')
    target=destination/'mcp.json'
    with target.open('xb') as stream:stream.write(raw)
    target.chmod(0o400)
    manifest['mcp']={'path':str(target.resolve()),'sha256':digest(target)}
    resolved=[]
    for name,raw_path in (executables or {'python':sys.executable,'claude':shutil.which('claude')}).items():
        if not raw_path:raise InputPinError('required executable unavailable: '+name)
        path=Path(raw_path).resolve(strict=True)
        resolved.append({'name':name,'path':str(path),'sha256':digest(path)})
    manifest['executables']=resolved
    names=('package-lock.json','pnpm-lock.yaml','yarn.lock','uv.lock','poetry.lock','requirements.txt','pyproject.toml')
    manifest['consumer_dependency_inventory']=[{'path':str((Path(project)/name).resolve()),'sha256':digest(Path(project)/name)}
        for name in names if (Path(project)/name).is_file()]
    manifest['settings_sha256']=digest(Path(manifest['worker_home'])/'settings.json')
    manifest['runtime_pinning']='fixed executable path/hash; system libraries require drain for host upgrade'
    manifest['plan_scripts']='toolkit references resolved via ILK_CODE_ROOT; consumer scripts remain gated worktree inputs'
    manifest['remaining']='code-only caller activation and external system-runtime upgrade coordination'
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n');manifest_path.chmod(0o600)
    return manifest


def verify_launch_resources(destination):
    destination=Path(destination).resolve()
    manifest=json.loads((destination/'inputs.json').read_text())
    for key,name in (('code_root','code'),('worker_home','worker-home')):
        if Path(manifest[key]).resolve()!=destination/name:
            raise InputPinError('private run input root contradicts prepared manifest')
    protocol=manifest.get('global_instruction_inventory_protocol')
    if protocol is not None and (type(protocol) is not int or protocol!=1):
        raise InputPinError('unknown global instruction inventory protocol')
    if protocol==1:
        if any(Path(row['path']).is_symlink() for row in manifest.get('global_instruction_inputs',[])):
            raise InputPinError('private global instruction link changed')
        actual=[str(path) for path in _global_instruction_paths(Path(manifest['worker_home']))]
        expected=sorted(row['path'] for row in manifest.get('global_instruction_inputs',[]))
        if actual!=expected:raise InputPinError('private global instruction inventory changed')
    paths=list(manifest.get('executables',[]))+[manifest['mcp']]+manifest.get('global_instruction_inputs',[])
    paths.append({'path':str(Path(manifest['worker_home'])/'settings.json'),'sha256':manifest['settings_sha256']})
    paths.extend({'path':row['pinned_script'],'sha256':row['pinned_sha256']} for row in manifest['hooks'])
    for row in manifest.get('context_bindings',[]):
        paths.append({'path':row['settings_path'],'sha256':row['settings_sha256']})
        if row.get('sandbox_path'):
            paths.append({'path':row['sandbox_path'],'sha256':row['sandbox_sha256']})
    for row in paths:
        if digest(Path(row['path']))!=row['sha256']:raise InputPinError('pinned run input changed: '+row['path'])
    return manifest


def bind_context(destination: Path, context_path: Path) -> Path:
    """Create immutable per-context enforcement settings before worker launch.

    The prepared base home is untouched, so old worker settings never switch
    during an active session. This API is still a candidate launcher dependency.
    """
    if os.environ.get('ILK_WORKER_SESSION','').strip():
        raise InputPinError('workers cannot bind authority settings')
    from iteration_context import read_context
    from worker_sandbox import profile
    destination=destination.resolve();context_path=context_path.resolve(strict=True)
    manifest_path=destination/'inputs.json';manifest=json.loads(manifest_path.read_text())
    context=read_context(context_path)
    if Path(context['code_root']).resolve()!=Path(manifest['code_root']).resolve():
        raise InputPinError('context code differs from prepared snapshot')
    if context.get('worker_sandbox_adapter')=='macos-two-layer':
        raise InputPinError('nested Mac sandbox is unavailable; brokered shell required')
    if context.get('worker_sandbox_adapter')!='macos-brokered-shell':
        raise InputPinError('explicit brokered OS boundary required')
    if context.get('measured_red_progress_protocol')!=manifest.get('measured_red_progress_protocol'):
        raise InputPinError('measured-red progress policy differs from frozen inputs')
    if context.get('private_worker_home')!=manifest['worker_home'] or context.get('worker_inputs_dir')!=str(destination):
        raise InputPinError('private CLI input roles contradict prepared home')
    broker_path=context.get('shell_broker_path')
    if not isinstance(broker_path,str) or not Path(broker_path).is_socket():
        raise InputPinError('owned shell broker is not ready')
    if context_path.stat().st_mode & 0o222:
        raise InputPinError('context must be frozen read-only')
    temporary=Path(context['worker_tmp_dir']).resolve(strict=True)
    diagnostic=context_path.parent/f"write-scope-{context['iteration']}.jsonl"
    profile(context,temporary=temporary,private_home=Path(manifest['worker_home']),diagnostic_log=diagnostic)
    source=Path(manifest['worker_home'])/'settings.json'
    settings=json.loads(source.read_text());observer=Path(context['code_root'])/'hooks/worker-write-scope.py'
    if settings.get('disableAllHooks'):
        raise InputPinError('code-only enforcement requires hooks enabled')
    wrapper=Path(context['code_root'])/'skills/ilk-loop/scripts/worker_shell.py'
    if not observer.is_file() or not wrapper.is_file():
        raise InputPinError('pinned scope/shell adapters absent')
    pre=settings.setdefault('hooks',{}).setdefault('PreToolUse',[])
    kept=[]
    for entry in pre:
        remaining=[]
        for hook in entry.get('hooks',[]):
            tokens=shlex.split(hook.get('command',''))
            if any(Path(token).name.endswith('worker-write-scope.py') for token in tokens): continue
            remaining.append(hook)
        if remaining: kept.append(dict(entry,hooks=remaining))
    kept.append({'matcher':'*','hooks':[{'type':'command','command':shlex.join([
        context['worker_shell_python'],str(observer),'--mode','enforce','--context',str(context_path)])}]})
    settings['hooks']['PreToolUse']=kept
    fingerprint=digest(context_path);directory=destination/'settings';directory.mkdir(mode=0o700,exist_ok=True)
    output=directory/(fingerprint+'.json')
    with output.open('x',encoding='utf-8') as stream:
        json.dump(settings,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    output.chmod(0o400)
    safe={key:value for key,value in settings.items() if key!='env'}
    row={'context_sha256':fingerprint,'context_path':str(context_path),'settings_path':str(output),
         'settings_sha256':digest(output),
         'settings_non_env_sha256':hashlib.sha256(json.dumps(safe,sort_keys=True).encode()).hexdigest(),
         'scope_mode':'enforce','shell_adapter':'macos-brokered-shell'}
    manifest.setdefault('context_bindings',[]).append(row)
    with tempfile.NamedTemporaryFile(mode='w',dir=destination,delete=False) as stream:
        temporary_manifest=Path(stream.name);json.dump(manifest,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    try:temporary_manifest.chmod(0o600);os.replace(temporary_manifest,manifest_path)
    finally:temporary_manifest.unlink(missing_ok=True)
    return output


def pin_context_policy(destination,context_path,policy_path):
    from iteration_context import read_context
    from run_registry import require_driver
    from plan_state import _sync_parent
    require_driver(read_context(context_path))
    target=Path(destination)/'inputs.json';manifest=json.loads(target.read_text())
    fingerprint=digest(Path(context_path));rows=[row for row in manifest.get('context_bindings',[])
                                                if row['context_sha256']==fingerprint]
    if len(rows)!=1 or rows[0].get('sandbox_path'):
        raise InputPinError('unique unbound policy record required')
    rows[0].update(sandbox_path=str(Path(policy_path).resolve(strict=True)),sandbox_sha256=digest(Path(policy_path)))
    with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(manifest,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600);require_driver(read_context(context_path));os.replace(temporary,target);_sync_parent(target)
    finally:temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--worker-home',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--revision',default='HEAD')
    parser.add_argument('--observe-scope',action='store_true')
    parser.add_argument('--project',type=Path)
    parser.add_argument('--mcp',type=Path)
    parser.add_argument('--worker-tools',choices=['shell','native'],default='shell')
    parser.add_argument('--worker-thinking',choices=['default','disabled'],default='default')
    parser.add_argument('--measured-red-progress',action='store_true')
    args=parser.parse_args()
    result=prepare(args.repo,args.worker_home,args.destination,revision=args.revision,observe_scope=args.observe_scope,project=args.project,worker_thinking=args.worker_thinking,worker_tools=args.worker_tools,measured_red_progress=args.measured_red_progress)
    if args.project:result=pin_launch_resources(args.destination,project=args.project,mcp=args.mcp)
    print(json.dumps({'toolkit_sha':result['toolkit_sha'],'hook_count':len(result['hooks']),
                      'scope_observation_only':result['scope_observation_only']}))


if __name__=='__main__':main()
