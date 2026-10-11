#!/usr/bin/env python3
"""Prepare a private pinned toolkit/settings/hook home for a new run.

Candidate preparation API, not yet wired to every launcher. Credentials are
explicitly copied only to private settings; manifest omits their values. Unknown
shell hook wrappers refuse preparation instead of retaining live-home commands.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile


class InputPinError(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_script(command):
    tokens=shlex.split(command)
    if not tokens or any(token in {';','&&','||','|','>'} for token in tokens):
        raise InputPinError('unsupported shell hook wrapper')
    candidates=[]
    for index,token in enumerate(tokens):
        expanded=Path(os.path.expanduser(os.path.expandvars(token)))
        if index==0 and expanded.name in {'python','python3','bash','sh','pwsh','powershell','python.exe','powershell.exe'}:
            continue
        if expanded.is_file() and (index==0 or expanded.suffix in {'.py','.sh','.ps1'}):
            candidates.append((index,expanded.resolve()))
    if len(candidates)!=1:
        raise InputPinError('hook must name exactly one explicit script')
    return tokens,*candidates[0]


def prepare(repo: Path, worker_home: Path, destination: Path, *, revision='HEAD', observe_scope=False):
    repo=repo.resolve();worker_home=worker_home.resolve();destination=destination.resolve()
    if destination.exists(): raise InputPinError('run input destination already exists')
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse',revision+'^{commit}'],text=True).strip()
    archive=subprocess.check_output(['git','-C',str(repo),'archive',sha])
    source=json.loads((worker_home/'settings.json').read_text(encoding='utf-8-sig'))
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
        settings=home/'settings.json';settings.write_text(json.dumps(pinned,indent=2)+'\n');settings.chmod(0o600)
        # Exclude credentials and arbitrary env values from public provenance.
        safe={key:value for key,value in pinned.items() if key!='env'}
        env=pinned.get('env',{})
        manifest={'schema_version':1,'toolkit_sha':sha,'code_root':str(code),'worker_home':str(home),
                  'settings_non_env_sha256':hashlib.sha256(json.dumps(safe,sort_keys=True).encode()).hexdigest(),
                  'env_keys':sorted(env),'requested_model':env.get('ANTHROPIC_MODEL') or pinned.get('model'),
                  'hooks':observations,'credentials_provisioned':'private settings copy; no values in manifest',
                  'scope_observation_only':observe_scope,
                  'remaining':'launcher wiring, model/effort resolution, MCP/plan-script adapters and shell enforcement'}
        (destination/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
        return manifest
    except BaseException:
        shutil.rmtree(destination);raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--worker-home',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--revision',default='HEAD')
    parser.add_argument('--observe-scope',action='store_true')
    args=parser.parse_args()
    result=prepare(args.repo,args.worker_home,args.destination,revision=args.revision,observe_scope=args.observe_scope)
    print(json.dumps({'toolkit_sha':result['toolkit_sha'],'hook_count':len(result['hooks']),
                      'scope_observation_only':result['scope_observation_only']}))


if __name__=='__main__':main()
