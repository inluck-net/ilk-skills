"""Seed a new project's Bitable with the standard 24-field ticket schema.

Usage:
  python init_bitable.py --project <name> [--rename-primary]

Prereqs:
  - The project entry must already exist in ~/.cursor/lark-tickets/config.json
  - The Feishu app must have edit permission on the bitable.

Behavior:
  - Renames the auto-generated primary field to "标题" (only if --rename-primary
    is passed; safer default is to leave it alone if you've customized it).
  - Creates any missing fields from the schema list below.
  - Skips fields that already exist (matched by name).

Field type cheat sheet (Bitable v1):
   1=Text  2=Number  3=SingleSelect  4=MultiSelect  5=DateTime  7=Checkbox
   11=User  13=Phone  15=Url  17=Attachment  18=SingleLink
   19=Lookup  20=Formula  21=DuplexLink  22=Location  23=GroupChat
   1001=CreatedTime  1002=ModifiedTime  1003=CreatedUser  1004=ModifiedUser
   1005=AutoNumber
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lark_client import BitableClient, _request  # noqa: E402


# Schema mirrors the live "uccargo 工单池" bitable.
# Field-id-to-name mapping is irrelevant here; only names matter.
SCHEMA = [
    # ------------- client-filled (form view) ----------------------------------
    {"field_name": "原文描述",   "type": 1},
    {"field_name": "在哪个页面", "type": 15},
    {"field_name": "操作步骤",   "type": 1},
    {"field_name": "期望看到",   "type": 1},
    {"field_name": "实际看到",   "type": 1},
    {"field_name": "截图",       "type": 17},
    {"field_name": "录入端", "type": 3, "property": {"options": [
        {"name": "Portal", "color": 0},
        {"name": "Admin",  "color": 1},
        {"name": "通用",   "color": 2},
    ]}},
    {"field_name": "紧急度", "type": 3, "property": {"options": [
        {"name": "低",   "color": 2},
        {"name": "中",   "color": 7},
        {"name": "高",   "color": 1},
        {"name": "紧急", "color": 6},
    ]}},

    # ------------- AI-filled (triage) -----------------------------------------
    {"field_name": "ticket_id", "type": 1},
    {"field_name": "类型", "type": 3, "property": {"options": [
        {"name": "bug",      "color": 6},
        {"name": "新功能",   "color": 4},
        {"name": "体验优化", "color": 7},
        {"name": "咨询",     "color": 0},
        {"name": "重复",     "color": 5},
        {"name": "无效",     "color": 5},
    ]}},
    {"field_name": "涉及模块",      "type": 1},
    {"field_name": "AI 理解",       "type": 1},
    {"field_name": "缺失信息",      "type": 1},
    {"field_name": "AI 优先级建议", "type": 3, "property": {"options": [
        {"name": "P0", "color": 6},
        {"name": "P1", "color": 1},
        {"name": "P2", "color": 7},
        {"name": "P3", "color": 0},
    ]}},
    {"field_name": "关联 plan",   "type": 15},
    {"field_name": "关联 commit", "type": 1},

    # ------------- CI-filled --------------------------------------------------
    {"field_name": "E2E 结果", "type": 3, "property": {"options": [
        {"name": "未运行", "color": 0},
        {"name": "通过",   "color": 2},
        {"name": "失败",   "color": 6},
        {"name": "跳过",   "color": 5},
    ]}},
    {"field_name": "E2E 报告链接", "type": 15},

    # ------------- shared status ---------------------------------------------
    {"field_name": "状态", "type": 3, "property": {"options": [
        {"name": "新建",   "color": 0},
        {"name": "待澄清", "color": 7},
        {"name": "可执行", "color": 1},
        {"name": "计划中", "color": 4},
        {"name": "实施中", "color": 4},
        {"name": "待验证", "color": 5},
        {"name": "已发布", "color": 2},
        {"name": "关闭",   "color": 0},
        {"name": "重复",   "color": 5},
        {"name": "无效",   "color": 5},
    ]}},
    {"field_name": "处理人", "type": 11, "property": {"multiple": False}},

    # ------------- system-managed --------------------------------------------
    {"field_name": "录入人",   "type": 1003},
    {"field_name": "录入时间", "type": 1001},
    {"field_name": "最后更新", "type": 1002},
]


def rename_primary_to_title(client: BitableClient) -> None:
    """Rename the first (primary) field to 标题 if it's still the default."""
    fields = client.list_fields(refresh=True)
    primary = None
    for name, info in fields.items():
        if info.get("is_primary"):
            primary = info
            primary_name = name
            break
    if primary is None:
        print("WARN: no primary field detected; skipping rename")
        return
    if primary_name == "标题":
        print("primary field already named 标题; skipping rename")
        return
    field_id = primary["field_id"]
    path = f"/open-apis/bitable/v1/apps/{client.app_token}/tables/{client.table_id}/fields/{field_id}"
    _request("PUT", path, token=client.token, body={"field_name": "标题", "type": 1})
    print(f"renamed primary field '{primary_name}' -> '标题'")
    client.list_fields(refresh=True)


def create_missing(client: BitableClient) -> None:
    existing = set(client.list_fields(refresh=True).keys())
    created, skipped, failed = 0, 0, 0
    for spec in SCHEMA:
        name = spec["field_name"]
        if name in existing:
            skipped += 1
            print(f"skip   {name} (exists)")
            continue
        try:
            path = f"/open-apis/bitable/v1/apps/{client.app_token}/tables/{client.table_id}/fields"
            _request("POST", path, token=client.token, body=spec)
            created += 1
            print(f"create {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL   {name}: {e}")
    print(f"\nDone. created={created} skipped={skipped} failed={failed}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", help="Project name (overrides .lark-project marker)")
    p.add_argument("--rename-primary", action="store_true",
                   help="Rename the primary field to '标题' if needed")
    args = p.parse_args()

    client = BitableClient(project_name=args.project)
    print(f"Project: {client.project_name}")
    print(f"Bitable: {client.app_token}  table={client.table_id}\n")

    if args.rename_primary:
        rename_primary_to_title(client)
        print()

    create_missing(client)


if __name__ == "__main__":
    main()
