"""CLI entry point for the ilk-lark-tickets skill.

Usage:
  python cli.py list [--status STATUS] [--limit N] [--project NAME]
  python cli.py show <record_id> [--project NAME]
  python cli.py pull-new [--project NAME]
  python cli.py update <record_id> --field NAME=VALUE [--field NAME=VALUE ...] [--project NAME]
  python cli.py next-id [--project NAME]
  python cli.py download <record_id> <field_name> --to DIR [--project NAME]
  python cli.py fields [--project NAME]
  python cli.py setup-issue-views [--project NAME]
       [--kanban-name STR] [--form-name STR] [--stack-field NAME]

All commands print JSON to stdout (one object per line, or a single object).
Errors go to stderr with non-zero exit code.

VALUE parsing for --field:
  - If looks like JSON (starts with [ { " or is true/false/null/number), parsed as JSON.
  - Otherwise treated as a plain string (will be wrapped to text-segment list when needed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr so Chinese field names render correctly in
# Windows terminals (which default to GBK / cp936). Python 3.7+.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# allow running as `python cli.py ...` regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))

import lark_client  # noqa: E402
from lark_client import (  # noqa: E402
    BitableClient,
    LarkError,
    _flatten_text,
    _request,
    create_bitable,
    get_tenant_access_token,
    load_config,
    next_ticket_id,
    upsert_project_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _parse_field_value(raw: str):
    """Parse a CLI --field value. Tries JSON first, falls back to plain string."""
    s = raw.strip()
    if s.startswith(("{", "[", '"')) or s in ("true", "false", "null"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    if s and (s[0] in "-0123456789"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return s


def _normalize_fields_for_write(client: BitableClient, raw_fields: dict) -> dict:
    """Convert plain CLI values into the shapes Bitable's records API expects.

    Rules per ui_type (Bitable v1 records.update):
      - Text:        plain string (NOT segment list)
      - SingleSelect/MultiSelect: option name string / list-of-strings
      - Number/Phone/Email/DateTime: pass through
      - Url:         {"link": str, "text": str}
      - User:        list of {"id": "ou_xxx"}; passed through
      - Attachment:  list of {"file_token": "..."} (must be uploaded first); passed through
      - Read-only fields (CreatedTime/ModifiedTime/CreatedUser/ModifiedUser/AutoNumber):
        rejected with a helpful error.
    """
    READONLY = {"CreatedTime", "ModifiedTime", "CreatedUser", "ModifiedUser", "AutoNumber"}
    fmap = client.list_fields()
    out: dict = {}
    for name, value in raw_fields.items():
        if name not in fmap:
            raise SystemExit(f"unknown field: {name}")
        ui = fmap[name].get("ui_type") or ""
        if ui in READONLY:
            raise SystemExit(f"field '{name}' is read-only ({ui}); cannot write")
        if ui == "Url" and isinstance(value, str):
            out[name] = {"link": value, "text": value}
        else:
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_fields(args):
    client = BitableClient(project_name=args.project)
    fmap = client.list_fields()
    rows = []
    for name, info in fmap.items():
        rows.append({
            "field_id": info.get("field_id"),
            "field_name": name,
            "type": info.get("type"),
            "ui_type": info.get("ui_type"),
        })
    _print(rows)


def cmd_list(args):
    client = BitableClient(project_name=args.project)
    filt = None
    if args.status:
        filt = {
            "conjunction": "and",
            "conditions": [
                {"field_name": "状态", "operator": "is", "value": [args.status]}
            ],
        }
    records = client.list_records(filter_expr=filt, max_records=args.limit)
    out = []
    for r in records:
        f = r.get("fields") or {}
        out.append({
            "record_id": r.get("record_id"),
            "ticket_id": _flatten_text(f.get("ticket_id")),
            "title": _flatten_text(f.get("标题")),
            "status": f.get("状态"),
            "type": f.get("类型"),
            "urgency": f.get("紧急度"),
            "ai_priority": f.get("AI 优先级建议"),
            "module": _flatten_text(f.get("涉及模块")),
            "source": f.get("录入端"),
        })
    _print(out)


def cmd_show(args):
    client = BitableClient(project_name=args.project)
    record = client.get_record(args.record_id)
    _print(record)


def cmd_pull_new(args):
    """Pull all 状态=新建 tickets, with full field content for AI triage."""
    client = BitableClient(project_name=args.project)
    filt = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "状态", "operator": "is", "value": ["新建"]}
        ],
    }
    records = client.list_records(filter_expr=filt)
    fmap = client.list_fields()
    out = []
    for r in records:
        f = r.get("fields") or {}
        readable = {}
        for name, info in fmap.items():
            v = f.get(name)
            if v is None:
                continue
            if info.get("ui_type") == "Text":
                readable[name] = _flatten_text(v)
            else:
                readable[name] = v
        out.append({
            "record_id": r.get("record_id"),
            "fields": readable,
        })
    _print(out)


def cmd_update(args):
    client = BitableClient(project_name=args.project)
    raw_fields: dict = {}
    for spec in args.field or []:
        if "=" not in spec:
            raise SystemExit(f"--field must be NAME=VALUE, got: {spec}")
        name, _, value = spec.partition("=")
        raw_fields[name.strip()] = _parse_field_value(value)
    fields = _normalize_fields_for_write(client, raw_fields)
    result = client.update_record(args.record_id, fields)
    _print(result)


def cmd_next_id(args):
    client = BitableClient(project_name=args.project)
    _print({"ticket_id": next_ticket_id(client)})


def cmd_download(args):
    client = BitableClient(project_name=args.project)
    record = client.get_record(args.record_id)
    f = (record.get("record") or record).get("fields", {})
    if args.field_name not in f:
        raise SystemExit(f"field '{args.field_name}' empty or missing on record")
    attachments = f[args.field_name]
    if not isinstance(attachments, list):
        raise SystemExit(f"field '{args.field_name}' is not an attachment field")
    out_dir = Path(args.to)
    saved = []
    for i, att in enumerate(attachments):
        token = att.get("file_token") if isinstance(att, dict) else None
        name = att.get("name") if isinstance(att, dict) else f"att-{i}"
        if not token:
            continue
        dest = out_dir / f"{args.record_id}__{i:02d}__{name}"
        client.download_attachment(token, dest)
        saved.append(str(dest))
    _print({"saved": saved})


def _find_view(views: list[dict], name: str, view_type: str) -> dict | None:
    for v in views:
        if v.get("view_name") == name and v.get("view_type") == view_type:
            return v
    return None


def ensure_issue_views(
    client: BitableClient,
    *,
    kanban_name: str = "工单看板",
    form_name: str = "提交新工单",
    stack_field: str = "状态",
    form_description: str = "请描述问题：现象、页面链接、期望与实际结果。可选上传截图。提交后运维/研发会分拣到看板列。",
    shared_limit: str = "tenant_editable",
    delay_s: int = 3,
) -> list[dict]:
    """Create Kanban + Form views for the ticket table; enable form sharing.

    Idempotent: finds existing views by name+type before creating.
    Returns a list of step dicts (kanban created/exists, form created/exists, etc.)
    """
    import time

    wait = max(1, int(delay_s))
    steps: list[dict] = []

    group_fid = client.field_id(stack_field)

    views = client.list_views()
    kb = _find_view(views, kanban_name, "kanban")
    if kb:
        kanban_id = kb["view_id"]
        steps.append({"kanban": "exists", "view_id": kanban_id})
    else:
        data = client.create_view(view_name=kanban_name, view_type="kanban")
        kanban_id = data["view"]["view_id"]
        steps.append({"kanban": "created", "view_id": kanban_id})
        time.sleep(wait)

    client.patch_view(kanban_id, {"property": {"group_field_id": group_fid}})
    steps.append({"kanban": "group_field_set", "field": stack_field})
    time.sleep(wait)

    views = client.list_views()
    fm = _find_view(views, form_name, "form")
    if fm:
        form_id = fm["view_id"]
        steps.append({"form": "exists", "view_id": form_id})
    else:
        data = client.create_view(view_name=form_name, view_type="form")
        form_id = data["view"]["view_id"]
        steps.append({"form": "created", "view_id": form_id})
        time.sleep(wait)

    meta = client.patch_form_meta(
        form_id,
        {
            "name": form_name,
            "description": form_description,
            "shared": True,
            "shared_limit": shared_limit,
            "submit_limit_once": False,
        },
    )
    form_info = meta.get("form") or {}
    steps.append(
        {
            "form": "meta_updated",
            "shared_url": form_info.get("shared_url"),
            "shared_limit": form_info.get("shared_limit"),
        }
    )

    return steps


def cmd_setup_issue_views(args):
    """Create Kanban + Form views for the ticket table; enable form sharing."""
    client = BitableClient(project_name=args.project)
    steps = ensure_issue_views(
        client,
        kanban_name=args.kanban_name,
        form_name=args.form_name,
        stack_field=args.stack_field,
        form_description=args.form_description,
        shared_limit=args.shared_limit,
        delay_s=args.delay_s,
    )
    _print({"ok": True, "steps": steps})


def cmd_set_default_folder(args):
    """Set the default Drive folder token for future init-project calls."""
    import tempfile
    import os

    cfg_path = lark_client._resolve_config_path()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    cfg["default_folder_token"] = args.token

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

    _print({"ok": True, "default_folder_token": args.token})


def cmd_set_operator(args):
    """Set the operator open_id for editability grants on future init-project calls."""
    import tempfile
    import os

    cfg_path = lark_client._resolve_config_path()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    cfg["operator_openid"] = args.open_id

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

    _print({"ok": True, "operator_openid": args.open_id})


def cmd_show_members(args):
    """List members of a project's bitable base (read-only discovery)."""
    cfg = load_config()
    token = get_tenant_access_token(cfg)
    projects = cfg.get("projects") or {}
    name = args.project
    entry = projects.get(name)
    if not entry or not entry.get("bitable_app_token"):
        raise SystemExit(f"Project '{name}' not in config or missing bitable_app_token.")
    app_token = entry["bitable_app_token"]

    try:
        data = _request(
            "GET",
            f"/open-apis/drive/v1/permissions/{app_token}/members",
            token=token,
            params={"type": "bitable"},
        )
        items = data.get("items") or []
        for item in items:
            _print({
                "member_id": item.get("member_id"),
                "member_type": item.get("member_type"),
                "perm": item.get("perm"),
            })
    except Exception as e:
        print(f"ERROR: failed to list members ({e})", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# init-project (idempotent bootstrap)
# ---------------------------------------------------------------------------

def _probe_tables(app_token: str, token: str) -> bool:
    """Return True if the base is reachable (GET tables succeeds)."""
    try:
        _request("GET", f"/open-apis/bitable/v1/apps/{app_token}/tables", token=token)
        return True
    except Exception:
        return False


def _try_grant_operator_access(app_token: str, cfg: dict, token: str) -> None:
    """Best-effort: grant the configured operator_openid full_access on the base.

    Editability mechanism: the operator's open_id is stored in config via
    ``set-operator``.  On ANY failure (missing config, API error), we print
    a one-line NOTE and return without raising.
    """
    oid = cfg.get("operator_openid")
    if not oid:
        return  # No operator configured, skip silently

    try:
        _request(
            "POST",
            f"/open-apis/drive/v1/permissions/{app_token}/members",
            token=token,
            params={"type": "bitable", "need_notification": "false"},
            body={
                "member_type": "openid",
                "member_id": oid,
                "perm": "full_access",
            },
        )
        print(f"granted  openid={oid}  perm=full_access")
    except Exception as e:
        print(f"NOTE: operator grant skipped ({e})", file=sys.stderr)


def cmd_init_project(args):
    """Idempotent one-command Lark bitable bootstrap.

    Idempotency contract:
      - entry + reachable  → reuse (skip create, re-seed schema, ensure marker)
      - no entry           → create (create_bitable, upsert config, write marker, seed)
      - entry + unreachable → refuse unless --force-recreate
    """
    import time as _time

    cfg = load_config()
    token = get_tenant_access_token(cfg)
    projects = cfg.get("projects") or {}
    name = args.project
    entry = projects.get(name)
    has_entry = entry is not None and entry.get("bitable_app_token")

    # Resolve folder: --folder arg -> config.default_folder_token -> none
    # (organization only — NOT the editability mechanism)
    folder = args.folder or cfg.get("default_folder_token")

    # Editability warning: keyed on operator_openid
    if not cfg.get("operator_openid"):
        print(
            "WARNING: base will be app-owned and NOT editable in the web UI. "
            "To fix: run `set-operator <open_id>` — find your open_id with "
            "`show-members --project <an-existing-editable-project>`.",
            file=sys.stderr,
        )

    if has_entry:
        # Entry exists — check reachability
        app_token = entry["bitable_app_token"]
        reachable = _probe_tables(app_token, token)
        if reachable:
            # Reuse path
            table_id = entry.get("table_id", "")
            url = entry.get("url", "")
            print(f"reused  app_token={app_token}  table={table_id}  url={url}")
            _ensure_marker(args.repo, name)
            from init_bitable import seed_schema
            seed_schema(project_name=name, rename_primary=True)
            # Best-effort openid member-grant (idempotent, non-fatal)
            _try_grant_operator_access(app_token, cfg, token)
            # Ensure kanban + shared form views (idempotent)
            client = BitableClient(project_name=name)
            steps = ensure_issue_views(
                client,
                stack_field="状态",
                form_name=f"{name}-提交新工单",
            )
            _print({"ok": True, "steps": steps})
            return
        else:
            # Unreachable
            if not args.force_recreate:
                print(
                    f"ERROR: project '{name}' exists in config but its base "
                    f"({app_token}) is unreachable. Use --force-recreate to "
                    f"replace it (the old base will NOT be deleted).",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Force-recreate: fall through to create path
            print(f"WARNING: replacing unreachable base {app_token} (--force-recreate)")

    # Create path
    result = create_bitable(name, folder_token=folder, token=token)
    app_token = result["app_token"]
    table_id = result.get("table_id", "")
    url = result.get("url", "")

    new_entry = {
        "bitable_app_token": app_token,
        "table_id": table_id,
        "url": url,
        "ticket_id_prefix": args.prefix,
    }
    upsert_project_config(name, new_entry)
    print(f"created  app_token={app_token}  table={table_id}  url={url}")

    _ensure_marker(args.repo, name)

    from init_bitable import seed_schema
    seed_schema(project_name=name, rename_primary=True)

    # Best-effort openid member-grant (idempotent, non-fatal)
    _try_grant_operator_access(app_token, cfg, token)

    # Ensure kanban + shared form views (idempotent)
    client = BitableClient(project_name=name)
    steps = ensure_issue_views(
        client,
        stack_field="状态",
        form_name=f"{name}-提交新工单",
    )
    _print({"ok": True, "steps": steps})


def _ensure_marker(repo_dir: str, project_name: str) -> Path:
    """Write ``.lark-project`` marker if missing or different. Return path."""
    marker = Path(repo_dir) / ".lark-project"
    if marker.exists():
        existing = marker.read_text(encoding="utf-8").strip().splitlines()
        if existing and existing[0].strip() == project_name:
            return marker  # already correct
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(project_name + "\n", encoding="utf-8")
    return marker


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lark-tickets")
    p.add_argument("--project", help="Project name (overrides .lark-project marker)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("fields", help="List all fields in the bitable")
    sp.set_defaults(func=cmd_fields)

    sp = sub.add_parser("list", help="List tickets (summary)")
    sp.add_argument("--status", help="Filter by status (e.g. 新建/可执行/进行中)")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="Show one ticket (full fields)")
    sp.add_argument("record_id")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("pull-new", help="Pull all 状态=新建 tickets for triage")
    sp.set_defaults(func=cmd_pull_new)

    sp = sub.add_parser("update", help="Update fields on a ticket")
    sp.add_argument("record_id")
    sp.add_argument("--field", action="append", help="NAME=VALUE (repeatable)")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("next-id", help="Generate next ticket id (T-YYYY-NNNN)")
    sp.set_defaults(func=cmd_next_id)

    sp = sub.add_parser("download", help="Download attachments from a ticket field")
    sp.add_argument("record_id")
    sp.add_argument("field_name")
    sp.add_argument("--to", required=True, help="Destination directory")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser(
        "setup-issue-views",
        help="Create Kanban (by status) + shared Form views for ticket workflow",
    )
    sp.add_argument("--kanban-name", default="工单看板", help="Name of the Kanban view")
    sp.add_argument("--form-name", default="提交新工单", help="Name of the Form view")
    sp.add_argument(
        "--stack-field",
        default="状态",
        help="Single-select field to group Kanban columns (default: 状态)",
    )
    sp.add_argument(
        "--form-description",
        default="请描述问题：现象、页面链接、期望与实际结果。可选上传截图。提交后运维/研发会分拣到看板列。",
        help="Subtitle text on the Feishu form",
    )
    sp.add_argument(
        "--shared-limit",
        default="tenant_editable",
        choices=["off", "tenant_editable", "anyone_editable"],
        help="Who may fill the form when shared",
    )
    sp.add_argument(
        "--delay-s",
        type=int,
        default=3,
        help="Seconds between write API calls (avoids Feishu write conflicts)",
    )
    sp.set_defaults(func=cmd_setup_issue_views)

    sp = sub.add_parser(
        "set-default-folder",
        help="Set the default Drive folder token for future init-project calls",
    )
    sp.add_argument("token", help="Drive folder token (from URL: .../drive/folder/<TOKEN>)")
    sp.set_defaults(func=cmd_set_default_folder)

    sp = sub.add_parser(
        "set-operator",
        help="Set the operator open_id for editability grants on future init-project calls",
    )
    sp.add_argument("open_id", help="Operator open_id (ou_...) from show-members or Lark admin")
    sp.set_defaults(func=cmd_set_operator)

    sp = sub.add_parser(
        "show-members",
        help="List members of a project's bitable base (discover your open_id)",
    )
    sp.add_argument("--project", required=True, help="Project name (config key)")
    sp.set_defaults(func=cmd_show_members)

    sp = sub.add_parser(
        "init-project",
        help="Idempotent one-command Lark bitable bootstrap (create + config + marker + schema)",
    )
    sp.add_argument("--project", required=True, help="Project name (config key)")
    sp.add_argument("--folder", default=None, help="Drive folder token for visibility")
    sp.add_argument("--prefix", default="T", help="Ticket id prefix (default: T)")
    sp.add_argument("--repo", default=".", help="Repo root for .lark-project marker (default: cwd)")
    sp.add_argument(
        "--force-recreate",
        action="store_true",
        help="Replace an unreachable base instead of refusing",
    )
    sp.set_defaults(func=cmd_init_project)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except LarkError as e:
        sys.stderr.write(f"Lark API error: {e}\nbody: {e.body[:500]}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
