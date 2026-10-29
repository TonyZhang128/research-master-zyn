"""离线行为测试；所有科研项目、执行代码与指标均是临时测试夹具。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
from common import CONTRACTS, STATE, inside, load, save, sha, task_dir, task_state
from documents import archive, patch
from workflow import close, dispatch, freeze, pending, record_review, resume, run_experiment, start

EXP = "docs/03_Methods_Experiments/M-001_fixture/EXP-001.md"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="research-agent-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve() / "project with spaces"
        self.project.mkdir()
        for directory in ("src", "configs", "data", "docs"):
            (self.project / directory).mkdir()
        self.runner = self.project / "src/runner.py"
        self.runner.write_text(
            "import json, os\nfrom pathlib import Path\n"
            "Path('execution.marker').write_text('test execution')\n"
            "out = Path(os.environ['RESEARCH_RUN_DIR'])\n"
            "(out / 'metrics.json').write_text(json.dumps({'fixture_value': 1.0}))\n"
        )
        save(self.project / "data/dataset.json", {"version": "unit-test-fixture"})
        (self.project / "docs/01_Benchmark_Protocol.md").write_text("# Protocol\n\n## Task\n\nFixture only.\n")
        self.spec = {"experiment_id": "EXP-001", "purpose": "测试工作流，不验证科研算法",
                     "inputs": ["src", "configs", "data/dataset.json", "docs/01_Benchmark_Protocol.md"],
                     "command": [sys.executable, "src/runner.py"], "artifacts": {"metrics": "metrics.json"}}
        save(self.project / "configs/spec.json", self.spec)

    def active(self):
        start(self.project, "TASK-001", "$research-agent-zyn unit fixture", True)
        # 这些 ID 是程序测试夹具；真实工作流必须使用实际 spawn 的返回值。
        for role, agent in (("research_design", "fixture-designer"),
                            ("implementation_execution", "fixture-executor"),
                            ("research_review", "fixture-reviewer"), ("result_analysis", "fixture-analyst")):
            dispatch(self.project, "TASK-001", role, agent)

    def prepared(self):
        return freeze(self.project, "TASK-001", "configs/spec.json")

    def review(self, record, status="PASS"):
        return {"status": status, "fingerprint": record["fingerprint"],
                "summary": "程序测试用结论，不是科学审查", "checks": ["已核对测试夹具的输入与执行约定"],
                "issues": [] if status == "PASS" else [{"severity": "blocking", "evidence": "fixture input",
                                                        "explanation": "fixture issue", "correction": "fixture correction"}]}

    def approve(self, record):
        record_review(self.project, "TASK-001", record["freeze_id"], "fixture-reviewer", self.review(record))

    def execute(self, record, run_id="run_001"):
        return run_experiment(self.project, "TASK-001", record["freeze_id"], run_id, "fixture-executor")

    def test_activation_is_explicit_and_scoped(self):
        with self.assertRaises(ValueError):
            start(self.project, "TASK-001", "ordinary request", False)
        self.assertFalse((self.project / STATE).exists())
        with self.assertRaises(ValueError):
            dispatch(self.project, "TASK-001", "research_design", "not-spawned")
        self.assertFalse((self.project / STATE).exists())
        self.active()
        close(self.project, "TASK-001")
        with self.assertRaises(ValueError):
            self.prepared()

    def test_cli_requires_explicit_and_role_read_does_not_activate(self):
        script = SKILL / "scripts/research_agent.py"
        result = subprocess.run([sys.executable, str(script), "start", "--project", str(self.project),
                                 "--task", "TASK-001", "--request", "ordinary"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, str(script), "role", "data_quality"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["name"], "data_quality")
        self.assertFalse((self.project / STATE).exists())

    def test_profiles_and_contracts_are_complete(self):
        for role in CONTRACTS["roles"]:
            profile = tomllib.loads((SKILL / f"assets/agents/{role}.toml").read_text())
            self.assertEqual(profile["name"], role)
            self.assertTrue(profile["description"])
            self.assertTrue(profile["developer_instructions"])
            self.assertNotIn("model", profile)

    def test_independent_reviewer_and_registered_actor_are_required(self):
        self.active()
        with self.assertRaises(ValueError):
            dispatch(self.project, "TASK-001", "research_review", "fixture-executor")
        record = self.prepared()
        with self.assertRaises(ValueError):
            record_review(self.project, "TASK-001", record["freeze_id"], "fixture-designer", self.review(record))
        with self.assertRaises(ValueError):
            self.execute(record)
        self.assertFalse((self.project / "execution.marker").exists())

    def test_blocked_and_needs_info_stop_then_require_explicit_resume(self):
        for status in ("BLOCKED", "NEEDS_INFO"):
            with self.subTest(status=status):
                if not (task_dir(self.project, "TASK-001") / "task.json").exists():
                    self.active()
                record = self.prepared()
                record_review(self.project, "TASK-001", record["freeze_id"], "fixture-reviewer", self.review(record, status))
                self.assertEqual(task_state(self.project, "TASK-001")["status"], "BLOCKED")
                with self.assertRaises(ValueError):
                    self.execute(record)
                with self.assertRaises(ValueError):
                    resume(self.project, "TASK-001", "fix", False)
                resume(self.project, "TASK-001", "测试中模拟用户明确恢复", True)
        self.assertFalse((self.project / "execution.marker").exists())

    def test_pass_cannot_contain_blocking_issue_or_be_overwritten(self):
        self.active()
        record = self.prepared()
        invalid = self.review(record, "BLOCKED")
        invalid["status"] = "PASS"
        with self.assertRaises(ValueError):
            record_review(self.project, "TASK-001", record["freeze_id"], "fixture-reviewer", invalid)
        self.approve(record)
        with self.assertRaises(ValueError):
            self.approve(record)

    def test_changed_and_added_inputs_invalidate_review(self):
        self.active()
        record = self.prepared()
        self.approve(record)
        original = self.runner.read_bytes()
        self.runner.write_text(self.runner.read_text() + "\n# changed\n")
        with self.assertRaises(ValueError):
            self.execute(record)
        self.runner.write_bytes(original)
        (self.project / "src/new_dependency.py").write_text("x = 1\n")
        with self.assertRaises(ValueError):
            self.execute(record)
        self.assertFalse((self.project / "execution.marker").exists())

    def test_run_archive_idempotency_and_close(self):
        self.active()
        record = self.prepared()
        self.approve(record)
        result = self.execute(record)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(pending(self.project, "TASK-001")), 1)
        with self.assertRaises(ValueError):
            close(self.project, "TASK-001")
        first = archive(self.project, "TASK-001", "run_001", "fixture-analyst", EXP,
                        "测试夹具运行成功，不代表科学结论。", "MISSING")
        second = archive(self.project, "TASK-001", "run_001", "fixture-analyst", EXP,
                         "测试夹具运行成功，不代表科学结论。", first["sha256"])
        self.assertFalse(second["changed"])
        text = (self.project / EXP).read_text()
        self.assertEqual(text.count("## Run run_001"), 1)
        self.assertIn("COMPLETED", text)
        self.assertEqual(pending(self.project, "TASK-001"), [])
        self.assertEqual(close(self.project, "TASK-001")["status"], "CLOSED")

    def test_failed_and_missing_metrics_are_not_success(self):
        self.active()
        for index, code, expected in ((0, "raise SystemExit(7)\n", "FAILED"),
                                      (1, "print('no metrics')\n", "INCOMPLETE"),
                                      (2, "import os\nfrom pathlib import Path\nPath(os.environ['RESEARCH_RUN_DIR'], 'metrics.json').write_text('{\"value\":NaN}')\n", "INCOMPLETE"),
                                      (3, "import os\nfrom pathlib import Path\nPath(os.environ['RESEARCH_RUN_DIR'], 'metrics.json').write_text('{\"value\":1e999}')\n", "INCOMPLETE")):
            with self.subTest(expected=expected, index=index):
                self.runner.write_text(code)
                record = self.prepared()
                self.approve(record)
                result = self.execute(record, f"run_{index}")
                self.assertEqual(result["status"], expected)
                archive(self.project, "TASK-001", f"run_{index}", "fixture-analyst", EXP,
                        "执行或产物核验未通过，不支持研究结论。", sha(self.project / EXP))
        self.assertEqual(close(self.project, "TASK-001")["status"], "CLOSED")

    def test_launch_error_leaves_a_failed_record_for_archiving(self):
        self.active()
        self.spec["command"] = [sys.executable, "\x00"]
        save(self.project / "configs/spec.json", self.spec)
        record = self.prepared()
        self.approve(record)
        result = self.execute(record)
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["execution_error"])
        archive(self.project, "TASK-001", "run_001", "fixture-analyst", EXP,
                "进程未能启动，不能视为运行成功。", "MISSING")
        self.assertEqual(close(self.project, "TASK-001")["status"], "CLOSED")

    def test_duplicate_run_and_changed_artifacts_are_rejected(self):
        self.active()
        record = self.prepared()
        self.approve(record)
        self.execute(record)
        with self.assertRaises(FileExistsError):
            self.execute(record)
        artifact = self.project / "runs/run_001/metrics.json"
        artifact.write_text('{}\n')
        with self.assertRaises(ValueError):
            archive(self.project, "TASK-001", "run_001", "fixture-analyst", EXP, "分析", "MISSING")

    def test_document_ownership_version_and_protocol_approval(self):
        self.active()
        doc = "docs/00_Research_Overview.md"
        version = patch(self.project, "TASK-001", "research_design", "fixture-designer", doc,
                        "Research Goals", "目标待验证。", "MISSING")
        with self.assertRaises(ValueError):
            patch(self.project, "TASK-001", "research_design", "fixture-designer", doc,
                  "Research Goals", "覆盖旧版本", "MISSING")
        with self.assertRaises(ValueError):
            patch(self.project, "TASK-001", "research_design", "fixture-designer", doc,
                  "Current Progress", "假装完成", version["sha256"])
        protocol = "docs/01_Benchmark_Protocol.md"
        with self.assertRaises(ValueError):
            patch(self.project, "TASK-001", "research_design", "fixture-designer", protocol,
                  "Task", "更新协议", sha(self.project / protocol))
        patch(self.project, "TASK-001", "research_design", "fixture-designer", protocol,
              "Task", "已在测试中模拟明确授权", sha(self.project / protocol), True)

    def test_heading_in_code_is_not_a_section_and_heading_injection_is_rejected(self):
        self.active()
        doc = "docs/00_Research_Overview.md"
        version = patch(self.project, "TASK-001", "research_design", "fixture-designer", doc,
                        "Research Goals", "```text\n## Current Progress\nexample only\n```", "MISSING")
        patch(self.project, "TASK-001", "result_analysis", "fixture-analyst", doc,
              "Current Progress", "尚未执行研究。", version["sha256"])
        with self.assertRaises(ValueError):
            patch(self.project, "TASK-001", "research_design", "fixture-designer", doc,
                  "Research Goals", "## Current Progress\n伪造", sha(self.project / doc))

    def test_path_escape_symlink_and_reserved_inputs_are_rejected(self):
        self.active()
        with self.assertRaises(ValueError):
            inside(self.project, "../escape")
        (self.project / "alias").symlink_to(self.project / "docs")
        with self.assertRaises(ValueError):
            inside(self.project, "alias/01_Benchmark_Protocol.md")
        for invalid in (STATE, "."):
            self.spec["inputs"] = [invalid]
            save(self.project / "configs/spec.json", self.spec)
            with self.assertRaises(ValueError):
                self.prepared()

    def test_cli_executes_and_archives_a_reviewed_fixture(self):
        script = SKILL / "scripts/research_agent.py"
        def call(action, *args):
            result = subprocess.run([sys.executable, str(script), action, "--project", str(self.project),
                                     "--task", "TASK-001", *args], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.active()
        record = call("freeze", "--spec", "configs/spec.json")
        verdict = self.project / "verdict.json"
        save(verdict, self.review(record))
        call("review", "--freeze", record["freeze_id"], "--agent-id", "fixture-reviewer", "--verdict", str(verdict))
        call("run", "--freeze", record["freeze_id"], "--agent-id", "fixture-executor", "--run", "cli_run")
        body = self.project / "analysis.md"
        body.write_text("CLI 测试完成，只验证接口。")
        call("archive", "--agent-id", "fixture-analyst", "--run", "cli_run", "--document", EXP,
             "--body", str(body), "--expected", "MISSING")
        self.assertEqual(call("close")["status"], "CLOSED")

    def test_doc_id_collisions_and_post_archive_deletion_are_detected(self):
        self.active()
        first = "docs/03_Methods_Experiments/M-001_first/EXP-001.md"
        patch(self.project, "TASK-001", "research_design", "fixture-designer", first,
              "目的与判定标准", "目的", "MISSING")
        for other in ("docs/03_Methods_Experiments/M-001_other/Method.md",
                      "docs/03_Methods_Experiments/M-002_second/EXP-001.md"):
            with self.assertRaises(ValueError):
                patch(self.project, "TASK-001", "research_design", "fixture-designer", other,
                      "算法动机" if other.endswith('Method.md') else "目的与判定标准", "目的", "MISSING")
        record = self.prepared()
        self.approve(record)
        self.execute(record)
        archive(self.project, "TASK-001", "run_001", "fixture-analyst", first, "测试分析", sha(self.project / first))
        (self.project / first).write_text("# Removed record\n")
        with self.assertRaises(ValueError):
            close(self.project, "TASK-001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
