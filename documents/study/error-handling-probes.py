"""Offline probes of UFO error propagation; no model or device connections.

Run from the UFO3 repository root with its Python environment.
These assertions describe observed behavior, including defects, not desired behavior.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastmcp import Client, FastMCP
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from aip.messages import Command, ResultStatus, TaskStatus
from config.config_loader import get_ufo_config
from galaxy.constellation.enums import DependencyType
from galaxy.constellation.orchestrator.orchestrator import TaskConstellationOrchestrator
from galaxy.constellation.task_constellation import TaskConstellation
from galaxy.constellation.task_star import TaskStar
from galaxy.constellation.task_star_line import TaskStarLine
from galaxy.core.types import ExecutionResult
from ufo.client.computer import CommandRouter
from ufo.module.basic import BaseSession
from ufo.server.services.session_manager import SessionManager


def show(name, **values):
    print(json.dumps({"probe": name, **values}, ensure_ascii=False, default=str))


class FakeSession:
    is_error = BaseSession.is_error
    is_finished = BaseSession.is_finished

    def __init__(self, status, step=1, total_rounds=1):
        self.current_round = SimpleNamespace(state=SimpleNamespace(name=lambda: status))
        self.step = step
        self.total_rounds = total_rounds
        self._finish = False
        self.results = [{"request": "offline probe", "result": "not accomplished"}]

    async def run(self):
        pass

    def reset(self):
        pass


async def session_status_probe():
    config = get_ufo_config().system
    for status, step, rounds, expected in [
        ("FAIL", 1, config.max_round, TaskStatus.COMPLETED),
        ("CONTINUE", config.max_step, 0, TaskStatus.COMPLETED),
        ("ERROR", 1, 0, TaskStatus.FAILED),
    ]:
        callback = AsyncMock()
        manager = SimpleNamespace(
            logger=logging.getLogger("probe"),
            _running_tasks={},
            _cancellation_reasons={},
            set_results=lambda _: None,
            remove_session=lambda _: None,
        )
        session = FakeSession(status, step, rounds)
        await SessionManager._run_session_background(manager, "offline", session, callback)
        result = callback.await_args.args[1]
        assert result.status == expected
        show("session_result", agent_status=status, steps=step,
             is_error=session.is_error(), reported_status=result.status)


async def route_results(tool_result):
    calls = []

    async def run_actions(actions):
        calls.append(actions)
        return [tool_result]

    computer = SimpleNamespace(
        command2tool=lambda command: SimpleNamespace(namespace="action"),
        run_actions=run_actions,
    )
    manager = SimpleNamespace(get_or_create=AsyncMock(return_value=computer))
    router = CommandRouter(manager)
    results = await router.execute(
        "LinuxAgent", None, None,
        [Command(tool_name="probe", tool_type="action", parameters={}, call_id=str(i))
         for i in range(2)],
    )
    return results, calls


async def tool_result_probe():
    server = FastMCP("offline-probe")

    @server.tool()
    def business_failure() -> dict:
        return {"success": False, "error": "simulated device failure"}

    # In-memory FastMCP transport, with no network listener or external tools.
    async with Client(server) as client:
        tool_result = await client.call_tool("business_failure", {}, raise_on_error=False)
    results, calls = await route_results(tool_result)
    assert tool_result.is_error is False
    assert results[0].status == ResultStatus.SUCCESS and len(calls) == 2
    show("business_failure", mcp_is_error=tool_result.is_error,
         router_status=results[0].status, result=results[0].result,
         executed_commands=len(calls))

    failed_result = CallToolResult(
        data=None, structured_content=None, is_error=True,
        content=[TextContent(type="text", text="simulated transport failure")],
    )
    results, calls = await route_results(failed_result)
    assert results[0].status == ResultStatus.FAILURE
    assert results[1].status == ResultStatus.SKIPPED and len(calls) == 1
    show("protocol_failure", statuses=[r.status for r in results],
         executed_commands=len(calls))


async def constellation_probe():
    graph = TaskConstellation()
    task = TaskStar(task_id="A", description="offline probe", target_device_id="fake")
    graph.add_task(task)
    child = TaskStar(task_id="B", description="depends on A", target_device_id="fake")
    graph.add_task(child)
    graph.add_dependency(TaskStarLine("A", "B", dependency_type=DependencyType.SUCCESS_ONLY))
    failure = ExecutionResult(task_id="A", status=TaskStatus.FAILED,
                              error="simulated disconnect", result={"reason": "offline"})
    task.execute = AsyncMock(return_value=failure)
    bus = SimpleNamespace(publish_event=AsyncMock())
    orchestrator = TaskConstellationOrchestrator(event_bus=bus)
    await orchestrator._execute_task_with_events(task, graph)
    event = bus.publish_event.await_args.args[0]
    assert task.error is None and task.result is None
    assert event.result.error and event.error is None
    show("failed_task_storage", status=task.status, stored_error=task.error,
         stored_result=task.result, event_error=event.error,
         nested_event_error=event.result.error)

    assert not graph.is_complete() and not graph.get_ready_tasks()
    orchestrator._sync_constellation_modifications = AsyncMock(return_value=graph)
    orchestrator._validate_existing_device_assignments = lambda _: None
    try:
        await asyncio.wait_for(orchestrator._run_execution_loop(graph), timeout=0.25)
    except asyncio.TimeoutError:
        show("blocked_dependency", ready_tasks=0, running_tasks=0,
             complete=graph.is_complete(), stopped_by="external probe timeout")
    else:
        raise AssertionError("Expected execution loop to remain waiting")

    dependency = TaskStarLine("A", "B", dependency_type=DependencyType.SUCCESS_ONLY)
    accepted_error = dependency.evaluate_condition(RuntimeError("upstream failed"))
    assert accepted_error is True
    show("success_only_dependency", error_object_satisfies_dependency=accepted_error)


async def main():
    logging.basicConfig(level=logging.CRITICAL)
    await session_status_probe()
    await tool_result_probe()
    await constellation_probe()
    show("all_probes", result="passed (observed behavior reproduced)")


if __name__ == "__main__":
    asyncio.run(main())
