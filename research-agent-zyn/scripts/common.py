"""任务状态、路径边界、文件锁与原子写入；不依赖第三方包。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

SKILL = Path(__file__).resolve().parents[1]
CONTRACTS = json.loads((SKILL / "assets/contracts.json").read_text(encoding="utf-8"))
STATE = ".agents/research-agent-zyn"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError(f"无效标识: {value}")
    return value


def project_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"科研项目不存在: {root}；本 skill 不初始化仓库")
    return root


def inside(root: Path, value: str) -> Path:
    """拒绝越界和符号链接，避免通过别名绕过文档归属或读到未授权位置。"""
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise ValueError(f"必须使用项目内相对路径: {value}")
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"受控路径不能经过符号链接: {path}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"路径越界: {value}")
    return path


def sha(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _invalid_number(value: str):
    raise ValueError(f"JSON 含非有限数值: {value}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"JSON 浮点数超出有限范围: {value}")
    return result


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_invalid_number,
                       parse_float=_finite_float)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 必须为对象: {path}")
    return value


def atomic_text(path: Path, text: str) -> None:
    """同目录临时文件替换，避免读到半个 JSON 或半份文档。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".research-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


@contextmanager
def lock(path: Path):
    """有并发写入时立即返回冲突；崩溃遗留锁需核实持有进程后人工处理。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError(f"写入锁已被占用，请重读状态后重试: {path}") from error
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def task_dir(project: Path, task_id: str) -> Path:
    return inside(project, f"{STATE}/tasks/{identifier(task_id)}")


def task_state(project: Path, task_id: str, *, active: bool = False) -> dict:
    task = load(task_dir(project, task_id) / "task.json")
    if task["project"] != str(project):
        raise ValueError("任务绑定的科研项目与当前路径不一致")
    if active and task["status"] != "ACTIVE":
        raise ValueError(f"任务当前为 {task['status']}，不能启动新工作")
    return task


@contextmanager
def task_lock(project: Path, task_id: str):
    directory = task_dir(project, task_id)
    if not (directory / "task.json").is_file():
        raise ValueError("不存在显式启动的任务")
    with lock(directory / "state.lock"):
        yield


def actor(task: dict, role: str, agent_id: str) -> None:
    if role not in CONTRACTS["roles"]:
        raise ValueError(f"未知角色: {role}")
    if not any(item["role"] == role and item["agent_id"] == agent_id for item in task["dispatches"]):
        raise ValueError("缺少该角色的真实子智能体调度记录")


def event(project: Path, task_id: str, kind: str, **details) -> None:
    path = task_dir(project, task_id) / "events.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": now(), "event": kind, **details}, ensure_ascii=False) + "\n")
