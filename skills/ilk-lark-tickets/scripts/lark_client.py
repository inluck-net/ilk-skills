"""Minimal Feishu (Lark) Bitable client. Stdlib only, no extra dependencies.

Reads credentials from ``$ILK_DATA_HOME/ilk-lark-tickets/config.json`` (default
``~/.ilk-data/ilk-lark-tickets/config.json``). The legacy
``~/.cursor/lark-tickets/config.json`` location is still honored as a
fallback for installs that predate the move under ~/.ilk-data:
{
  "app_id": "cli_xxx",
  "app_secret": "xxx",
  "projects": {
    "<project_name>": {
      "bitable_app_token": "Nxxx",
      "table_id": "tblxxx",
      "ticket_id_prefix": "T"
    }
  }
}
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

LARK_BASE = "https://open.feishu.cn"

# Credentials/cache live under the ilk data home, like the rest of the
# toolkit (cf. improvement_backlog.py). The legacy ~/.cursor location is
# kept as a read fallback so existing installs keep working pre-migration.
_LEGACY_DIR = Path.home() / ".cursor" / "lark-tickets"


def _resolve_data_root() -> Path:
    """Resolve the canonical data root via ``ilk_paths.ilk_data_root()``.

    Tries a relative ``sys.path`` insert to import ``ilk_paths`` from the
    sibling ``ilk-loop/scripts`` directory.  Falls back to the legacy
    inline resolver if ``ilk_paths`` is not importable (this module must
    remain stdlib-only / standalone).
    """
    try:
        here = Path(__file__).resolve()
        loop_scripts = here.parent.parent.parent / "ilk-loop" / "scripts"
        if loop_scripts.is_dir():
            if str(loop_scripts) not in sys.path:
                sys.path.insert(0, str(loop_scripts))
            import importlib
            import ilk_paths
            importlib.reload(ilk_paths)  # pick up env changes between calls
            return ilk_paths.ilk_data_root()
    except Exception:
        pass
    # Fallback: inline resolver (same precedence, kept in sync).
    env = os.environ.get("ILK_DATA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    env = os.environ.get("ILK_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".ilk-data"


def _resolve_data_dir() -> Path:
    return _resolve_data_root() / "ilk-lark-tickets"


def _resolve_config_path() -> Path:
    primary = _resolve_data_dir() / "config.json"
    if primary.exists():
        return primary
    legacy = _LEGACY_DIR / "config.json"
    if legacy.exists():
        return legacy
    return primary  # not found anywhere → report the canonical (new) path


CONFIG_PATH = _resolve_config_path()
TOKEN_CACHE = CONFIG_PATH.parent / ".token_cache.json"


# ---------------------------------------------------------------------------
# Config & project resolution
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"config not found: {CONFIG_PATH}\n"
            "Create it with at least: app_id, app_secret, projects.<name>."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def find_project_name(start: Path | None = None) -> str | None:
    """Walk up from cwd looking for a `.lark-project` marker file.

    The file's first non-empty line is the project name (key in config['projects']).
    Returns None if no marker is found.
    """
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        marker = parent / ".lark-project"
        if marker.exists():
            text = marker.read_text(encoding="utf-8").strip()
            if text:
                return text.splitlines()[0].strip()
    return None


def resolve_project(cfg: dict, name: str | None = None) -> tuple[str, dict]:
    name = name or find_project_name()
    if not name:
        raise SystemExit(
            "No project specified.\n"
            "Either pass --project=<name> or create a `.lark-project` file in the repo root."
        )
    projects = cfg.get("projects") or {}
    if name not in projects:
        raise SystemExit(
            f"Project '{name}' not in config.\n"
            f"Known projects: {sorted(projects)}"
        )
    return name, projects[name]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class LarkError(Exception):
    def __init__(self, code: int, msg: str, http_status: int = 0, body: str = ""):
        super().__init__(f"[{code}] {msg} (http={http_status})")
        self.code = code
        self.msg = msg
        self.http_status = http_status
        self.body = body


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    params: dict | None = None,
    body: dict | None = None,
    raw_response: bool = False,
) -> Any:
    url = LARK_BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    text = raw.decode("utf-8", errors="replace")
    if raw_response:
        return status, text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise LarkError(-1, f"non-json response: {text[:300]}", http_status=status, body=text)
    if not isinstance(payload, dict) or payload.get("code", 0) != 0:
        code = payload.get("code", -1) if isinstance(payload, dict) else -1
        msg = payload.get("msg", "unknown") if isinstance(payload, dict) else "unknown"
        raise LarkError(code, msg, http_status=status, body=text)
    return payload.get("data", {})


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_tenant_access_token(cfg: dict) -> str:
    if TOKEN_CACHE.exists():
        try:
            cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            if cache.get("app_id") == cfg["app_id"] and cache.get("expires_at", 0) > time.time() + 60:
                return cache["token"]
        except Exception:
            pass
    status, text = _request(
        "POST",
        "/open-apis/auth/v3/tenant_access_token/internal",
        body={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]},
        raw_response=True,
    )
    payload = json.loads(text)
    if payload.get("code", 0) != 0:
        raise LarkError(payload.get("code", -1), payload.get("msg", "auth failed"), http_status=status, body=text)
    token = payload["tenant_access_token"]
    expire = int(payload.get("expire", 7200))
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(
        json.dumps({"app_id": cfg["app_id"], "token": token, "expires_at": time.time() + expire}),
        encoding="utf-8",
    )
    return token


# ---------------------------------------------------------------------------
# Bitable: create + config helpers
# ---------------------------------------------------------------------------

def create_bitable(
    name: str,
    folder_token: str | None = None,
    token: str | None = None,
) -> dict:
    """Create a new Lark Bitable base and return ``{app_token, table_id, url}``.

    If *token* is ``None`` a tenant access token is acquired automatically.
    """
    cfg = load_config()
    if token is None:
        token = get_tenant_access_token(cfg)
    body: dict = {"name": name}
    if folder_token:
        body["folder_token"] = folder_token
    data = _request("POST", "/open-apis/bitable/v1/apps", token=token, body=body)
    app = data.get("app", data)
    return {
        "app_token": app.get("app_token"),
        "table_id": app.get("default_table_id"),
        "url": app.get("url"),
    }


def upsert_project_config(
    name: str,
    entry: dict,
    config_path: str | Path | None = None,
) -> None:
    """Atomically upsert ``projects.<name>`` in the Lark config file.

    Preserves every existing key (``app_id``, ``app_secret``, other projects).
    Write is atomic (tmp + ``os.replace``), utf-8, no BOM.
    """
    cfg_path = Path(config_path) if config_path else _resolve_config_path()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    projects = cfg.setdefault("projects", {})
    projects[name] = entry
    # Atomic write: tmp in same dir, then os.replace
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(cfg_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(cfg_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Bitable: client class
# ---------------------------------------------------------------------------

class BitableClient:
    def __init__(self, cfg: dict | None = None, project_name: str | None = None):
        self.cfg = cfg or load_config()
        self.project_name, self.project = resolve_project(self.cfg, project_name)
        self.app_token = self.project["bitable_app_token"]
        self.table_id = self.project["table_id"]
        self._token: str | None = None
        self._field_map: dict[str, dict] | None = None

    @property
    def token(self) -> str:
        if not self._token:
            self._token = get_tenant_access_token(self.cfg)
        return self._token

    def _path(self, *parts: str) -> str:
        return f"/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/" + "/".join(parts)

    # -- fields ----------------------------------------------------------------

    def list_fields(self, refresh: bool = False) -> dict[str, dict]:
        """Return {field_name: {field_id, type, ui_type, property}}."""
        if self._field_map and not refresh:
            return self._field_map
        page_token = None
        out: dict[str, dict] = {}
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = _request("GET", self._path("fields"), token=self.token, params=params)
            for item in data.get("items", []):
                out[item["field_name"]] = item
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        self._field_map = out
        return out

    def field_id(self, name: str) -> str:
        fmap = self.list_fields()
        if name not in fmap:
            raise SystemExit(f"field '{name}' not found. Known: {sorted(fmap)}")
        return fmap[name]["field_id"]

    # -- views ----------------------------------------------------------------

    def list_views(self) -> list[dict]:
        """All views on this table (paginated)."""
        out: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = _request("GET", self._path("views"), token=self.token, params=params)
            out.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return out

    def create_view(self, *, view_name: str, view_type: str) -> dict:
        """Create a grid/kanban/gallery/gantt/form view. Returns API `data` object."""
        return _request(
            "POST",
            self._path("views"),
            token=self.token,
            body={"view_name": view_name, "view_type": view_type},
        )

    def get_view(self, view_id: str) -> dict:
        return _request("GET", self._path("views", view_id), token=self.token)

    def patch_view(self, view_id: str, body: dict) -> dict:
        return _request("PATCH", self._path("views", view_id), token=self.token, body=body)

    def delete_view(self, view_id: str) -> dict:
        return _request("DELETE", self._path("views", view_id), token=self.token)

    def patch_form_meta(self, form_id: str, body: dict) -> dict:
        """Update form sharing / title / description. form_id is the form view_id."""
        path = f"/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/forms/{form_id}"
        return _request("PATCH", path, token=self.token, body=body)

    # -- records ---------------------------------------------------------------

    def list_records(
        self,
        *,
        filter_expr: str | None = None,
        sort: list[dict] | None = None,
        field_names: list[str] | None = None,
        page_size: int = 100,
        max_records: int | None = None,
    ) -> list[dict]:
        """List records using the search endpoint (POST).

        filter_expr example:
          {"conjunction": "and", "conditions": [
            {"field_name": "状态", "operator": "is", "value": ["新建"]}
          ]}
        """
        body: dict = {"automatic_fields": False}
        if filter_expr:
            body["filter"] = filter_expr
        if sort:
            body["sort"] = sort
        if field_names:
            body["field_names"] = field_names
        out: list[dict] = []
        page_token = None
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = _request(
                "POST",
                self._path("records", "search"),
                token=self.token,
                params=params,
                body=body,
            )
            out.extend(data.get("items", []))
            if max_records and len(out) >= max_records:
                return out[:max_records]
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return out

    def get_record(self, record_id: str) -> dict:
        return _request("GET", self._path("records", record_id), token=self.token)

    def create_record(self, fields: dict) -> dict:
        return _request("POST", self._path("records"), token=self.token, body={"fields": fields})

    def update_record(self, record_id: str, fields: dict) -> dict:
        return _request("PUT", self._path("records", record_id), token=self.token, body={"fields": fields})

    def batch_update(self, records: list[dict]) -> dict:
        """records: [{record_id, fields}, ...]"""
        return _request(
            "POST",
            self._path("records", "batch_update"),
            token=self.token,
            body={"records": records},
        )

    # -- attachments -----------------------------------------------------------

    def download_attachment(self, file_token: str, dest: Path) -> Path:
        """Download an attachment by file_token to dest."""
        url = f"{LARK_BASE}/open-apis/drive/v1/medias/{file_token}/download"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
        return dest


# ---------------------------------------------------------------------------
# Convenience: ticket-id generation
# ---------------------------------------------------------------------------

def next_ticket_id(client: BitableClient, year: int | None = None) -> str:
    """Generate next ticket id like T-2026-0001 by scanning existing rows.

    Looks at the 'ticket_id' field of all records and increments the largest
    sequence found for the given year. Year defaults to current UTC year.
    """
    import datetime as _dt
    year = year or _dt.datetime.utcnow().year
    prefix = client.project.get("ticket_id_prefix", "T")
    needle = f"{prefix}-{year}-"
    records = client.list_records(field_names=["ticket_id"])
    max_seq = 0
    for r in records:
        v = (r.get("fields") or {}).get("ticket_id")
        text = _flatten_text(v)
        if text and text.startswith(needle):
            try:
                seq = int(text[len(needle):])
                max_seq = max(max_seq, seq)
            except ValueError:
                continue
    return f"{prefix}-{year}-{max_seq + 1:04d}"


def _flatten_text(value: Any) -> str:
    """Bitable text fields come back as list of segment dicts; flatten to str."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for seg in value:
            if isinstance(seg, dict):
                parts.append(seg.get("text") or seg.get("name") or "")
            else:
                parts.append(str(seg))
        return "".join(parts)
    if isinstance(value, dict):
        return value.get("text") or value.get("name") or ""
    return str(value)
