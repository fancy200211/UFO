"""Linux X11 desktop tools. Run in the logged-in desktop's UFO environment."""

import argparse
import os
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from starlette.middleware import Middleware

from ufo.automator.linux.desktop import LinuxDesktop
from ufo.client.mcp.http_servers.linux_mcp_server import LocalhostGuardMiddleware, _validate_api_key


def create_linux_gui_mcp_server(host="localhost", port=8011, desktop=None):
    """Build the service without starting it, for in-process MCP tests too."""
    backend = desktop if desktop is not None else LinuxDesktop()
    mcp = FastMCP("Linux GUI", host=host, port=port, json_response=True)

    def authenticate(api_key):
        if not _validate_api_key(api_key):
            raise ValueError("Authentication failed. Invalid or missing API key.")

    @mcp.tool()
    def get_desktop_observation(api_key: str, app_name: Optional[str] = None) -> Dict[str, Any]:
        """Capture PNG data URLs, numbered controls and the AT-SPI tree. Defaults to the foreground window; control IDs are valid until the next observation."""
        authenticate(api_key)
        return backend.get_desktop_observation(app_name)

    @mcp.tool()
    def list_windows(api_key: str) -> Dict[str, Any]:
        """List X11 window IDs, titles, PIDs and screen bounds for switching applications."""
        authenticate(api_key)
        return {"windows": backend.list_windows()}

    @mcp.tool()
    def activate_window(window_id: str, api_key: str) -> Dict[str, Any]:
        """Bring an X11 window from list_windows to the foreground. Observe again before using control IDs."""
        authenticate(api_key)
        return backend.activate_window(window_id)

    @mcp.tool()
    def click(x: int, y: int, api_key: str, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """Click at original screenshot pixel coordinates. button: left/right/middle; clicks=2 for double-click."""
        authenticate(api_key)
        return backend.click(x, y, button, clicks)

    @mcp.tool()
    def click_control(control_id: str, api_key: str, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """Click the center of a numbered control from the most recent observation."""
        authenticate(api_key)
        return backend.click_control(control_id, button, clicks)

    @mcp.tool()
    def scroll(x: int, y: int, amount: int, api_key: str) -> Dict[str, Any]:
        """Scroll at screen coordinates; positive amount scrolls up, negative down."""
        authenticate(api_key)
        return backend.scroll(x, y, amount)

    @mcp.tool()
    def type_text(text: str, api_key: str) -> Dict[str, Any]:
        """Paste Unicode or multiline text into the focused application using the clipboard and Ctrl+V."""
        authenticate(api_key)
        return backend.type_text(text)

    @mcp.tool()
    def press_keys(keys: List[str], api_key: str) -> Dict[str, Any]:
        """Press a single key or simultaneous key combination, e.g. ['enter'], ['ctrl','s'], ['ctrl','shift','end']."""
        authenticate(api_key)
        return backend.press_keys(keys)

    @mcp.tool()
    def drag(start_x: int, start_y: int, end_x: int, end_y: int, api_key: str, duration: float = 0.5) -> Dict[str, Any]:
        """Drag with the left mouse button between two original screenshot coordinates."""
        authenticate(api_key)
        return backend.drag(start_x, start_y, end_x, end_y, duration)

    return mcp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    if not os.environ.get("UFO_MCP_API_KEY"):
        parser.error("Set UFO_MCP_API_KEY before starting the GUI service")
    create_linux_gui_mcp_server(args.host, args.port).run(
        transport="streamable-http", middleware=[Middleware(LocalhostGuardMiddleware)]
    )


if __name__ == "__main__":
    main()
