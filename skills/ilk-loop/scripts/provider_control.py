"""Opt-in owned MiMo thinking control; no shared settings or default changes.

Claude's thinking-off controls omit the wire field. MiMo defaults omission to
enabled, so explicit disable requires a provider adapter. Loopback clients use
an ephemeral capability, never the upstream credential. The driver forwards
only the pinned model's message/count-token API over verified HTTPS.
"""
from __future__ import annotations
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from iteration_clock import monotonic
from iteration_context import ContextError
from run_registry import require_driver

HOSTS={'api.xiaomimimo.com','token-plan-cn.xiaomimimo.com',
       'token-plan-sgp.xiaomimimo.com','token-plan-ams.xiaomimimo.com'}
MODELS={'mimo-v2.6-pro','mimo-v2.6-flash','mimo-v2.6-pro-ultraspeed'}


def validate_settings(settings,model):
    env=settings.get('env',{});upstream=env.get('ANTHROPIC_BASE_URL','')
    parsed=urllib.parse.urlsplit(upstream)
    if (parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.port not in {None,443}
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path.rstrip('/')!='/anthropic' or model not in MODELS):
        raise ContextError('thinking adapter needs an explicit supported official MiMo endpoint/model')
    if env.get('ANTHROPIC_CUSTOM_HEADERS'):
        raise ContextError('thinking adapter custom credential-header adapter unavailable')
    credential=env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')
    if not isinstance(credential,str) or not credential:
        raise ContextError('thinking adapter needs privately provisioned credential')
    return upstream.rstrip('/'),env


def rewrite(body,model):
    if not isinstance(body,dict) or body.get('model')!=model:
        raise ContextError('provider request contradicts pinned model')
    body=dict(body,thinking={'type':'disabled'})
    return body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def upstream_response(url,payload,headers,timeout):
    request=urllib.request.Request(url,data=payload,headers=headers,method='POST')
    return urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout)


@contextmanager
def session(context,path,manifest,settings_path,model,*,transport=upstream_response):
    mode=manifest.get('worker_thinking','default')
    if mode=='default':
        yield {'settings_path':str(settings_path),'env':{},'observation':{'mode':'default'}}
        return
    if mode!='disabled':raise ContextError('unknown frozen thinking profile')
    require_driver(context)
    client_directory=(Path(context['worker_inputs_dir'])/'settings').resolve(strict=True)
    if not Path(settings_path).resolve().is_relative_to(client_directory):
        raise ContextError('provider settings are outside frozen private bindings')
    settings=json.loads(Path(settings_path).read_text())
    requested=model or manifest.get('requested_model')
    if requested!=manifest.get('requested_model'):
        raise ContextError('thinking profile model contradicts frozen launch request')
    upstream,credentials=validate_settings(settings,requested)
    capability=secrets.token_urlsafe(32);observations=[];deadline=context['iteration_deadline_monotonic']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def error(self,status):
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"api_error","message":"Owned provider control refused or upstream failed"}}')
        def do_POST(self):
            supplied=self.headers.get('Authorization','').removeprefix('Bearer ')
            supplied=supplied or self.headers.get('x-api-key','')
            if not hmac.compare_digest(supplied,capability):self.error(403);return
            parsed=urllib.parse.urlsplit(self.path)
            if parsed.path not in {'/v1/messages','/v1/messages/count_tokens'} or parsed.fragment:
                self.error(404);return
            try:
                require_driver(context)
                remaining=deadline-monotonic()
                if remaining<=0:raise ContextError('original provider deadline reached')
                self.connection.settimeout(remaining)
                length=int(self.headers.get('Content-Length','0'))
                if length<=0:raise ContextError('provider request length absent')
                body=json.loads(self.rfile.read(length))
                if body.get('model')!=requested:raise ContextError('provider model changed')
                if parsed.path=='/v1/messages':
                    body=rewrite(body,requested)
                headers={key:value for key,value in self.headers.items()
                         if key.lower() not in {'host','content-length','connection','authorization','x-api-key'}}
                if credentials.get('ANTHROPIC_AUTH_TOKEN'):
                    headers['Authorization']='Bearer '+credentials['ANTHROPIC_AUTH_TOKEN']
                else:headers['x-api-key']=credentials['ANTHROPIC_API_KEY']
                with transport(upstream+self.path,json.dumps(body).encode(),headers,remaining) as response:
                    self.send_response(response.status)
                    self.send_header('Content-Type',response.headers.get('Content-Type','application/json'));self.end_headers()
                    observations.append({'model':requested,'thinking':'disabled',
                                         'api':parsed.path,'status':response.status})
                    while True:
                        require_driver(context)
                        if monotonic()>=deadline:raise ContextError('original provider deadline reached')
                        chunk=response.read1(65536)
                        if not chunk:break
                        self.wfile.write(chunk);self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
            except urllib.error.HTTPError as exc:self.error(exc.code)
            except Exception:self.error(502)
    class Server(ThreadingHTTPServer):
        daemon_threads=False
        def get_request(self):
            connection,address=super().get_request()
            remaining=deadline-monotonic()
            if remaining<=0:
                connection.close();raise TimeoutError('original provider deadline reached')
            connection.settimeout(remaining)
            return connection,address
    server=Server(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client_env=dict(settings.get('env',{}),ANTHROPIC_BASE_URL='http://127.0.0.1:'+str(server.server_port),
                    ANTHROPIC_AUTH_TOKEN=capability,ANTHROPIC_API_KEY=capability)
    client=dict(settings,env=client_env)
    client_path=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=client_directory,prefix='provider-client-',suffix='.json',delete=False) as stream:
            client_path=Path(stream.name);json.dump(client,stream);stream.flush();os.fsync(stream.fileno())
        client_path.chmod(0o400)
        yield {'settings_path':str(client_path),'env':{key:client_env[key] for key in
               ('ANTHROPIC_BASE_URL','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_API_KEY')},
               'observation':{'mode':'disabled','adapter':'owned-mimo-anthropic','requests':observations}}
    finally:
        server.shutdown();server.server_close();thread.join()
        if client_path:client_path.unlink(missing_ok=True)
