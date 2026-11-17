"""Exercise the task gate as a command against isolated Git repositories."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
TASK = "AUDIT-001"
PENDING = "- V01 | PENDING | 先执行仓库检查"
AUTO_PASS = "- V01 | PASS | 2026-11-16 | Python 3.12 | 基准检查退出 0"
MANUAL_PASS = "- V02 | PASS | 2026-11-16 | 复核人：测试记录 | 按样例逐项复核"
HARDWARE_PASS = "- V03 | PASS | 2026-11-16 | 样例台架和设备 | 测试夹具中的实机结果记录"


class VerifyTasksTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git("init", "-q")
        (self.root / "scripts").mkdir()
        (self.root / "tasks").mkdir()
        shutil.copyfile(SCRIPTS / "verify_tasks.py", self.root / "scripts/verify_tasks.py")
        self.write("tasks/TEMPLATE.md", "# 模板\n")
        self.write(".gitignore", "backend/data/\n")
        self.write("backend/app/old.py", "value = 1\n")
        self.git("add", "--", "backend/app/old.py")
        self.record()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return file

    def record(self, status="IN_PROGRESS", results=PENDING, modes=("AUTO",),
               audit=False, blocked="无"):
        completed = status == "COMPLETED"
        stages = "- [x] S1 已核对输入与输出" if completed else "- [ ] S1 等待校验"
        criteria = "\n".join(
            f"- V{number:02} | {mode} | 核对该验证类型的预期"
            for number, mode in enumerate(modes, 1)
        )
        self.write("tasks/PROGRESS.md", f"# 进度\n\n## 任务\n\n- [{TASK}]({TASK}.md) — {status}\n")
        self.task = self.write(f"tasks/{TASK}.md", f"""# 验收测试任务

## 任务 ID

{TASK}

## 当前状态

{status}

## 目标与非目标

- 目标：检查样例
- 非目标：访问真实设备

## 工作范围

样例文件

## 逐文件审计台账

{TASK + ".audit.md" if audit else "无"}

## 前置依赖

无

## 阶段计划

{stages}

## 验收标准

{criteria}

## 验证方式及证据

{results}

## 已完成工作

{"样例已验证" if completed else "无"}

## 剩余工作

{"无" if completed else "执行样例"}

## 阻塞原因

{blocked}

## 下一步动作

{"无" if completed else "运行校验"}
""")

    def rewrite_task(self, old, new):
        content = self.task.read_text(encoding="utf-8")
        self.assertIn(old, content)
        self.task.write_text(content.replace(old, new), encoding="utf-8")

    def run_gate(self, *args):
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/verify_tasks.py"), *args],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )

    def assert_rejected(self, *messages, args=()):
        result = self.run_gate(*args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for message in messages:
            self.assertIn(message, result.stderr)

    def assert_accepted(self, *messages, args=()):
        result = self.run_gate(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        for message in messages:
            self.assertIn(message, result.stdout)

    def ledger(self, reviewed=(), partial=()):
        entries = []
        for path in reviewed:
            digest = hashlib.sha256((self.root / path).read_bytes()).hexdigest()
            entries.append(f"- {path} | REVIEWED | {digest} | 样例已语义复核")
        entries.extend(f"- {path} | PARTIAL | 样例仅检查了入口" for path in partial)
        return self.write(f"tasks/{TASK}.audit.md", "# 逐文件台账\n\n" + "\n".join(entries) + "\n")

    def test_valid_pending_verification_and_completed_task(self):
        self.assert_accepted("任务校验通过（1 个任务）")
        self.record("COMPLETED", results="\n".join((AUTO_PASS, MANUAL_PASS, HARDWARE_PASS)),
                    modes=("AUTO", "MANUAL", "HARDWARE"))
        self.assert_accepted()

    def test_missing_required_section(self):
        self.rewrite_task("## 下一步动作", "## 备注")
        self.assert_rejected("缺少必需小节或内容：下一步动作")

    def test_unknown_status_and_index_status_mismatch(self):
        self.rewrite_task("IN_PROGRESS", "DONE")
        self.assert_rejected("非法状态 DONE", "状态与任务文件不一致")

    def test_missing_index_entry(self):
        self.write("tasks/PROGRESS.md", "# 进度\n\n## 任务\n")
        self.assert_rejected(f"未登记 {TASK}.md")

    def test_blocked_requires_a_reason(self):
        self.record("BLOCKED")
        self.assert_rejected("BLOCKED 缺少阻塞原因和恢复条件")

    def test_missing_validation_result_in_active_task(self):
        self.record("VERIFYING", modes=("AUTO", "MANUAL"))
        self.assert_rejected("V02 缺少验证结果")

    def test_completed_task_rejects_missing_pending_and_failed_results(self):
        failed = "- V03 | FAIL | 2026-11-16 | 样例台架 | 缺少预期响应"
        self.record("COMPLETED", results=f"- V02 | PENDING | 等待复核\n{failed}",
                    modes=("AUTO", "MANUAL", "HARDWARE"))
        self.assert_rejected("V01 缺少验证结果", "V02 必须为 PASS，当前 PENDING",
                             "V03 必须为 PASS，当前 FAIL")

    def test_pass_requires_date_environment_and_evidence(self):
        self.record("COMPLETED", results="- V01 | PASS | 2026-02-31 | Python | 校验通过")
        self.assert_rejected("V01 缺少有效结果、日期、环境或证据", "V01 缺少验证结果")
        self.rewrite_task("2026-02-31 | Python | 校验通过", "2026-11-16 | Python | TBD")
        self.assert_rejected("V01 缺少有效结果、日期、环境或证据")

    def test_duplicate_and_undeclared_evidence_rejected(self):
        self.record(results=f"{PENDING}\n{AUTO_PASS}\n- V02 | PENDING | 待执行")
        self.assert_rejected("V01 有多条当前结果", "结果未关联有效验收项")

    def test_completed_task_requires_finished_stages(self):
        self.record("COMPLETED", results=AUTO_PASS)
        self.rewrite_task("- [x]", "- [ ]")
        self.assert_rejected("已完成任务仍有未完成阶段")

    def test_audit_detects_untracked_and_tracked_gaps_but_respects_exclusions(self):
        self.record(audit=True)
        self.write("client/src/新增 文件.ts", "export const a = 1;\n")
        self.write("backend/data/ignored.py", "ignored\n")
        self.write("backend/vendor/scrcpy/distributed.js", "vendor\n")
        self.ledger(reviewed=("scripts/verify_tasks.py", ".gitignore"))
        self.assert_accepted("当前已审 2/4，缺失 2")
        result = self.run_gate("--audit-coverage", TASK)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("缺少审阅记录：backend/app/old.py", result.stderr)
        self.assertIn("缺少审阅记录：client/src/新增 文件.ts", result.stderr)
        self.assertNotIn("ignored.py", result.stderr)
        self.assertNotIn("distributed.js", result.stderr)

    def test_completed_audit_cannot_bypass_partial_or_stale_coverage(self):
        self.record("COMPLETED", results=AUTO_PASS, audit=True)
        self.ledger(reviewed=("scripts/verify_tasks.py", ".gitignore"),
                    partial=("backend/app/old.py",))
        self.assert_rejected("部分审阅：backend/app/old.py")

        self.ledger(reviewed=("scripts/verify_tasks.py", ".gitignore", "backend/app/old.py"))
        self.assert_accepted("当前已审 3/3", args=("--audit-coverage", TASK))
        self.write("backend/app/old.py", "value = 2\n")
        self.assert_rejected("内容已变更，需复核：backend/app/old.py")

    def test_audit_rejects_reused_or_unknown_entries(self):
        self.record(audit=True)
        self.ledger(partial=("backend/app/old.py", "backend/app/old.py"))
        self.assert_rejected("重复审阅记录 backend/app/old.py")
        self.ledger(partial=("backend/app/missing.py",))
        self.assert_rejected("路径不在当前枚举范围")

    def test_audit_exclusions_require_scope_declaration_and_current_digest(self):
        self.record(audit=True)
        source = "backend/app/old.py"
        ledger = self.ledger(reviewed=("scripts/verify_tasks.py", ".gitignore", source))
        body = ledger.read_text(encoding="utf-8").replace(
            f"- {source} | REVIEWED |", f"- {source} | EXCLUDED |"
        )
        ledger.write_text(body, encoding="utf-8")
        self.assert_rejected("EXCLUDED 状态与任务工作范围不一致")

        self.rewrite_task("样例文件", f"样例文件，用户已确认排除\n\n- 排除文件：{source}")
        self.assert_accepted("当前已审 2/2", "范围排除 1", args=("--audit-coverage", TASK))
        self.write(source, "value = 2\n")
        self.assert_rejected("内容已变更，需复核", args=("--audit-coverage", TASK))

    def test_declared_exclusion_still_requires_a_record_and_real_path(self):
        self.record(audit=True)
        self.ledger(reviewed=("scripts/verify_tasks.py", ".gitignore"))
        self.rewrite_task("样例文件", "样例文件\n\n- 排除文件：backend/app/old.py")
        self.assert_rejected("缺少审阅记录：backend/app/old.py", args=("--audit-coverage", TASK))
        self.rewrite_task("排除文件：backend/app/old.py", "排除文件：backend/**")
        self.assert_rejected("排除文件不在独立清单")

    def test_audit_requires_ledger_and_valid_selector(self):
        self.record(audit=True)
        self.assert_rejected("缺少审计台账")
        self.ledger()
        self.assert_rejected("未找到逐文件审计任务", args=("--audit-coverage", "MISSING-001"))


if __name__ == "__main__":
    unittest.main()
