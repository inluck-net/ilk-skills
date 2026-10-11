"""Exercise the authenticated loopback adapter without credentials/API calls."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import urllib.error
import urllib.request
import pytest
from iteration_clock import monotonic
from iteration_context import ContextError
from provider_control import session,validate_settings


MODEL='mimo-v2.6-pro'


def test_owned_profile_rewrites_only_pinned_api_and_hides_upstream_key(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    settings={'env':{'ANTHROPIC_BASE_URL':'https://token-plan-cn.xiaomimimo.com/anthropic',
                     'ANTHROPIC_AUTH_TOKEN':'upstream-fixture-key'}}
    private=tmp_path/'inputs/settings';private.mkdir(parents=True)
    source=private/'settings.json';source.write_text(json.dumps(settings));original=source.read_bytes()
    calls=[]
    @contextmanager
    def transport(url,payload,headers,timeout):
        calls.append((url,json.loads(payload),headers,timeout))
        response=io.BytesIO(b'fixture response');response.status=200;response.headers={'Content-Type':'text/event-stream'}
        yield response
    ctx={'iteration_deadline_monotonic':monotonic()+10,'worker_inputs_dir':str(private.parent)}
    manifest={'worker_thinking':'disabled','requested_model':MODEL}
    with session(ctx,tmp_path/'ctx.json',manifest,source,MODEL,transport=transport) as control:
        client=Path(control['settings_path']);data=json.loads(client.read_text())
        env=data['env'];cap=env['ANTHROPIC_AUTH_TOKEN'];assert cap!='upstream-fixture-key'
        assert 'upstream-fixture-key' not in client.read_text()
        def request(path,body,auth=cap):
            return urllib.request.urlopen(urllib.request.Request(env['ANTHROPIC_BASE_URL']+path,
                data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+auth,'Content-Type':'application/json'}))
        body={'model':MODEL,'thinking':{'type':'adaptive'},'output_config':{'effort':'high'},'messages':[]}
        with request('/v1/messages',body) as result:assert result.read()==b'fixture response'
        with pytest.raises(urllib.error.HTTPError) as refused:request('/v1/messages',body,auth='foreign-cap')
        assert refused.value.code==403
        with pytest.raises(urllib.error.HTTPError):request('/v1/messages',dict(body,model='other-model'))
        with pytest.raises(urllib.error.HTTPError):request('/unrelated',body)
        assert len(calls)==1
        url,forwarded,headers,timeout=calls[0]
        assert url=='https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages'
        assert forwarded['thinking']=={'type':'disabled'} and forwarded['output_config']=={'effort':'high'}
        assert headers['Authorization']=='Bearer upstream-fixture-key'
        assert timeout<=10 and 'upstream-fixture-key' not in json.dumps(control['observation'])
    assert source.read_bytes()==original and not client.exists()


@pytest.mark.parametrize('endpoint,model',[
    ('http://api.xiaomimimo.com/anthropic',MODEL),('https://foreign.invalid/anthropic',MODEL),
    ('https://api.xiaomimimo.com/anthropic?token=fixture',MODEL),
    ('https://api.xiaomimimo.com/anthropic','other-model')])
def test_unsupported_provider_profile_refuses(endpoint,model):
    with pytest.raises(ContextError,match='supported official'):
        validate_settings({'env':{'ANTHROPIC_BASE_URL':endpoint,'ANTHROPIC_AUTH_TOKEN':'fixture'}},model)


def test_default_profile_does_not_start_adapter_or_copy_settings(tmp_path):
    missing=tmp_path/'missing'
    with session({},tmp_path/'ctx.json',{},missing,MODEL) as control:
        assert control=={'settings_path':str(missing),'env':{},'observation':{'mode':'default'}}
    assert not list(tmp_path.iterdir())
