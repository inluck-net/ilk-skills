"""Explicit Mac OS-boundary integration fixtures; no live repos or network hosts."""
from pathlib import Path
import subprocess
import sys
import pytest
from worker_sandbox import profile
from iteration_context import ContextError
pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='explicit Mac sandbox integration')


def fixture(tmp_path):
    root=tmp_path/'work';root.mkdir();temporary=tmp_path/'private-tmp';temporary.mkdir()
    home=tmp_path/'private-home';home.mkdir();(home/'hooks').mkdir()
    ctx={key:str(tmp_path/key) for key in ('plans_dir','runtime_dir','data_root','code_root')}
    for key in ctx:Path(ctx[key]).mkdir()
    ctx.update(exec_tree=str(root),clone=str(root),mode='none')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    return ctx,temporary,home


def run(profile_text,*arguments):
    return subprocess.run(['/usr/bin/sandbox-exec','-p',profile_text,*arguments],capture_output=True,text=True,timeout=10)


def test_shell_can_write_code_but_not_control_plane_git_or_foreign_paths(tmp_path):
    ctx,temporary,home=fixture(tmp_path);policy=profile(ctx,temporary=temporary,shell=True)
    root=Path(ctx['exec_tree']);target=root/'accepted.py'
    python=['/usr/bin/python3','-c','import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("fixture")']
    assert run(policy,*python,str(target)).returncode==0
    for denied in (tmp_path/'outside.py',root/'.git/index',Path(ctx['plans_dir'])/'plan.md',home/'settings.json',root/'AGENTS.md',root/'CLAUDE.local.md'):
        before=denied.read_bytes() if denied.exists() else None
        cp=run(policy,*python,str(denied));assert cp.returncode!=0
        assert (denied.read_bytes() if denied.exists() else None)==before
    assert run(policy,*python,str(temporary/'cache')).returncode==0
    before=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True)
    cp=run(policy,'/usr/bin/git','-C',str(root),'add','accepted.py')
    assert cp.returncode!=0
    assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True)==before


def test_shell_network_is_denied_before_connecting(tmp_path):
    ctx,temporary,_=fixture(tmp_path);policy=profile(ctx,temporary=temporary,shell=True)
    cp=run(policy,'/usr/bin/python3','-c','import socket;socket.create_connection(("127.0.0.1",9))')
    assert cp.returncode!=0 and 'PermissionError' in cp.stderr


def test_tools_can_stop_their_own_children_but_not_independent_peers(tmp_path):
    ctx,temporary,_=fixture(tmp_path);policy=profile(ctx,temporary=temporary,shell=True)
    child='import subprocess,sys;p=subprocess.Popen([sys.executable,"-c","import threading;threading.Event().wait()"]);p.terminate();assert p.wait()==-15'
    assert run(policy,'/usr/bin/python3','-I','-c',child).returncode==0
    peer=subprocess.Popen(['/usr/bin/python3','-I','-c','import threading;threading.Event().wait()'])
    try:
        denied=run(policy,'/usr/bin/python3','-I','-c',f'import os,signal;os.kill({peer.pid},signal.SIGTERM)')
        assert denied.returncode!=0 and peer.poll() is None
    finally:peer.terminate();peer.wait()


def test_cli_and_shell_can_redirect_to_null_without_broadening_scope(tmp_path):
    ctx,temporary,home=fixture(tmp_path)
    for policy in (profile(ctx,temporary=temporary,shell=True),
                   profile(ctx,temporary=temporary,private_home=home)):
        cp=run(policy,'/bin/bash','-c','printf ignored > /dev/null')
        assert cp.returncode==0,cp.stderr
        cp=run(policy,'/bin/bash','-c','printf forged > "$1"','fixture',str(tmp_path/'outside'))
        assert cp.returncode!=0 and not (tmp_path/'outside').exists()


def test_frozen_kernel_scope_denies_writes_creation_and_ancestor_rename(tmp_path):
    ctx,temporary,_=fixture(tmp_path);root=Path(ctx['exec_tree'])
    (root/'driver').mkdir();protected=root/'driver/authority.py';protected.write_text('owner')
    ctx['kernel_list']={'loop_kernel_policy':'owner-only','rules':[{'path':'driver/authority.py'}],
                        'kernel_basenames':[{'name':'conftest.py'}]}
    policy=profile(ctx,temporary=temporary,shell=True)
    for target in (protected,root/'conftest.py',root/'driver/conftest.py'):
        cp=run(policy,'/usr/bin/python3','-c',
               'from pathlib import Path;Path(__import__("sys").argv[1]).write_text("worker")',str(target))
        assert cp.returncode!=0
    cp=run(policy,'/bin/mv',str(root/'driver'),str(root/'moved-driver'))
    assert cp.returncode!=0 and protected.read_text()=='owner'
    cp=run(policy,'/usr/bin/python3','-c','from pathlib import Path;Path(__import__("sys").argv[1]).write_text("allowed")',str(root/'driver/report.md'))
    assert cp.returncode==0 and (root/'driver/report.md').read_text()=='allowed'


def test_parent_home_is_writable_but_settings_and_hooks_are_pinned(tmp_path):
    ctx,temporary,home=fixture(tmp_path);policy=profile(ctx,temporary=temporary,private_home=home)
    python=['/usr/bin/python3','-c','import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("fixture")']
    assert run(policy,*python,str(home/'session.jsonl')).returncode==0
    for target in (home/'settings.json',home/'hooks/changed.py'):
        assert run(policy,*python,str(target)).returncode!=0
        assert not target.exists()


def test_symlink_cannot_escape_shell_write_scope(tmp_path):
    ctx,temporary,_=fixture(tmp_path);root=Path(ctx['exec_tree'])
    (root/'escape').symlink_to(Path(ctx['plans_dir']),target_is_directory=True)
    cp=run(profile(ctx,temporary=temporary,shell=True),'/usr/bin/python3','-c','from pathlib import Path;Path(__import__("sys").argv[1]).write_text("escaped")',str(root/'escape/changed.md'))
    assert cp.returncode!=0
    assert not (Path(ctx['plans_dir'])/'changed.md').exists()


def test_pinned_wrapper_denies_shell_expansion_and_control_writes(tmp_path):
    import json,os
    from worker_shell import canonical_command,admitted_command
    from iteration_context import write_context
    ctx,temporary,_=fixture(tmp_path)
    root=Path(ctx['exec_tree']);repo=Path(__file__).resolve().parents[3]
    ctx.update(schema_version=1,run_id='wrapper-fixture',iteration=1,
        code_root=str(repo),worker_shell_python=sys.executable,
        worker_sandbox_adapter='macos-brokered-shell',worker_tmp_dir=str(temporary))
    path=tmp_path/'ctx.json';write_context(path,ctx)
    command=canonical_command(ctx,path,'printf fixture > accepted.txt')
    assert admitted_command(command,ctx,path)
    assert not admitted_command(command+'; touch escaped',ctx,path)
    assert not admitted_command(command.replace("'printf fixture > accepted.txt'",'"$(touch escaped)"'),ctx,path)


def test_native_and_shell_cannot_rewrite_nested_instructions(tmp_path):
    ctx,temporary,home=fixture(tmp_path);root=Path(ctx['exec_tree'])
    nested=root/'module';nested.mkdir();target=nested/'CLAUDE.md';target.write_text('frozen')
    for shell in (False,True):
        policy=profile(ctx,temporary=temporary,private_home=home if not shell else None,shell=shell)
        cp=run(policy,'/usr/bin/python3','-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_text("changed")',str(target))
        assert cp.returncode!=0 and target.read_text()=='frozen'
        scratch=temporary/'CLAUDE.md'
        assert run(policy,'/usr/bin/python3','-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_text("test fixture")',str(scratch)).returncode==0
        assert scratch.read_text()=='test fixture'
