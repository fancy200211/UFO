"""Preview or submit natural-language LinuxAgent cases; no model call by default."""
import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def cases(directory):
    prefix = f"测试资料目录为 {directory}。只读取，不修改任何文件。"
    return {
        "system": "查询 Linux 系统版本、运行时间、内存和磁盘使用情况，返回实际查询结果。",
        "logs": prefix + "读取 app.txt，返回所有 ERROR 行，统计 ERROR 数量并总结错误类型。",
        "diff": prefix + "比较 before.conf 和 after.conf，列出配置变更。注意 diff 返回 1 表示发现差异，不等于命令运行故障。",
        "recover": prefix + "先尝试读取 missing-file.txt；如果不存在，列出目录并读取 app.txt。最后解释发生了什么，返回实际日志内容。",
        "unsupported": "请操作 Ubuntu 桌面，打开计算器，用鼠标输入 1+2 并截图。若现有工具不支持，请返回 FAIL 并如实说明原因，不要虚构操作结果。",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=cases(FIXTURES), default="logs")
    parser.add_argument("--fixtures", default=str(FIXTURES), help="Fixture path on the Linux device")
    parser.add_argument("--server", default="http://127.0.0.1:5000")
    parser.add_argument("--client-id", default="linux_device_1")
    parser.add_argument("--send", action="store_true", help="Submit the task (uses configured LLM)")
    parser.add_argument("--result", metavar="TASK_NAME", help="Query one previously submitted task")
    args = parser.parse_args()
    task = {
        "client_id": args.client_id,
        "request": cases(args.fixtures)[args.case],
        "task_name": f"linux-study-{args.case}-{uuid4().hex[:8]}",
    }
    if not args.send and not args.result:
        print(json.dumps(task, ensure_ascii=False, indent=2))
        print("\nPreview only. Add --send to submit this task.")
        return
    key = os.environ.get("UFO_SERVER_API_KEY", "ufo123")
    if not key:
        parser.error("Set UFO_SERVER_API_KEY to the Device Agent Server's API key")
    endpoint = "/api/task_result/" + quote(args.result, safe="") if args.result else "/api/dispatch"
    body = None if args.result else json.dumps(task).encode("utf-8")
    request = Request(args.server.rstrip("/") + endpoint, data=body,
                      headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
    except HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
    except URLError as exc:
        raise SystemExit(f"Server connection failed: {exc.reason}")


if __name__ == "__main__":
    main()
