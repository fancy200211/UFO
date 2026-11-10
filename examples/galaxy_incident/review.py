"""Audit selected Galaxy/Linux trace files; optionally check a final JSON report.

This is a structural checker, not an LLM judge or proof of causality.
"""
import argparse
import json
from pathlib import Path
import shlex
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
READ_TOOLS = {"cat", "head", "tail", "grep", "cut"}


def trace_summary(files):
    required = {str(p.relative_to(HERE / "evidence")) for p in (HERE / "evidence").rglob("*") if p.is_file()}
    seen, missing_attempts, diffs, hashes = set(), set(), [], []
    calls, steps, rejected = 0, 0, 0
    issues = []
    device_files = []
    for path in files:
        is_device = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                issues.append(f"Invalid JSON: {path}:{number}")
                continue
            actions = item.get("action", [])
            if not isinstance(actions, list):
                continue
            cli_actions = [a for a in actions if isinstance(a, dict) and a.get("function", "").split("/")[-1] == "execute_command"]
            if not cli_actions:
                continue
            is_device = True
            steps += 1
            for action in cli_actions:
                calls += 1
                args = action.get("arguments", {})
                command = args.get("command", "")
                try:
                    tokens = shlex.split(command)
                except ValueError:
                    issues.append(f"Malformed command: {path}:{number}")
                    continue
                if not tokens:
                    continue
                outer = action.get("result", {})
                payload = outer.get("result", {}) if isinstance(outer, dict) else {}
                if not isinstance(payload, dict):
                    issues.append(f"Unrecognized tool payload: {path}:{number}")
                    continue
                if "blocked" in str(payload.get("error", "")).lower():
                    rejected += 1
                operands = []
                for token in tokens[1:]:
                    # Use actual fixture root/cwd; arbitrary suffixes cannot prove a read.
                    candidate = Path(token)
                    if not candidate.is_absolute():
                        candidate = Path(args.get("cwd") or ROOT) / candidate
                    try:
                        relative = str(candidate.resolve().relative_to((HERE / "evidence").resolve()))
                    except ValueError:
                        continue
                    operands.append(relative)
                if tokens[0] in READ_TOOLS and payload.get("success") is True:
                    if len(operands) != 1:
                        issues.append(f"Content read must target one file: {path}:{number}")
                    seen.update(set(operands) & required)
                if tokens[0] == "cat" and payload.get("success") is False and payload.get("stderr"):
                    missing_attempts.update(set(operands) & {"collection/current.log", "collection/metrics-live.csv"})
                if tokens[0] == "diff" and payload.get("exit_code") == 1:
                    if set(operands) == {"config/default.conf", "config/override.conf"}:
                        diffs.append(command)
                if tokens[0] == "sha256sum" and payload.get("success") is True and operands == ["cache/latest-summary.txt"]:
                    hashes.append(command)
        if is_device:
            device_files.append(str(path))
    return {
        "device_subtask_logs_with_cli_actions": device_files,
        "process_records_with_cli_actions": steps,
        "execute_command_calls": calls,
        "distinct_evidence_files_read": len(seen),
        "required_evidence_files": len(required),
        "unread_evidence": sorted(required - seen),
        "missing_path_attempts": sorted(missing_attempts),
        "config_diff_observed": bool(diffs),
        "cache_hash_observed": bool(hashes),
        "policy_rejections": rejected,
        "format_or_read_issues": issues,
        "note": "Read detection cannot prove full comprehension; inspect contents, DAG dependencies and final reasoning manually. No automatic overall PASS.",
    }


def check_answer(answer):
    expected = json.loads((HERE / "expected.json").read_text())
    failures = []
    for key, wanted in expected.items():
        actual = answer.get(key)
        if isinstance(wanted, list):
            if key == "window_utc":
                passed = actual == wanted
            else:
                passed = (isinstance(actual, list)
                          and all(isinstance(value, str) for value in actual)
                          and sorted(actual) == sorted(wanted))
        elif isinstance(wanted, bool):
            passed = actual is wanted
        else:
            passed = not isinstance(actual, bool) and actual == wanted
        if not passed:
            failures.append({"field": key, "expected": wanted, "actual": actual})
    for key in ("root_cause_explanation", "evidence", "limitations"):
        if not answer.get(key):
            failures.append({"field": key, "error": "missing or empty"})
    return {"checked_fields": len(expected) + 3, "failures": failures,
            "manual_review": "核验引用真实性、因果链、批准条件、数据缺失说明。字段匹配不是完整验收。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--answer", type=Path, help="A JSON object copied from the final review subtask; never expected.json")
    args = parser.parse_args()
    # Task IDs in Galaxy device logs embed the Galaxy task name. Match only this run.
    files = []
    for path in (ROOT / "logs").rglob("response.log"):
        if path.parent.name == args.task_name or path.parent.name.startswith("galaxy_" + args.task_name + "_"):
            files.append(path)
    if not files:
        parser.error("No matching response.log found; check task-name and the server's log location")
    print(json.dumps(trace_summary(sorted(files)), ensure_ascii=False, indent=2))
    if args.answer:
        if args.answer.resolve() == (HERE / "expected.json").resolve():
            parser.error("expected.json is the reference, not the model's answer")
        answer = json.loads(args.answer.read_text(encoding="utf-8"))
        if not isinstance(answer, dict):
            parser.error("answer must be a JSON object")
        result = check_answer(answer)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1 if result["failures"] else 0)


if __name__ == "__main__":
    main()
