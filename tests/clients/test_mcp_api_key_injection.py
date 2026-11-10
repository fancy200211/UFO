"""Exercise client-side credentials against the real Linux MCP tool schema.

Uses in-memory MCP transport and a fake command executor except for one
read-only whoami smoke test. No model calls, network listeners, or access
to the user's configured credentials.
"""

import copy
import os
import pwd
import unittest
from unittest.mock import AsyncMock, patch

from fastmcp import Client, FastMCP

from ufo.client.computer import Computer
from ufo.client.mcp.http_servers import linux_mcp_server
from ufo.client.mcp.mcp_server_manager import HTTPMCPServer, MCPServerManager


TEST_KEY = "test-only-mcp-credential"
ENV_NAME = "UFO_MCP_API_KEY"


class MCPAPIKeyInjectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def new_linux_server():
        servers = []
        with patch.object(
            FastMCP, "run", lambda server, **kwargs: servers.append(server)
        ):
            linux_mcp_server.create_bash_mcp_server()
        return servers[0]

    async def asyncSetUp(self):
        self.env_patch = patch.dict(os.environ, {ENV_NAME: TEST_KEY})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        # Capture the production server without starting an HTTP listener.
        self.mcp = self.new_linux_server()
        self.server = HTTPMCPServer(
            {"namespace": "BashExecutor", "api_key_env": ENV_NAME}
        )
        self.server._server = self.mcp
        self.computer = Computer("test", "", MCPServerManager(), [], [])
        self.addCleanup(self.computer._executor.shutdown, wait=True)
        self.execute = AsyncMock(
            return_value={"success": True, "exit_code": 0, "stdout": "test-user\n"}
        )
        self.exec_patch = patch.object(
            linux_mcp_server, "_execute_allowed_command", self.execute
        )
        self.exec_patch.start()
        self.addCleanup(self.exec_patch.stop)
        await self.computer.register_one_mcp_server(
            "BashExecutor", "action", self.server
        )

    def tool(self, name="execute_command", params=None):
        tool = self.computer._tools_registry[Computer.make_tool_key("action", name)]
        if params is not None:
            tool.parameters = params
        return tool

    async def test_model_schema_hides_key_but_server_still_requires_it(self):
        for name in ("execute_command", "get_system_info"):
            schema = self.tool(name).tool_info.input_schema
            self.assertNotIn("api_key", schema["properties"])
            self.assertNotIn("api_key", schema.get("required", []))
        self.assertIn("command", self.tool().input_schema["required"])

        async with Client(self.mcp) as client:
            for tool in await client.list_tools():
                self.assertIn("api_key", tool.inputSchema["properties"])
                self.assertIn("api_key", tool.inputSchema["required"])
            missing = await client.call_tool(
                "execute_command", {"command": "whoami"}, raise_on_error=False
            )
            self.assertTrue(missing.is_error)
            wrong = await client.call_tool(
                "execute_command", {"command": "whoami", "api_key": "wrong"}
            )
            self.assertFalse(wrong.data["success"])
        self.execute.assert_not_awaited()

    async def test_injects_key_without_changing_action_or_logging_secret(self):
        for supplied_key in (None, "key1", TEST_KEY):
            with self.subTest(supplied_key_present=supplied_key is not None):
                params = {"command": "whoami", "timeout": 15}
                if supplied_key is not None:
                    params["api_key"] = supplied_key
                original = copy.deepcopy(params)
                tool = self.tool(params=params)
                with self.assertLogs("Computer", level="DEBUG") as captured:
                    result = await self.computer._run_action(tool)
                self.assertFalse(result.is_error)
                self.assertTrue(result.data["success"])
                self.assertEqual(tool.parameters, original)
                self.assertNotIn(TEST_KEY, "\n".join(captured.output))
                self.assertNotIn(TEST_KEY, str(result))
        self.assertEqual(self.execute.await_count, 3)
        self.assertEqual(self.execute.call_args.args[0], "whoami")

    async def test_system_info_with_no_model_parameters(self):
        expected = {"uname": "test Linux", "memory": "test memory"}
        with patch.object(
            linux_mcp_server, "_collect_system_info", new_callable=AsyncMock,
            return_value=expected,
        ) as collect:
            result = await self.computer._run_action(self.tool("get_system_info"))
        self.assertFalse(result.is_error)
        self.assertEqual(result.data, expected)
        collect.assert_awaited_once()
        self.assertIsNone(self.tool("get_system_info").parameters)

    async def test_real_whoami_round_trip(self):
        self.exec_patch.stop()
        result = await self.computer._run_action(
            self.tool(params={"command": "whoami", "timeout": 5})
        )
        self.assertFalse(result.is_error)
        self.assertTrue(result.data["success"])
        self.assertEqual(result.data["exit_code"], 0)
        self.assertEqual(
            result.data["stdout"].strip(), pwd.getpwuid(os.geteuid()).pw_name
        )

    async def test_missing_or_empty_key_fails_before_transport(self):
        for value in (None, ""):
            with self.subTest(value=value), patch.dict(os.environ):
                if value is None:
                    os.environ.pop(ENV_NAME, None)
                else:
                    os.environ[ENV_NAME] = value
                # A model-provided credential must not bypass local configuration.
                tool = self.tool(params={"command": "whoami", "api_key": "key1"})
                with patch("ufo.client.computer.Client") as client:
                    result = await self.computer._run_action(tool)
                client.assert_not_called()
                self.assertTrue(result.is_error)
                self.assertIn(ENV_NAME, result.content[0].text)
                self.assertIn("device client", result.content[0].text)
        self.execute.assert_not_awaited()

    async def test_transport_exception_does_not_expose_injected_key(self):
        with patch(
            "ufo.client.computer.Client",
            side_effect=RuntimeError(f"transport rejected credential {TEST_KEY}"),
        ), self.assertLogs("Computer", level="ERROR") as captured:
            result = await self.computer._run_action(
                self.tool(params={"command": "whoami"})
            )
        self.assertTrue(result.is_error)
        self.assertNotIn(TEST_KEY, str(result))
        self.assertNotIn(TEST_KEY, "\n".join(captured.output))
        self.assertIn("[REDACTED]", result.content[0].text)

    async def test_unconfigured_server_keeps_its_schema_and_arguments(self):
        self.server.config.pop("api_key_env")
        self.computer._tools_registry.clear()
        await self.computer.register_one_mcp_server(
            "BashExecutor", "action", self.server
        )
        tool = self.tool(params={"command": "whoami", "api_key": "wrong"})
        self.assertIn("api_key", tool.input_schema["required"])
        result = await self.computer._run_action(tool)
        self.assertFalse(result.data["success"])
        self.execute.assert_not_awaited()
        tool.parameters["api_key"] = TEST_KEY
        result = await self.computer._run_action(tool)
        self.assertTrue(result.data["success"])

    async def test_unconfigured_tool_without_key_gets_no_extra_argument(self):
        other_mcp = FastMCP("Other MCP")

        @other_mcp.tool()
        def echo(text: str) -> str:
            """Return the supplied text."""
            return text

        other = HTTPMCPServer({"namespace": "OtherExecutor"})
        other._server = other_mcp
        await self.computer.register_one_mcp_server("OtherExecutor", "action", other)
        result = await self.computer._run_action(self.tool("echo", {"text": "hello"}))
        self.assertFalse(result.is_error)
        self.assertEqual(result.data, "hello")


if __name__ == "__main__":
    unittest.main()
