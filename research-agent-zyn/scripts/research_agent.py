#!/usr/bin/env python3
"""research-agent-zyn 的公共接口；只操作显式任务，不创建或安装 skill。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

from common import CONTRACTS, SKILL, inside, load, project_root, sha, task_state
from documents import archive, patch
from workflow import close, dispatch, freeze, pending, record_review, resume, run_experiment, start


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="action", required=True)
    role = commands.add_parser("role", help="读取角色指令，供实际 spawn 调用使用；此命令不会启动子智能体")
    role.add_argument("name", choices=CONTRACTS["roles"])
    role.add_argument("--instructions-only", action="store_true")
    version = commands.add_parser("hash", help="读取文档当前 SHA-256，不存在时返回 MISSING")
    version.add_argument("--project", required=True)
    version.add_argument("--path", required=True)
    for name in ("start", "dispatch", "freeze", "review", "run", "patch", "archive", "pending", "status", "resume", "close"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        command.add_argument("--task", required=True)
        if name in {"start", "resume"}:
            command.add_argument("--explicit", action="store_true", required=True)
        if name == "start":
            command.add_argument("--request", required=True)
        if name == "resume":
            command.add_argument("--reason", required=True)
        if name in {"dispatch", "patch"}:
            command.add_argument("--role", choices=CONTRACTS["roles"], required=True)
        if name in {"dispatch", "review", "run", "patch", "archive"}:
            command.add_argument("--agent-id", required=True)
        if name == "freeze":
            command.add_argument("--spec", required=True, help="项目内 JSON spec 相对路径")
        if name in {"review", "run"}:
            command.add_argument("--freeze", required=True)
        if name == "review":
            command.add_argument("--verdict", type=Path, required=True)
        if name in {"run", "archive"}:
            command.add_argument("--run", required=True)
        if name in {"patch", "archive"}:
            command.add_argument("--document", required=True)
            command.add_argument("--body", type=Path, required=True, help="待写入正文/分析的 UTF-8 文件")
            command.add_argument("--expected", required=True, help="先用 hash 读取的完整文档版本")
        if name == "patch":
            command.add_argument("--section", required=True)
            command.add_argument("--protocol-change-approved", action="store_true")
        if name == "close":
            command.add_argument("--cancelled", action="store_true")
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        if args.action == "role":
            record = tomllib.loads((SKILL / f"assets/agents/{args.name}.toml").read_text(encoding="utf-8"))
            if args.instructions_only:
                print(record["developer_instructions"])
                return 0
            result = record
        else:
            project = project_root(args.project)
            action = args.action
            if action == "hash":
                result = {"sha256": sha(inside(project, args.path))}
            elif action == "start":
                result = start(project, args.task, args.request, args.explicit)
            elif action == "dispatch":
                result = dispatch(project, args.task, args.role, args.agent_id)
            elif action == "freeze":
                result = freeze(project, args.task, args.spec)
            elif action == "review":
                result = record_review(project, args.task, args.freeze, args.agent_id, load(args.verdict))
            elif action == "run":
                result = run_experiment(project, args.task, args.freeze, args.run, args.agent_id)
            elif action == "patch":
                result = patch(project, args.task, args.role, args.agent_id, args.document, args.section,
                               args.body.read_text(encoding="utf-8"), args.expected, args.protocol_change_approved)
            elif action == "archive":
                result = archive(project, args.task, args.run, args.agent_id, args.document,
                                 args.body.read_text(encoding="utf-8"), args.expected)
            elif action == "pending":
                result = {"pending": pending(project, args.task)}
            elif action == "status":
                result = task_state(project, args.task)
            elif action == "resume":
                result = resume(project, args.task, args.reason, args.explicit)
            else:
                result = close(project, args.task, args.cancelled)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if args.action == "run" and result["status"] != "COMPLETED":
            return 1
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"research-agent-zyn: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
