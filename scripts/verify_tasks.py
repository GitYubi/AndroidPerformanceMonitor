#!/usr/bin/env python3
"""Validate repository task records and independent per-file audit coverage."""

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATUSES = {"PENDING", "IN_PROGRESS", "VERIFYING", "BLOCKED", "COMPLETED"}
VERIFY_MODES = {"AUTO", "MANUAL", "HARDWARE"}
TASK_ID = r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+"
CRITERION_ID = r"[A-Z][A-Z0-9-]*"
TASK_SECTIONS = (
    "任务 ID", "当前状态", "目标与非目标", "工作范围", "逐文件审计台账",
    "前置依赖", "阶段计划", "验收标准", "验证方式及证据", "已完成工作",
    "剩余工作", "阻塞原因", "下一步动作",
)


def sections(path, errors):
    content = path.read_text(encoding="utf-8")
    name = f"tasks/{path.name}"
    if not re.match(r"^# \S", content):
        errors.append(f"{name}: 缺少一级标题")
    headings = list(re.finditer(r"^## (.+)$", content, re.MULTILINE))
    result = {}
    for i, heading in enumerate(headings):
        title = heading[1]
        if title in result:
            errors.append(f"{name}: 重复小节「{title}」")
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        result[title] = content[heading.end():end].strip()
    return result


def has_content(value):
    return value.strip().lower() not in {"", "-", "—", "无", "tbd", "todo", "n/a", "待补", "待填写"}


def rows(body):
    return [line.strip() for line in body.splitlines() if line.strip()]


def fields(line, count):
    if not line.startswith("- "):
        return []
    return [field.strip() for field in line[2:].split(" | ", count - 1)]


def iso_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def verify_evidence(record, name, errors):
    criteria = set()
    for line in rows(record["验收标准"]):
        parts = fields(line, 3)
        if (len(parts) != 3 or not re.fullmatch(CRITERION_ID, parts[0])
                or parts[1] not in VERIFY_MODES or not has_content(parts[2])):
            errors.append(f"{name}: 非法验收项「{line}」")
            continue
        if parts[0] in criteria:
            errors.append(f"{name}: 重复验收项 {parts[0]}")
        criteria.add(parts[0])
    if not criteria:
        errors.append(f"{name}: 至少需要一条有效验收标准")

    results = {}
    for line in rows(record["验证方式及证据"]):
        parts = fields(line, 5)
        if len(parts) < 2 or parts[0] not in criteria:
            errors.append(f"{name}: 结果未关联有效验收项「{line}」")
            continue
        criterion, result = parts[:2]
        pending = result == "PENDING" and len(parts) == 3 and has_content(parts[2])
        recorded = (result in {"PASS", "FAIL"} and len(parts) == 5
                    and iso_date(parts[2]) and all(has_content(value) for value in parts[3:]))
        if not (pending or recorded):
            errors.append(f"{name}: {criterion} 缺少有效结果、日期、环境或证据")
            continue
        if criterion in results:
            errors.append(f"{name}: {criterion} 有多条当前结果")
        results[criterion] = result

    for criterion in sorted(criteria):
        if criterion not in results:
            errors.append(f"{name}: {criterion} 缺少验证结果")
        elif record["当前状态"] == "COMPLETED" and results[criterion] != "PASS":
            errors.append(f"{name}: 已完成任务的 {criterion} 必须为 PASS，当前 {results[criterion]}")


def verify_record(path, errors):
    name = f"tasks/{path.name}"
    record = sections(path, errors)
    missing = [title for title in TASK_SECTIONS if not record.get(title)]
    if missing:
        errors.append(f"{name}: 缺少必需小节或内容：{'、'.join(missing)}")
        return None
    if not re.fullmatch(TASK_ID, record["任务 ID"]) or record["任务 ID"] != path.stem:
        errors.append(f"{name}: 任务 ID 格式错误或与文件名不一致")
    status = record["当前状态"]
    if status not in STATUSES:
        errors.append(f"{name}: 非法状态 {status}")
    goals = rows(record["目标与非目标"])
    for prefix in ("- 目标：", "- 非目标："):
        if not any(line.startswith(prefix) and has_content(line[len(prefix):]) for line in goals):
            errors.append(f"{name}: 缺少{prefix[2:-1]}")
    audit = record["逐文件审计台账"]
    if audit not in {"无", f"{path.stem}.audit.md"}:
        errors.append(f"{name}: 审计台账须为 {path.stem}.audit.md 或「无」")

    stages = [re.fullmatch(r"- \[([ x])\] (\S.*)", line) for line in rows(record["阶段计划"])]
    if not stages or not all(stages):
        errors.append(f"{name}: 阶段计划须使用任务清单格式")
    elif status == "COMPLETED" and any(stage[1] != "x" for stage in stages):
        errors.append(f"{name}: 已完成任务仍有未完成阶段")
    if status == "COMPLETED":
        if not has_content(record["已完成工作"]):
            errors.append(f"{name}: 已完成任务须填写「已完成工作」")
        for title in ("剩余工作", "阻塞原因", "下一步动作"):
            if record[title] != "无":
                errors.append(f"{name}: 已完成任务的「{title}」须为「无」")
    else:
        for title in ("剩余工作", "下一步动作"):
            if not has_content(record[title]):
                errors.append(f"{name}: 未完成任务须填写「{title}」")
    if status == "BLOCKED" and not has_content(record["阻塞原因"]):
        errors.append(f"{name}: BLOCKED 缺少阻塞原因和恢复条件")
    verify_evidence(record, name, errors)
    return record


def read_index(path, errors):
    index = {}
    content = sections(path, errors)
    if "任务" not in content:
        errors.append("tasks/PROGRESS.md: 缺少「任务」小节")
    for line in rows(content.get("任务", "")):
        match = re.fullmatch(rf"- \[({TASK_ID})\]\(\1\.md\) — (\S+)", line)
        if not match:
            errors.append(f"tasks/PROGRESS.md: 非法任务条目「{line}」")
            continue
        task_id, status = match.groups()
        if task_id in index:
            errors.append(f"tasks/PROGRESS.md: 重复任务 {task_id}")
        if status not in STATUSES:
            errors.append(f"tasks/PROGRESS.md: {task_id} 非法状态 {status}")
        index[task_id] = status
    return index


def repository_files():
    # Git supplies the scope independently of the report and ledger.
    search = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    )
    paths = set()
    for path in map(os.fsdecode, search.stdout.rstrip(b"\0").split(b"\0")):
        if not path or path.startswith("backend/vendor/"):
            continue
        if path.startswith("tasks/") and path.endswith(".md"):
            continue
        source = ROOT / path
        if source.is_symlink() or source.is_file():
            paths.add(path)
    return paths


def file_digest(path):
    if path.is_symlink():
        return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_audit(ledger, sources, exclusions, require_complete, errors):
    name = f"tasks/{ledger.name}"
    lines = rows(ledger.read_text(encoding="utf-8"))
    if not lines or not re.match(r"^# \S", lines[0]):
        errors.append(f"{name}: 缺少审计台账标题")
        return
    entries = {}
    for line in lines[1:]:
        parts = fields(line, 4)
        if len(parts) < 2 or parts[0] not in sources:
            errors.append(f"{name}: 无效条目或路径不在当前枚举范围「{line}」")
            continue
        path, status = parts[:2]
        partial = status == "PARTIAL" and len(parts) == 3 and has_content(parts[2])
        has_digest = (status in {"REVIEWED", "EXCLUDED"} and len(parts) == 4
                      and re.fullmatch(r"[0-9a-f]{64}", parts[2]) and has_content(parts[3]))
        if not (partial or has_digest):
            errors.append(f"{name}: {path} 缺少有效审阅状态、摘要或证据")
            continue
        if (status == "EXCLUDED") != (path in exclusions):
            errors.append(f"{name}: {path} 的 EXCLUDED 状态与任务工作范围不一致")
            continue
        if path in entries:
            errors.append(f"{name}: 重复审阅记录 {path}")
        entries[path] = parts

    missing = sorted(sources - entries.keys())
    partial_paths = []
    changed = []
    current = excluded = 0
    for path, parts in sorted(entries.items()):
        if parts[1] == "PARTIAL":
            partial_paths.append(path)
        elif file_digest(ROOT / path) != parts[2]:
            changed.append(path)
        elif parts[1] == "EXCLUDED":
            excluded += 1
        else:
            current += 1
    print(f"{ledger.name}: 当前已审 {current}/{len(sources - exclusions)}，"
          f"缺失 {len(missing)}，部分审阅 {len(partial_paths)}，内容已变更 {len(changed)}，"
          f"范围排除 {excluded}")
    if require_complete:
        for label, paths in (("缺少审阅记录", missing), ("部分审阅", partial_paths), ("内容已变更，需复核", changed)):
            errors.extend(f"{name}: {label}：{path}" for path in paths)


def verify_all(target, errors):
    tasks_dir = ROOT / "tasks"
    if not (tasks_dir / "TEMPLATE.md").is_file():
        errors.append("tasks/TEMPLATE.md: 缺少任务模板")
    index = read_index(tasks_dir / "PROGRESS.md", errors)
    files = {p.stem: p for p in tasks_dir.glob("*.md")
             if p.name not in {"PROGRESS.md", "TEMPLATE.md"} and not p.name.endswith(".audit.md")}

    for task_id in sorted(files.keys() - index.keys()):
        errors.append(f"tasks/PROGRESS.md: 未登记 {task_id}.md")
    for task_id in sorted(index.keys() - files.keys()):
        errors.append(f"tasks/PROGRESS.md: 找不到 {task_id}.md")

    sources = None
    ledgers = set()
    for task_id, path in sorted(files.items()):
        record = verify_record(path, errors)
        if record is None:
            continue
        status = record["当前状态"]
        if task_id in index and status != index[task_id]:
            errors.append(f"tasks/PROGRESS.md: {task_id} 状态与任务文件不一致")
        ledger_name = f"{task_id}.audit.md"
        if record["逐文件审计台账"] != ledger_name:
            continue
        ledgers.add(ledger_name)
        ledger = tasks_dir / ledger_name
        if not ledger.is_file():
            errors.append(f"tasks/{ledger_name}: 缺少审计台账")
            continue
        if sources is None:
            sources = repository_files()
        prefix = "- 排除文件："
        exclusions = [line[len(prefix):].strip() for line in rows(record["工作范围"])
                      if line.startswith(prefix)]
        if len(exclusions) != len(set(exclusions)):
            errors.append(f"tasks/{path.name}: 重复声明排除文件")
        for unknown in sorted(set(exclusions) - sources):
            errors.append(f"tasks/{path.name}: 排除文件不在独立清单「{unknown}」")
        verify_audit(ledger, sources, set(exclusions), status == "COMPLETED" or task_id == target, errors)

    for ledger in sorted(tasks_dir.glob("*.audit.md")):
        if ledger.name not in ledgers:
            errors.append(f"tasks/{ledger.name}: 无对应任务引用")
    if target and f"{target}.audit.md" not in ledgers:
        errors.append(f"{target}: 未找到逐文件审计任务")
    return len(files)


def main():
    parser = argparse.ArgumentParser(description="检查项目任务、验收证据和审计覆盖")
    parser.add_argument("--audit-coverage", metavar="任务ID", help="要求指定审计任务具备完整、有效的覆盖")
    args = parser.parse_args()
    errors = []
    try:
        count = verify_all(args.audit_coverage, errors)
    except subprocess.CalledProcessError as exc:
        errors.append(f"Git 文件枚举失败：{os.fsdecode(exc.stderr).strip()}")
    except (OSError, UnicodeError) as exc:
        errors.append(f"读取校验输入失败：{exc}")
    if errors:
        for error in errors:
            print(f"失败 {error}", file=sys.stderr)
        return 1
    print(f"任务校验通过（{count} 个任务）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
