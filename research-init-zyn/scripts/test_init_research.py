"""测试空科研项目初始化；可显式在 Downloads 创建并保留测试仓库。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile

SCRIPT = Path(__file__).with_name("init_research.py")
DOWNLOADS = Path("/Users/zinnia/Downloads")


def invoke(target: Path, *args: str, script: Path = SCRIPT,
           env: dict | None = None, answers: str = "\n\n") -> subprocess.CompletedProcess:
    """测试显式提供本轮两项答案，不依赖历史状态或交互终端。"""
    return subprocess.run([sys.executable, str(script), str(target), *args],
                          input=answers, text=True, capture_output=True, env=env)


def write_plan(root: Path, source: Path, sections: list[dict], **extra) -> Path:
    plan = root / "import-plan.json"
    plan.write_text(json.dumps({"documents": [{"source": str(source), "sections": sections, **extra}]},
                               ensure_ascii=False), encoding="utf-8")
    return plan


class InitializationTests(unittest.TestCase):
    def test_only_empty_project_structure_and_rendered_templates(self):
        with tempfile.TemporaryDirectory(prefix="research-structure-") as temporary:
            root = Path(temporary)
            target = root / "中文 project"
            title = '研究 "引号" 与换行\n标题'
            result = invoke(target, "--title", title, "--question", "研究问题", "--objective", "研究目标",
                            "--requirements", "只建立结构，不安装训练框架")
            self.assertEqual(result.returncode, 0, result.stderr)
            project = tomllib.loads((target / "pyproject.toml").read_text())
            self.assertEqual(project["project"]["description"], title)
            self.assertEqual(project["project"]["dependencies"], [])
            self.assertTrue((target / ".git").is_dir())
            self.assertTrue((target / "uv.lock").is_file())
            for directory in ("src/dataset", "src/models", "src/modules", "src/training", "src/evaluation",
                              "src/utils", "tools", "configs", "data/raw", "runs", "results/figures", "tests/unit",
                              "tests/integration", "paper/manuscript", "paper/figures", "paper/supplementary"):
                self.assertTrue((target / directory).is_dir(), directory)
            self.assertEqual(list((target / "runs").iterdir()), [])
            self.assertEqual(list((target / "configs").iterdir()), [])
            self.assertEqual(list((target / "docs/03_Methods_Experiments").iterdir()), [])
            self.assertEqual(list(target.rglob(".gitkeep")), [])
            self.assertFalse((target / "docs/_imports").exists())
            for file in (target / "src").rglob("*.py"):
                self.assertEqual(file.name, "__init__.py")
                self.assertEqual(file.read_text(), "")
            for file in target.rglob("*.md"):
                self.assertIsNone(re.search(r"\{\{[A-Z_]+\}\}", file.read_text()), str(file))
            self.assertIn("研究目标", (target / "README.md").read_text())
            self.assertIn("只建立结构，不安装训练框架", (target / "AGENTS.md").read_text())
            for script in ("train.sh", "evaluate.sh"):
                entry = subprocess.run(["bash", str(target / "scripts" / script)], capture_output=True)
                self.assertEqual(entry.returncode, 2)

    def test_both_questions_are_shown_with_prefilled_values_and_eof_refuses(self):
        with tempfile.TemporaryDirectory(prefix="research-prompts-") as temporary:
            target = Path(temporary) / "new"
            result = invoke(target, "--python", "/missing/suggestion", "--requirements", "原有要求",
                            answers=f"{sys.executable}\n新的特殊要求\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("选择 Python 版本或路径", result.stdout)
            self.assertIn("有没有特殊要求", result.stdout)
            self.assertIn("新的特殊要求", (target / "AGENTS.md").read_text())
            missing = Path(temporary) / "missing-answers"
            result = invoke(missing, answers="")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("必须回答", result.stderr)
            self.assertFalse(missing.exists())

    def test_existing_empty_nonempty_and_symlink_targets_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="research-existing-") as temporary:
            root = Path(temporary)
            empty, full, link = root / "empty", root / "full", root / "link"
            empty.mkdir()
            full.mkdir()
            sentinel = full / "important.txt"
            sentinel.write_text("preserve")
            link.symlink_to(root / "missing")
            for target in (empty, full, link):
                result = invoke(target)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("拒绝覆盖", result.stderr)
            self.assertEqual(list(empty.iterdir()), [])
            self.assertEqual(sentinel.read_text(), "preserve")
            self.assertTrue(link.is_symlink())

    def test_invalid_name_and_missing_dependencies_do_not_publish(self):
        with tempfile.TemporaryDirectory(prefix="research-dependencies-") as temporary:
            root = Path(temporary)
            target = root / "project"
            self.assertNotEqual(invoke(target, "--name", "../bad").returncode, 0)
            self.assertNotEqual(invoke(target, "--python", "/missing/python").returncode, 0)
            binary = root / "bin"
            binary.mkdir()
            env = dict(os.environ, PATH=str(binary))
            self.assertIn("缺少 uv", invoke(target, env=env).stderr)
            real_uv = shutil.which("uv")
            self.assertIsNotNone(real_uv)
            (binary / "uv").symlink_to(real_uv)
            self.assertIn("缺少 git", invoke(target, env=env).stderr)
            self.assertFalse(target.exists())

    def test_custom_asset_files_and_empty_directories_are_copied(self):
        with tempfile.TemporaryDirectory(prefix="research-copy-") as temporary:
            root = Path(temporary)
            skill = root / "skill"
            shutil.copytree(SCRIPT.parent, skill / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(SCRIPT.parents[1] / "assets", skill / "assets")
            data = bytes(range(256))
            (skill / "assets/payload.bin").write_bytes(data)
            # 任意新增的模板目录/文件都必须自动复制，不能依赖初始化器中的预设名单。
            (skill / "assets/custom/nested/empty").mkdir(parents=True)
            (skill / "assets/custom/package").mkdir()
            (skill / "assets/custom/package/__init__.py").write_text('# custom template\n')
            target = root / "new"
            result = invoke(target, script=skill / "scripts/init_research.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((target / "payload.bin").read_bytes(), data)
            self.assertTrue((target / "custom/nested/empty").is_dir())
            self.assertEqual(list((target / "custom/nested/empty").iterdir()), [])
            self.assertEqual((target / "custom/package/__init__.py").read_text(), '# custom template\n')
            self.assertTrue((target / "scripts/train.sh").stat().st_mode & 0o111)

    def test_lock_failure_does_not_publish_half_finished_repository(self):
        with tempfile.TemporaryDirectory(prefix="research-failure-") as temporary:
            root = Path(temporary)
            binary = root / "bin"
            binary.mkdir()
            real_uv = shutil.which("uv")
            self.assertIsNotNone(real_uv)
            proxy = binary / "uv"
            proxy.write_text(
                f"#!{sys.executable}\nimport os, sys\n"
                "if sys.argv[1] == 'lock':\n"
                "    print('simulated lock failure', file=sys.stderr)\n"
                "    raise SystemExit(23)\n"
                f"os.execv({real_uv!r}, [{real_uv!r}, *sys.argv[1:]])\n"
            )
            proxy.chmod(0o755)
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ.get("PATH", ""))
            target = root / "new"
            result = invoke(target, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("simulated lock failure", result.stderr)
            self.assertFalse(target.exists())


class DocumentImportTests(unittest.TestCase):
    def test_mixed_markdown_is_merged_into_correct_documents_with_original_preserved(self):
        with tempfile.TemporaryDirectory(prefix="research-import-") as temporary:
            root = Path(temporary)
            source = root / "旧研究.md"
            content = "# 历史资料\n研究目标是验证跨会话泛化。\n已有文献仍待核对。\n"
            source.write_text(content)
            plan = write_plan(root, source, [
                {"destination": "docs/00_Research_Overview.md", "heading": "Research Goals",
                 "content": "研究目标是验证跨会话泛化。"},
                {"destination": "docs/02_Related_Work.md", "heading": "Research Gaps",
                 "content": "已有文献仍待核对。"},
            ])
            target = root / "new"
            result = invoke(target, "--import-plan", str(plan))
            self.assertEqual(result.returncode, 0, result.stderr)
            overview = (target / "docs/00_Research_Overview.md").read_text()
            related = (target / "docs/02_Related_Work.md").read_text()
            self.assertIn("> 研究目标是验证跨会话泛化。", overview)
            self.assertNotIn("已有文献仍待核对。", overview)
            self.assertIn("> 已有文献仍待核对。", related)
            record = json.loads((target / ".agents/import-manifest.json").read_text())["documents"][0]
            self.assertEqual(record["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual((target / record["archived_original"]).read_bytes(), source.read_bytes())
            self.assertEqual(source.read_text(), content)
            self.assertEqual(len(record["merged_sections"]), 2)

    def test_import_creates_only_requested_method_and_experiment(self):
        with tempfile.TemporaryDirectory(prefix="research-method-import-") as temporary:
            root = Path(temporary)
            source = root / "method.md"
            source.write_text("已有方法动机。\n已有实验目的。\n")
            plan = write_plan(root, source, [
                {"destination": "docs/03_Methods_Experiments/M-001_existing/Method.md",
                 "heading": "算法动机", "content": "已有方法动机。"},
                {"destination": "docs/03_Methods_Experiments/M-001_existing/EXP-001.md",
                 "heading": "目的与判定标准", "content": "已有实验目的。"},
            ])
            target = root / "new"
            result = invoke(target, "--import-plan", str(plan))
            self.assertEqual(result.returncode, 0, result.stderr)
            folder = target / "docs/03_Methods_Experiments/M-001_existing"
            self.assertEqual({p.name for p in folder.iterdir()}, {"Method.md", "EXP-001.md"})
            self.assertIn("已有方法动机。", (folder / "Method.md").read_text())
            self.assertIn("EXP-001", (folder / "EXP-001.md").read_text())
            self.assertEqual(list((target / "runs").iterdir()), [])

    def test_docx_and_preextracted_sources(self):
        with tempfile.TemporaryDirectory(prefix="research-import-format-") as temporary:
            root = Path(temporary)
            source = root / "notes.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>历史研究目标。</w:t></w:r></w:p></w:body></w:document>')
            section = {"destination": "docs/00_Research_Overview.md", "heading": "Research Goals",
                       "content": "历史研究目标。"}
            plan = write_plan(root, source, [section])
            result = invoke(root / "docx-project", "--import-plan", str(plan))
            self.assertEqual(result.returncode, 0, result.stderr)
            # 此处只测试“原件 + 外部已提取文本”的导入接口，不测试 PDF 解析。
            original = root / "original.pdf"
            original.write_bytes(b"PDF preservation fixture")
            extracted = root / "extracted.md"
            extracted.write_text("历史研究目标。")
            plan = write_plan(root, original, [section], text_source=str(extracted))
            result = invoke(root / "extracted-project", "--import-plan", str(plan))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_imports_fail_without_changing_source_or_publishing(self):
        with tempfile.TemporaryDirectory(prefix="research-import-invalid-") as temporary:
            root = Path(temporary)
            source = root / "notes.md"
            source.write_text("原始研究目标。")
            for index, (destination, heading, content) in enumerate((
                ("../AGENTS.md", "Research Goals", "原始研究目标。"),
                ("AGENTS.md", "Research Goals", "原始研究目标。"),
                ("docs/00_Research_Overview.md", "不存在的标题", "原始研究目标。"),
                ("docs/00_Research_Overview.md", "Research Goals", "原文中不存在的结论。"),
            )):
                with self.subTest(index=index):
                    plan = write_plan(root, source, [{"destination": destination, "heading": heading,
                                                     "content": content}])
                    target = root / f"new-{index}"
                    result = invoke(target, "--import-plan", str(plan))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(target.exists())
            self.assertEqual(source.read_text(), "原始研究目标。")

    def test_duplicate_experiment_id_across_methods_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="research-import-id-") as temporary:
            root = Path(temporary)
            source = root / "notes.md"
            source.write_text("历史实验目的。")
            plan = write_plan(root, source, [
                {"destination": f"docs/03_Methods_Experiments/{method}/EXP-001.md",
                 "heading": "目的与判定标准", "content": "历史实验目的。"}
                for method in ("M-001_first", "M-002_second")
            ])
            target = root / "new"
            result = invoke(target, "--import-plan", str(plan))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("实验 ID 已在其他方法使用", result.stderr)
            self.assertFalse(target.exists())

    def test_duplicate_method_id_with_different_names_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="research-import-method-id-") as temporary:
            root = Path(temporary)
            source = root / "notes.md"
            source.write_text("历史方法动机。")
            plan = write_plan(root, source, [
                {"destination": f"docs/03_Methods_Experiments/{method}/Method.md",
                 "heading": "算法动机", "content": "历史方法动机。"}
                for method in ("M-001_first", "M-001_second")
            ])
            target = root / "new"
            result = invoke(target, "--import-plan", str(plan))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("方法 ID 已对应其他方法目录", result.stderr)
            self.assertFalse(target.exists())


def create_downloads_repository(target: Path) -> Path:
    """真实测试调用：只在 Downloads 下新建空测试仓库，保留供用户检查。"""
    target = Path(os.path.abspath(target.expanduser()))
    target.parent.resolve().relative_to(DOWNLOADS.resolve())
    result = invoke(target, "--title", "科研项目结构测试", "--question", "待确定：尚未开始研究",
                    "--requirements", "本仓库只用于初始化结构检查；不生成算法、数据或实验。")
    if result.returncode:
        raise RuntimeError(result.stderr)
    # 不把空测试目录当成通过：实际核验 uv 锁文件、Git 与关键结构。
    subprocess.run(["uv", "lock", "--project", str(target), "--check", "--offline", "--no-cache"], check=True)
    subprocess.run(["git", "-C", str(target), "status", "--short"], check=True)
    assert not list(target.rglob(".gitkeep"))
    assert not list((target / "runs").iterdir())
    assert not list((target / "docs/03_Methods_Experiments").iterdir())
    print(f"Downloads 测试仓库已创建并保留: {target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-test-repo", nargs="?", const=DOWNLOADS / "research-init-zyn-test",
                        type=Path, help="测试通过后在 Downloads 下生成并保留一个新测试仓库")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        return 1
    if args.create_test_repo:
        create_downloads_repository(args.create_test_repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
