"""显式任务、调度记录、输入快照、审查与受控运行。记录不替代实际科学审查。"""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import uuid

from common import (CONTRACTS, actor, event, identifier, inside, load,
                    now, object_hash, save, sha, task_dir, task_lock, task_state)

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}


def git_metadata(project: Path) -> dict:
    """可用时记录 Git 版本与当前项目的未提交状态；无仓库时明确为 null。"""
    git = shutil.which("git")
    def query(*arguments: str) -> str | None:
        if git is None:
            return None
        result = subprocess.run([git, "-C", str(project), *arguments], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return {"commit": query("rev-parse", "HEAD"), "status": query("status", "--porcelain", "--", ".")}


def start(project: Path, task_id: str, request: str, explicit: bool) -> dict:
    if not explicit or not request.strip():
        raise ValueError("只有本次用户明确调用后，才能以 --explicit 创建非空任务")
    directory = task_dir(project, task_id)
    directory.mkdir(parents=True, exist_ok=False)
    task = {"task_id": task_id, "project": str(project), "request": request,
            "status": "ACTIVE", "generation": 0, "created_at": now(), "dispatches": [], "runs": []}
    save(directory / "task.json", task)
    event(project, task_id, "started")
    return task


def dispatch(project: Path, task_id: str, role: str, agent_id: str) -> dict:
    with task_lock(project, task_id):
        task = task_state(project, task_id, active=True)
        if role not in CONTRACTS["roles"] or not agent_id.strip():
            raise ValueError("需要有效角色和实际 spawn 返回的 agent_id")
        matches = [item for item in task["dispatches"] if item["agent_id"] == agent_id]
        if matches:
            if matches[0]["role"] != role:
                raise ValueError("一个子智能体调用不能兼任多个角色；独立审查必须新调用")
            return matches[0]
        record = {"role": role, "agent_id": agent_id, "time": now()}
        task["dispatches"].append(record)
        save(task_dir(project, task_id) / "task.json", task)
        event(project, task_id, "dispatched", **record)
        return record


def _spec(project: Path, value: str) -> tuple[Path, dict]:
    path = inside(project, value)
    spec = load(path)
    if not re.fullmatch(r"EXP-\d{3,}", spec.get("experiment_id", "")):
        raise ValueError("spec 需要 EXP-xxx experiment_id")
    if not isinstance(spec.get("purpose"), str) or not spec["purpose"].strip():
        raise ValueError("spec 需要明确 purpose")
    if not isinstance(spec.get("inputs"), list) or not spec["inputs"] or not all(isinstance(v, str) for v in spec["inputs"]):
        raise ValueError("spec.inputs 必须列出相关代码、配置、协议和数据/划分清单")
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
        raise ValueError("command 必须为非空参数数组，不能是 shell 字符串")
    artifacts = spec.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts or not all(isinstance(k, str) and isinstance(v, str) for k, v in artifacts.items()):
        raise ValueError("artifacts 必须明确列出需核验的产物")
    for relative in artifacts.values():
        artifact = inside(project, relative)
        if artifact == project or relative == "run.json" or relative.startswith("logs/"):
            raise ValueError("产物不能使用保留的 run.json 或 logs/ 路径")
    return path, spec


def input_manifest(project: Path, inputs: list[str]) -> dict:
    manifest = {}
    for relative in sorted(set(inputs)):
        if relative.split("/")[0] in {"runs", ".agents", ".git", ".venv"}:
            raise ValueError("输入不能是运行输出、任务状态、Git 或虚拟环境目录")
        path = inside(project, relative)
        if path == project:
            raise ValueError("不能把整个项目根目录作为输入；请显式列出相关代码、配置和协议")
        if not path.exists():
            raise ValueError(f"审查输入缺失: {relative}")
        paths = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
        for child in paths:
            name = child.relative_to(project).as_posix()
            if set(child.relative_to(path).parts if child != path else ()).intersection(IGNORED):
                continue
            inside(project, name)
            if child.is_dir():
                manifest[name.rstrip("/") + "/"] = None
            elif child.is_file():
                manifest[name] = sha(child)
    return manifest


def freeze(project: Path, task_id: str, spec_path: str) -> dict:
    with task_lock(project, task_id):
        task = task_state(project, task_id, active=True)
        path, spec = _spec(project, spec_path)
        inputs = list(spec["inputs"]) + [spec_path]
        inputs += [name for name in ("pyproject.toml", "uv.lock", ".python-version") if (project / name).is_file()]
        manifest = input_manifest(project, inputs)
        executable = shutil.which(spec["command"][0])
        if not executable:
            raise ValueError(f"执行命令不存在: {spec['command'][0]}")
        executable = str(Path(executable).resolve())
        freeze_id = "FRZ-" + uuid.uuid4().hex[:16]
        directory = task_dir(project, task_id) / "freezes" / freeze_id
        directory.mkdir(parents=True)
        for name, checksum in manifest.items():
            destination = directory / "inputs" / name
            if checksum is None:
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(inside(project, name), destination)
                if sha(destination) != checksum:
                    raise ValueError("生成快照期间输入发生变化，请重新准备")
        stamp = {"spec": spec, "inputs": manifest, "executable_sha256": sha(Path(executable))}
        result = {"freeze_id": freeze_id, "task_id": task_id, "generation": task["generation"],
                  "spec_path": spec_path, "spec": spec, "input_paths": inputs, "inputs": manifest,
                  "executable": executable, "executable_sha256": stamp["executable_sha256"],
                  "fingerprint": object_hash(stamp), "git": git_metadata(project), "created_at": now()}
        save(directory / "freeze.json", result)
        event(project, task_id, "frozen", freeze_id=freeze_id, fingerprint=result["fingerprint"])
        return result


def frozen(project: Path, task_id: str, freeze_id: str) -> dict:
    record = load(task_dir(project, task_id) / "freezes" / identifier(freeze_id) / "freeze.json")
    if record["task_id"] != task_id:
        raise ValueError("快照任务不匹配")
    return record


def check_fingerprint(project: Path, record: dict) -> None:
    manifest = input_manifest(project, record["input_paths"])
    current = object_hash({"spec": record["spec"], "inputs": manifest,
                           "executable_sha256": sha(Path(record["executable"]))})
    if current != record["fingerprint"]:
        raise ValueError("代码/配置/协议/输入清单或命令已变化，审查失效，请重新 freeze 和审查")
    directory = task_dir(project, record["task_id"]) / "freezes" / record["freeze_id"] / "inputs"
    for relative, expected in record["inputs"].items():
        path = directory / relative
        if (expected is None and not path.is_dir()) or (expected is not None and sha(path) != expected):
            raise ValueError("审查输入快照已被修改")


def record_review(project: Path, task_id: str, freeze_id: str, agent_id: str, verdict: dict) -> dict:
    with task_lock(project, task_id):
        task = task_state(project, task_id, active=True)
        actor(task, "research_review", agent_id)
        record = frozen(project, task_id, freeze_id)
        check_fingerprint(project, record)
        status = verdict.get("status")
        issues = verdict.get("issues")
        if status not in {"PASS", "BLOCKED", "NEEDS_INFO"} or not isinstance(issues, list):
            raise ValueError("审查需要 PASS/BLOCKED/NEEDS_INFO 和 issues 数组")
        if not isinstance(verdict.get("summary"), str) or not verdict["summary"].strip():
            raise ValueError("审查结论必须有 summary")
        if (not isinstance(verdict.get("checks"), list) or not verdict["checks"]
                or not all(isinstance(item, str) and item.strip() for item in verdict["checks"])):
            raise ValueError("审查必须记录实际完成的 checks")
        for item in issues:
            if (not isinstance(item, dict) or item.get("severity") not in {"blocking", "info"}
                    or not all(isinstance(item.get(k), str) and item[k].strip()
                               for k in ("evidence", "explanation", "correction"))):
                raise ValueError("审查问题必须包含级别、证据、解释与修正建议")
        if status == "PASS" and any(item["severity"] == "blocking" for item in issues):
            raise ValueError("含阻断问题不能 PASS")
        if status != "PASS" and not issues:
            raise ValueError("阻断或信息不足必须记录具体问题")
        if verdict.get("fingerprint") != record["fingerprint"] or record["generation"] != task["generation"]:
            raise ValueError("审查对应的快照或任务轮次不匹配")
        path = task_dir(project, task_id) / "freezes" / freeze_id / "review.json"
        if path.exists():
            raise ValueError("历史审查不可覆盖，请建立新 freeze 后复核")
        review = {**verdict, "reviewer_agent_id": agent_id, "generation": task["generation"], "time": now()}
        save(path, review)
        if status != "PASS":
            task["status"] = "BLOCKED"
            save(task_dir(project, task_id) / "task.json", task)
        event(project, task_id, "reviewed", freeze_id=freeze_id, status=status, agent_id=agent_id)
        return review


def resume(project: Path, task_id: str, reason: str, explicit: bool) -> dict:
    with task_lock(project, task_id):
        task = task_state(project, task_id)
        if task["status"] != "BLOCKED" or not explicit or not reason.strip():
            raise ValueError("只有用户明确要求恢复阻断任务并说明原因后才能 resume")
        task.update(status="ACTIVE", generation=task["generation"] + 1)
        save(task_dir(project, task_id) / "task.json", task)
        event(project, task_id, "resumed", reason=reason)
        return task


def run_experiment(project: Path, task_id: str, freeze_id: str, run_id: str, agent_id: str) -> dict:
    identifier(run_id)
    with task_lock(project, task_id):
        task = task_state(project, task_id, active=True)
        actor(task, "implementation_execution", agent_id)
        record = frozen(project, task_id, freeze_id)
        review_path = task_dir(project, task_id) / "freezes" / freeze_id / "review.json"
        if not review_path.is_file():
            raise ValueError("未审查，禁止启动")
        review = load(review_path)
        actor(task, "research_review", review["reviewer_agent_id"])
        if (review["status"] != "PASS" or review["fingerprint"] != record["fingerprint"]
                or record["generation"] != task["generation"] or review["generation"] != task["generation"]):
            raise ValueError("审查未通过或已失效，禁止启动")
        check_fingerprint(project, record)
        run = inside(project, f"runs/{run_id}")
        run.mkdir(parents=True, exist_ok=False)
        (run / "logs").mkdir()
        argv = [record["executable"], *record["spec"]["command"][1:]]
        argv = [arg.replace("{run_dir}", str(run)) for arg in argv]
        result = {"run_id": run_id, "task_id": task_id, "experiment_id": record["spec"]["experiment_id"],
                  "freeze_id": freeze_id, "fingerprint": record["fingerprint"], "review_sha256": sha(review_path),
                  "command": argv, "started_at": now(), "status": "RUNNING", "exit_code": None,
                  "executor_agent_id": agent_id,
                  "controller_python": sys.version, "controller_platform": platform.platform(),
                  "git": git_metadata(project), "archive": None}
        save(run / "run.json", result)
        task["runs"].append(run_id)
        save(task_dir(project, task_id) / "task.json", task)
        event(project, task_id, "run_started", run_id=run_id)
    interrupted = False
    error = None
    try:
        with (run / "logs/console.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(argv, cwd=project, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, RESEARCH_RUN_DIR=str(run)), start_new_session=True)
            result["pid"] = process.pid
            save(run / "run.json", result)
            try:
                result["exit_code"] = process.wait()
            except KeyboardInterrupt:
                interrupted = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                result["exit_code"] = process.returncode
    except (OSError, ValueError) as exception:
        error = str(exception)
    artifacts = {}
    missing = []
    invalid = []
    for label, relative in record["spec"]["artifacts"].items():
        try:
            path = inside(run, relative)
        except ValueError as exception:
            invalid.append(str(exception))
            continue
        if not path.is_file():
            missing.append(relative)
            continue
        artifacts[relative] = sha(path)
        if label == "metrics":
            try:
                load(path)
            except (OSError, ValueError) as exception:
                invalid.append(str(exception))
    try:
        check_fingerprint(project, record)
    except (OSError, ValueError) as exception:
        invalid.append(str(exception))
    status = ("INTERRUPTED" if interrupted else "FAILED" if error or result["exit_code"] != 0
              else "INCOMPLETE" if missing or invalid else "COMPLETED")
    result.update(status=status, finished_at=now(), artifact_sha256=artifacts,
                  missing_artifacts=missing, validation_errors=invalid, execution_error=error)
    with task_lock(project, task_id):
        save(run / "run.json", result)
        event(project, task_id, "run_finished", run_id=run_id, status=status)
    return result


def pending(project: Path, task_id: str) -> list[dict]:
    task = task_state(project, task_id)
    result = []
    for run_id in task["runs"]:
        record = load(inside(project, f"runs/{run_id}/run.json"))
        if record["status"] == "RUNNING" or record["archive"] is None:
            result.append({"run_id": run_id, "status": record["status"], "needs_archive": record["archive"] is None})
    return result


def close(project: Path, task_id: str, cancelled: bool = False) -> dict:
    with task_lock(project, task_id):
        task = task_state(project, task_id)
        if task["status"] not in {"ACTIVE", "BLOCKED"}:
            raise ValueError("任务已经结束")
        if pending(project, task_id):
            raise ValueError("存在运行中或尚未归档的 Run，请先核实状态并归档")
        # 归档标记不能代替实际文档；结束任务前再次核对章节与原始证据。
        from documents import section_range
        for run_id in task["runs"]:
            run = load(inside(project, f"runs/{run_id}/run.json"))
            archived = run["archive"]
            document = inside(project, archived["document"])
            text = document.read_text(encoding="utf-8")
            selected = section_range(text, archived["section"])
            if selected is None or hashlib.sha256(text[selected[0]:selected[1]].strip().encode()).hexdigest() != archived["section_sha256"]:
                raise ValueError("归档章节已删除或变化，请核对后重新归档")
            for name, checksum in run["artifact_sha256"].items():
                if sha(inside(project, f"runs/{run_id}/{name}")) != checksum:
                    raise ValueError("归档后原始产物发生变化，请核查")
        task.update(status="CANCELLED" if cancelled else "CLOSED", closed_at=now())
        save(task_dir(project, task_id) / "task.json", task)
        event(project, task_id, "closed", status=task["status"])
        return task
