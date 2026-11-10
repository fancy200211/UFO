"""Test the real Linux MCP transport and tools, without an LLM.

Starts a temporary localhost server with an ephemeral key, runs read-only
cases against bundled fixtures, and stops only that child server on exit.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def decode(result):
    if isinstance(result.data, dict):
        return result.data
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for block in result.content:
        if block.type == "text":
            return json.loads(block.text)
    raise ValueError("Tool did not return a JSON object")


async def run_cases(url, key):
    async with Client(url) as client:
        tool_names = {tool.name for tool in await client.list_tools()}
        assert {"execute_command", "get_system_info"} <= tool_names
        print("TOOLS:", ", ".join(sorted(tool_names)))
        cases = [
            ("system_info", "get_system_info", {},
             lambda r: all(r.get(k) for k in ("uname", "uptime", "memory", "disk"))),
            ("read_log", "execute_command", {"command": "cat app.txt"},
             lambda r: r.get("success") is True and "service recovered" in r["stdout"]),
            ("count_errors", "execute_command", {"command": "grep -c ERROR app.txt"},
             lambda r: r.get("success") is True and r["stdout"].strip() == "3"),
            ("show_errors", "execute_command", {"command": "grep -n ERROR app.txt"},
             lambda r: r.get("success") is True and len(r["stdout"].splitlines()) == 3),
            # diff exits 1 when files differ: success=False does not mean no useful result.
            ("config_diff", "execute_command", {"command": "diff -u before.conf after.conf"},
             lambda r: r.get("exit_code") == 1 and "+timeout=60" in r["stdout"]),
            ("missing_file", "execute_command", {"command": "cat missing-file.txt"},
             lambda r: r.get("success") is False and bool(r.get("stderr"))),
            ("reject_pipeline", "execute_command", {"command": "cat app.txt | wc -l"},
             lambda r: "blocked by security policy" in r.get("error", "")),
            ("reject_script", "execute_command", {"command": "python3 missing-script.py"},
             lambda r: "blocked by security policy" in r.get("error", "")),
            ("reject_bad_key", "get_system_info", {"api_key": "intentionally-invalid"},
             lambda r: "Authentication failed" in r.get("error", "")),
        ]
        failures = 0
        for name, tool, extra, check in cases:
            arguments = {"api_key": key}
            if tool == "execute_command":
                arguments.update(cwd=str(FIXTURES), timeout=5)
            arguments.update(extra)
            result = await client.call_tool(tool, arguments, timeout=40)
            payload = decode(result)
            passed = not result.is_error and check(payload)
            failures += not passed
            print(f"\n{'PASS' if passed else 'FAIL'} {name}")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\n{len(cases) - failures}/{len(cases)} cases passed")
        return 1 if failures else 0


async def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_urlsafe(32)
    env = dict(os.environ, UFO_MCP_API_KEY=key, PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryFile(mode="w+") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ufo.client.mcp.http_servers.linux_mcp_server",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(150):
                if proc.poll() is not None:
                    log.seek(0)
                    raise RuntimeError("MCP server exited:\n" + log.read())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                raise TimeoutError("MCP server did not become ready in 15 seconds")
            return await run_cases(f"http://127.0.0.1:{port}/mcp", key)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
