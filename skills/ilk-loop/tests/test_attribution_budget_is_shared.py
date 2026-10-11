"""A node loop must spend one budget, rather than granting each node a new one."""
import subprocess
import red_owner


def test_all_baseline_nodes_share_one_budget(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    clock=[100.0];timeouts=[];original=subprocess.run
    monkeypatch.setattr(red_owner,'_clock',lambda:clock[0],raising=False)
    def bounded(*args,**kwargs):
        cap=kwargs['timeout'];timeouts.append(cap)
        elapsed=min(.1,cap);clock[0]+=elapsed
        return (0,'','',False) if cap>=.1 else (None,'','',True)
    monkeypatch.setattr(red_owner,'_bounded_run',bounded)
    def old_shell(*args,**kwargs):
        if kwargs.get('shell'):
            timeouts.append(kwargs['timeout']);clock[0]+=.1
            return subprocess.CompletedProcess(args[0],0,stdout='',stderr='')
        return original(*args,**kwargs)
    monkeypatch.setattr(red_owner.subprocess,'run',old_shell)
    result=red_owner._run_at_base_worktree(repo,sha,'python3 -m pytest',['one','two','three'],.15)
    assert len(timeouts)<=2,timeouts
    assert timeouts[-1]<timeouts[0],timeouts
    assert any(value=='unmeasured' for value in result.values()),result


def test_cli_clips_to_an_available_original_iteration_deadline(tmp_path,monkeypatch,capsys):
    import iteration_context,iteration_clock
    monkeypatch.setattr(iteration_context,'read_context',lambda:{'iteration_deadline_monotonic':123.5})
    monkeypatch.setattr(iteration_clock,'monotonic',lambda:123.)
    seen=[]
    monkeypatch.setattr(red_owner,'attribute_red',lambda *a,**kw:seen.append(kw['budget_s']) or {'verdict':'unmeasured'})
    assert red_owner.main(['--attribute','--repo',str(tmp_path),'--iteration-base','a'*40,'--head','b'*40,'--cmd','focused-check','--budget-s','300'])==0
    assert seen==[.5]


def test_unknown_bisect_sample_cannot_become_a_first_red_commit(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    def commit(message):
        subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm',message],check=True)
        return subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    base=commit('base');head=commit('change [plan:owner#step-0]')
    samples=iter([True,None]);monkeypatch.setattr(red_owner,'_run_test',lambda *a:next(samples))
    result=red_owner.bisect_red_owner(repo,base,head,'focused-check',['node'],budget_s=5)
    assert result['first_red'] is None and result['reason']=='unmeasured'


def test_capped_sample_is_unknown_rather_than_red(tmp_path,monkeypatch):
    monkeypatch.setattr(red_owner,'_bounded_run',lambda *a,**k:(None,'','',True))
    assert red_owner._run_test(tmp_path,'focused-check',[]) is None


def test_bisect_cli_obeys_original_deadline(tmp_path, monkeypatch, capsys):
    import iteration_context, iteration_clock
    monkeypatch.setattr(iteration_context, 'read_context', lambda: {
        'iteration_deadline_monotonic': 123.5})
    monkeypatch.setattr(iteration_clock, 'monotonic', lambda: 123.)
    seen = []
    def bisect(*args):
        seen.append(args[-1])
        return {'first_red': None, 'reason': 'unmeasured'}
    monkeypatch.setattr(red_owner, 'bisect_red_owner', bisect)
    assert red_owner.main(['--repo', str(tmp_path), '--base', 'a'*40,
        '--head', 'b'*40, '--cmd', 'focused-check', '--budget-s', '300']) == 0
    assert seen == [.5]


def test_bisect_cli_refuses_invalid_original_deadline(tmp_path, monkeypatch, capsys):
    import iteration_context
    monkeypatch.setattr(iteration_context, 'read_context', lambda: {
        'iteration_deadline_monotonic': float('nan')})
    monkeypatch.setattr(red_owner, 'bisect_red_owner',
                        lambda *a: __import__('pytest').fail('invalid deadline started bisect'))
    assert red_owner.main(['--repo', str(tmp_path), '--base', 'a'*40,
        '--head', 'b'*40, '--cmd', 'focused-check']) == 0
    result = __import__('json').loads(capsys.readouterr().out)
    assert result['first_red'] is None and result['reason'] == 'unmeasured'
