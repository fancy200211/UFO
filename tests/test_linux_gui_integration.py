"""Linux observation -> MCP -> prompt and action/result regressions."""

import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client
from PIL import Image
import pytest

from aip.messages import Result, ResultStatus
from ufo.automator.linux.desktop import LinuxDesktop
from ufo.client.mcp.http_servers.linux_gui_mcp_server import create_linux_gui_mcp_server
from ufo.agents.processors.strategies.linux_agent_strategy import LinuxDesktopCollectionStrategy, LinuxActionExecutionStrategy, LinuxLoggingMiddleware
from ufo.agents.states.linux_agent_state import LinuxAgentStateManager
from ufo.module.context import ContextNames
from ufo.agents.processors.schemas.actions import ActionCommandInfo
from ufo.prompter.customized.linux_agent_prompter import LinuxAgentPrompter
from ufo.module.sessions.linux_session import LinuxSession, ufo_config


def observation():
    image = Image.new("RGB", (32, 20), "white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    return {"screenshot_url": url, "annotated_screenshot_url": url,
            "ui_tree": [{"id": "1", "parent_id": None, "name": "保存", "role": "push button"}],
            "controls": [{"control_id": "1", "bounds": [10, 2, 20, 12]}],
            "active_window": {"title": "test editor"}, "warnings": []}


@pytest.mark.asyncio
async def test_mcp_observation_and_error_roundtrip(monkeypatch):
    monkeypatch.setenv("UFO_MCP_API_KEY", "test-key")
    backend = LinuxDesktop()
    monkeypatch.setattr(backend, "get_desktop_observation", lambda app_name=None: observation())
    windows = [{"window_id": "0x123", "title": "Test editor", "bounds": [0, 0, 100, 100]}]
    monkeypatch.setattr(backend, "list_windows", lambda: windows)
    server = create_linux_gui_mcp_server(desktop=backend)
    async with Client(server) as client:
        assert len(await client.list_tools()) == 9
        result = await client.call_tool("get_desktop_observation", {"api_key": "test-key"})
        assert result.data == observation()
        window_result = await client.call_tool("list_windows", {"api_key": "test-key"})
        assert window_result.data == {"windows": windows}
        error = await client.call_tool("click_control", {"control_id": "missing", "api_key": "test-key"}, raise_on_error=False)
        assert error.is_error
        denied = await client.call_tool("get_desktop_observation", {"api_key": "wrong"}, raise_on_error=False)
        assert denied.is_error


@pytest.mark.asyncio
async def test_collection_to_multimodal_prompt(tmp_path):
    payload = observation()
    dispatcher = SimpleNamespace(execute_commands=AsyncMock(return_value=[Result(status=ResultStatus.SUCCESS, result=payload)]))
    values = {"log_path": str(tmp_path), "session_step": 3}
    context = SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                              global_context=SimpleNamespace(command_dispatcher=dispatcher))
    result = await LinuxDesktopCollectionStrategy().execute(None, context)
    assert result.success
    assert (tmp_path / "action_step3.png").is_file()
    saved = json.loads((tmp_path / "action_step3_ui.json").read_text())
    assert saved["ui_tree"] == payload["ui_tree"]
    assert "screenshot_url" not in saved
    prompter = LinuxAgentPrompter("ufo/prompts/third_party/linux_agent.yaml", "ufo/prompts/third_party/linux_agent_example.yaml")
    content = prompter.user_content_construction([], "Save the document", screenshot_url=result.data["clean_screenshot_url"],
        annotated_screenshot_url=result.data["annotated_screenshot_url"], desktop_observation=result.data["desktop_observation"])
    assert [x["image_url"]["url"] for x in content if x["type"] == "image_url"] == [payload["screenshot_url"]] * 2
    assert any("保存" in x.get("text", "") for x in content)


@pytest.mark.asyncio
async def test_collection_failure_discards_old_observation(tmp_path):
    dispatcher = SimpleNamespace(execute_commands=AsyncMock(return_value=[Result(status=ResultStatus.FAILURE, error="No display")]))
    context = SimpleNamespace(get=lambda key, default=None: default,
                              global_context=SimpleNamespace(command_dispatcher=dispatcher))
    result = await LinuxDesktopCollectionStrategy().execute(None, context)
    assert result.success  # A CLI task can continue with the reported observation error.
    assert result.data["clean_screenshot_url"] is None
    assert result.data["desktop_observation"]["controls"] == []
    assert "No display" in result.data["desktop_observation"]["warnings"]


def test_numbered_click_uses_current_bounds(monkeypatch):
    desktop = LinuxDesktop()
    desktop.controls = {"7": {"bounds": [20, 30, 60, 80]}}
    monkeypatch.setattr(desktop, "click", lambda *args: args)
    assert desktop.click_control("7", clicks=2) == (40, 55, "left", 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_failed_action_cannot_finish(failure):
    action = ActionCommandInfo(function="execute_command", arguments={"command": "cp a b"}, status="FINISH")
    payload = {"success": not failure, "exit_code": 1 if failure else 0, "stderr": "missing source" if failure else ""}
    result = Result(status=ResultStatus.SUCCESS, result=payload)
    strategy = LinuxActionExecutionStrategy()
    strategy._execute_app_action = AsyncMock(return_value=[result])
    context = SimpleNamespace(get_local=lambda key: SimpleNamespace(action=action),
                              global_context=SimpleNamespace(command_dispatcher=None))
    output = await strategy.execute(None, context)
    assert output.data["status"] == ("CONTINUE" if failure else "FINISH")
    if failure:
        assert action.result.error == "missing source"
        assert action.result.result["exit_code"] == 1


def test_empty_action_does_not_consume_result():
    actions = [ActionCommandInfo(function="", status="CONTINUE"),
               ActionCommandInfo(function="click", status="CONTINUE"),
               ActionCommandInfo(function="", status="FINISH")]
    actual = Result(status=ResultStatus.FAILURE, error="click failed")
    output = LinuxActionExecutionStrategy()._create_action_info(actions, [actual])
    assert output[0].result.status == ResultStatus.SUCCESS
    assert output[1].result == actual
    assert output[2].result.status == ResultStatus.SUCCESS
    assert output[2].function == ""


@pytest.mark.asyncio
async def test_processor_error_ends_linux_session_as_failure():
    local, shared = {}, {}
    context = SimpleNamespace(set_local=local.__setitem__,
                              global_context=SimpleNamespace(set=shared.__setitem__))
    await LinuxLoggingMiddleware().on_error(SimpleNamespace(processing_context=context),
                                           ValueError("Invalid model JSON"))
    state = LinuxAgentStateManager().get_state(local["status"])
    assert state.name() == "FAIL"
    assert shared[ContextNames.ROUND_RESULT] == "Invalid model JSON"
    session = SimpleNamespace(current_round=SimpleNamespace(state=state), step=1, _finish=False)
    assert LinuxSession.is_error(session)


@pytest.mark.parametrize("state,step,expected", [("FINISH", 1, False), ("FAIL", 1, True),
    ("ERROR", 1, True), ("CONTINUE", 0, False), ("CONTINUE", 100000, True), ("FINISH", 100000, False)])
def test_linux_terminal_status(state, step, expected):
    session = SimpleNamespace(current_round=SimpleNamespace(state=SimpleNamespace(name=lambda: state)),
                              step=step, _finish=False)
    assert LinuxSession.is_error(session) is expected
