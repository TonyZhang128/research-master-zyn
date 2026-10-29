"""公共文档操作：章节归属、乐观版本检查、串行写入和有证据的 Run 归档。"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from common import (CONTRACTS, STATE, actor, atomic_text, event, identifier, inside,
                    load, lock, now, save, sha, task_dir, task_lock, task_state)


def headings(text: str) -> list[tuple[int, int, int, str]]:
    """识别围栏代码块以外的 Markdown 标题，避免把示例代码当成章节。"""
    result = []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        elif fence is None:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*\n?$", line)
            if match:
                result.append((offset, offset + len(line), len(match.group(1)), match.group(2)))
        offset += len(line)
    return result


def section_range(text: str, section: str) -> tuple[int, int] | None:
    all_headings = headings(text)
    matches = [(i, item) for i, item in enumerate(all_headings) if item[3] == section]
    if len(matches) > 1:
        raise ValueError("存在同名章节，请先明确唯一目标，禁止猜测覆盖")
    if not matches:
        return None
    index, item = matches[0]
    ending = next((h[0] for h in all_headings[index + 1:] if h[2] <= item[2]), len(text))
    return item[1], ending


def permitted(project: Path, role: str, relative: str, section: str) -> Path:
    path = inside(project, relative)
    permissions = CONTRACTS["documents"].get(relative)
    method = re.fullmatch(r"docs/03_Methods_Experiments/(M-\d{3,}_[\w-]+)/(Method|EXP-\d{3,})\.md", relative)
    if method:
        folder, name = method.groups()
        permissions = CONTRACTS["method_sections"] if name == "Method" else CONTRACTS["experiment_sections"]
        root = inside(project, "docs/03_Methods_Experiments")
        method_id = folder.split("_", 1)[0]
        if any(p.name != folder for p in root.glob(method_id + "_*")):
            raise ValueError("相同 M ID 已对应另一个方法目录")
        if name != "Method" and any(p.parent.name != folder for p in root.glob(f"*/{name}.md")):
            raise ValueError("EXP ID 必须在项目内全局唯一")
    allowed = permissions.get(role, []) if permissions else []
    if "*" not in allowed and section not in allowed and not (
            role == "result_analysis" and "Run " in allowed and section.startswith("Run ")):
        raise ValueError(f"角色不能修改该文档/章节: {role} / {relative} / {section}")
    return path


def merged(text: str, section: str, body: str, title: str) -> str:
    if not section.strip() or "\n" in section or not body.strip():
        raise ValueError("需要非空的单行章节名和正文")
    if any(level <= 2 for _, _, level, _ in headings(body)):
        raise ValueError("正文不能插入一级/二级标题绕过章节归属；子标题请用 ###")
    if not text:
        text = f"# {title}\n"
    selected = section_range(text, section)
    if selected is None:
        return text.rstrip() + f"\n\n## {section}\n\n{body.strip()}\n"
    start, end = selected
    suffix = "\n\n" + text[end:].lstrip() if text[end:].strip() else "\n"
    return text[:start].rstrip() + "\n\n" + body.strip() + suffix


def _write(project: Path, relative: str, path: Path, section: str, body: str, expected: str) -> dict:
    name = hashlib.sha256(relative.encode()).hexdigest()
    with lock(inside(project, f"{STATE}/locks/{name}.lock")):
        actual = sha(path)
        if actual != expected:
            raise ValueError(f"文档版本冲突，请重读并合并: expected={expected}, actual={actual}")
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = merged(text, section, body, path.stem)
        if updated != text:
            atomic_text(path, updated)
        return {"document": relative, "section": section, "sha256": sha(path), "changed": updated != text}


def patch(project: Path, task_id: str, role: str, agent_id: str, relative: str, section: str,
          body: str, expected: str, protocol_approved: bool = False) -> dict:
    with task_lock(project, task_id):
        task = task_state(project, task_id)
        actor(task, role, agent_id)
        if task["status"] not in {"ACTIVE", "BLOCKED"}:
            raise ValueError("任务已结束，不能继续修改文档")
        if task["status"] == "BLOCKED" and role not in {"research_review", "result_analysis"}:
            raise ValueError("任务已阻断，先报告并等待用户决定，不继续设计或实现")
        path = permitted(project, role, relative, section)
        if relative == "docs/01_Benchmark_Protocol.md" and not protocol_approved:
            raise ValueError("Benchmark 变更需要本次用户授权；确认后使用 --protocol-change-approved")
        result = _write(project, relative, path, section, body, expected)
        event(project, task_id, "document_updated", role=role, agent_id=agent_id, **result)
        return result


def archive(project: Path, task_id: str, run_id: str, agent_id: str, relative: str,
            analysis: str, expected: str) -> dict:
    """将机器事实与分析共同写入 EXP；同一正文可幂等归档，原始指标不改动。"""
    identifier(run_id)
    with task_lock(project, task_id):
        task = task_state(project, task_id)
        actor(task, "result_analysis", agent_id)
        if task["status"] not in {"ACTIVE", "BLOCKED"}:
            raise ValueError("任务已结束，不能新增归档")
        if run_id not in task["runs"]:
            raise ValueError("Run 不属于当前任务")
        run_path = inside(project, f"runs/{run_id}/run.json")
        run = load(run_path)
        if run["status"] == "RUNNING":
            raise ValueError("运行结束尚未确认，不能猜测归档为完成")
        if Path(relative).name != run["experiment_id"] + ".md":
            raise ValueError("归档 EXP 与运行记录不一致")
        for name, expected_artifact in run["artifact_sha256"].items():
            artifact = inside(run_path.parent, name)
            if not artifact.is_file() or sha(artifact) != expected_artifact:
                raise ValueError(f"运行原始产物已变化，不能据此生成报告: {name}")
        section = f"Run {run_id}"
        path = permitted(project, "result_analysis", relative, section)
        # 固定事实块由程序生成；LLM 的解释单独标明，不能替换退出状态与缺失证据。
        evidence = ", ".join(f"`runs/{run_id}/{name}` (SHA-256 {checksum})"
                             for name, checksum in run["artifact_sha256"].items()) or "无完整产物"
        body = (f"机器记录：`runs/{run_id}/run.json`\n\n"
                f"执行状态：**{run['status']}**；退出码：`{run['exit_code']}`。\n\n"
                f"证据：{evidence}\n\n"
                f"缺失产物：{run['missing_artifacts']}；核验问题：{run['validation_errors']}。\n\n"
                f"### 分析与不确定性\n\n{analysis.strip()}")
        if not analysis.strip():
            raise ValueError("归档需要实际分析或明确的证据不足说明")
        result = _write(project, relative, path, section, body, expected)
        if (not result["changed"] and run["archive"] is not None
                and run["archive"]["document"] == relative
                and run["archive"]["analysis_sha256"] == hashlib.sha256(analysis.encode()).hexdigest()):
            return result
        run["archive"] = {"document": relative, "section": section, "agent_id": agent_id,
                          "analysis_sha256": hashlib.sha256(analysis.encode()).hexdigest(),
                          "section_sha256": hashlib.sha256(body.strip().encode()).hexdigest(), "time": now()}
        save(run_path, run)
        event(project, task_id, "archived", run_id=run_id, **result)
        return result
