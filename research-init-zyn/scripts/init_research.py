#!/usr/bin/env python3
"""初始化近乎空白的科研仓库：依赖检查、指令运行、文件复制。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from document_import import import_documents

SKILL_ROOT = Path(__file__).resolve().parents[1]


# 第一部分：依赖检查。只查找已有工具和解释器，不下载或修改全局环境。
@dataclass(frozen=True)
class Runtime:
    uv: str
    git: str
    python: str
    python_version: tuple[int, int]


def check_dependencies(python: str) -> Runtime:
    """检查 uv/Git，并确认用户选定的本机 Python 至少为 3.11。"""
    tools = {}
    for command in ("uv", "git"):
        executable = shutil.which(command)
        if executable is None:
            raise ValueError(f"缺少 {command}，请先安装后重试")
        tools[command] = executable
    interpreter = run([tools["uv"], "python", "find", python, "--offline",
                       "--no-python-downloads", "--no-cache"])
    major, minor = json.loads(run([
        interpreter, "-c", "import json,sys; print(json.dumps(list(sys.version_info[:2])))",
    ]))
    if (major, minor) < (3, 11):
        raise ValueError("本模板要求 Python 3.11 或更高版本")
    return Runtime(tools["uv"], tools["git"], interpreter, (major, minor))


# 第二部分：指令运行。外部命令统一使用参数列表，不拼接 shell 指令。
def run(args: list[str]) -> str:
    """运行失败时保留退出码和 stderr，由 main 统一报告。"""
    result = subprocess.run(args, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def initialize_repository(staging: Path, name: str, runtime: Runtime) -> None:
    """仅初始化 uv 非打包应用与独立 Git 仓库，不生成算法或实验。"""
    run([runtime.uv, "init", str(staging), "--bare", "--no-workspace", "--vcs", "git",
         "--author-from", "none", "--no-pin-python", "--name", name,
         "--python", runtime.python, "--offline", "--no-python-downloads", "--no-cache"])
    if not (staging / ".git").is_dir():
        run([runtime.git, "init", str(staging)])


def lock_dependencies(staging: Path, runtime: Runtime) -> None:
    """最终项目配置写入后再生成 uv.lock，避免锁文件与配置不一致。"""
    run([runtime.uv, "lock", "--project", str(staging), "--python", runtime.python,
         "--offline", "--no-python-downloads", "--no-cache"])


# 第三部分：文件复制。只有一套 assets 模板，不包含演示模式或占位数据。
def copy_assets(source: Path, target: Path, values: dict[str, str]) -> None:
    """整体复制模板树（包含空目录），再渲染 Markdown 并设置脚本权限。"""
    # 项目结构直接来自 assets，新增目录或静态文件无需再修改 Python 清单。
    # 目标是 uv 刚创建的临时仓库，允许合并；最终用户目标仍由 publish_project 拒绝覆盖。
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".gitkeep"))
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == ".gitkeep" or path.suffix == ".pyc":
            continue
        destination = target / path.relative_to(source)
        if path.suffix == ".md":
            def replace(match: re.Match[str]) -> str:
                key = match.group(1)
                if key not in values:
                    raise ValueError(f"模板变量未定义: {key} ({path})")
                return values[key]
            text = re.sub(r"\{\{([A-Z_]+)\}\}", replace, path.read_text(encoding="utf-8"))
            destination.write_text(text, encoding="utf-8")
        if destination.suffix == ".sh":
            destination.chmod(0o755)


def copy_project_files(staging: Path, *, title: str, question: str, objective: str,
                       requirements: str, name: str, runtime: Runtime) -> None:
    """生成 uv 项目配置，再复制完整 assets；不额外创建算法或实验目录。"""
    version = ".".join(map(str, runtime.python_version))
    # JSON 字符串转义适用于这里的 TOML 基本字符串，正确处理引号和换行。
    pyproject = (
        "[project]\n" f"name = {json.dumps(name)}\n" 'version = "0.1.0"\n'
        f"description = {json.dumps(title, ensure_ascii=False)}\n"
        'readme = "README.md"\nrequires-python = ">=3.11"\ndependencies = []\n'
        "\n[tool.uv]\npackage = false\n"
    )
    (staging / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (staging / ".python-version").write_text(version + "\n", encoding="utf-8")
    copy_assets(SKILL_ROOT / "assets", staging, {
        "PROJECT_TITLE": title,
        "RESEARCH_QUESTION": question,
        "RESEARCH_OBJECTIVE": objective,
        "PROJECT_STATUS": "项目初始化，尚未开展实验",
        "PYTHON_VERSION": version,
        "SPECIAL_REQUIREMENTS": requirements,
    })


def publish_project(staging: Path, target: Path) -> None:
    """全部步骤成功后发布；copytree 默认拒绝任何已存在的目标。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, target)


def initialize(target: Path, *, title: str, question: str, objective: str,
               requirements: str, name: str, python: str,
               import_plan: Path | None = None) -> None:
    """检查 → 初始化 → 组装/导入文件 → 锁定依赖 → 发布。"""
    # 不 resolve 最后一级路径，避免把悬空符号链接误认为可用的新目录。
    target = Path(os.path.abspath(target.expanduser()))
    if target.exists() or target.is_symlink():
        raise ValueError(f"目标已存在，拒绝覆盖（包括空目录和符号链接）: {target}")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("--name 必须由小写字母、数字及单个连字符组成")
    runtime = check_dependencies(python)
    # 命令、模板或导入失败时只清理临时目录，不发布半成品或触碰用户原文档。
    with tempfile.TemporaryDirectory(prefix="research-init-zyn-") as temporary:
        staging = Path(temporary) / "repository"
        initialize_repository(staging, name, runtime)
        copy_project_files(staging, title=title, question=question, objective=objective,
                           requirements=requirements, name=name, runtime=runtime)
        imports = import_documents(import_plan, staging) if import_plan else 0
        lock_dependencies(staging, runtime)
        publish_project(staging, target)
    print(json.dumps({"path": str(target), "name": name, "imported_documents": imports,
                      "python": ".".join(map(str, runtime.python_version))}, ensure_ascii=False))


def prompt_preferences(python: str | None, requirements: str | None) -> tuple[str, str]:
    """每次 CLI 调用都询问两项；预传参数仅作为本次问题的建议值。"""
    default_python = python or sys.executable
    default_requirements = requirements or "无特殊要求"
    try:
        selected_python = input(f"选择 Python 版本或路径（回车使用 {default_python}）：").strip()
        selected_requirements = input(f"有没有特殊要求（回车使用：{default_requirements}）：").strip()
    except EOFError as error:
        raise ValueError("必须回答 Python 版本和特殊要求两项问题；无交互输入时请显式提供两行 stdin 答案") from error
    return selected_python or default_python, selected_requirements or default_requirements


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="新仓库目标路径，不能已存在")
    parser.add_argument("--name", help="Python 项目名，默认由目录名规范化")
    parser.add_argument("--title", help="科研项目名称")
    parser.add_argument("--question", default="待确定：请填写核心研究问题")
    parser.add_argument("--objective", default="待确定：请填写研究目标与成功标准")
    parser.add_argument("--python", help="Python 版本问题的建议值，仍会每次询问")
    parser.add_argument("--requirements", help="特殊要求问题的建议值，仍会每次询问")
    parser.add_argument("--import-plan", type=Path, help="已有研究文档的 JSON 归类与合并计划")
    args = parser.parse_args()
    default_name = re.sub(r"[^a-z0-9]+", "-", args.target.name.lower()).strip("-") or "research-project"
    try:
        python, requirements = prompt_preferences(args.python, args.requirements)
        initialize(args.target, title=args.title or args.target.name,
                   question=args.question, objective=args.objective, requirements=requirements,
                   name=args.name or default_name, python=python, import_plan=args.import_plan)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"初始化失败: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
