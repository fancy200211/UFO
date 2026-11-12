# LinuxAgent CLI + X11 desktop setup

LinuxAgent now uses Bash and GUI tools together. The GUI service provides screenshots,
AT-SPI controls, window switching, mouse input and Unicode clipboard input. Configure
the AppAgent model in `config/ufo/agents.yaml` and the Galaxy model in
`config/galaxy/agent.yaml`. A model that accepts image inputs is required for GUI tasks.
This MVP targets one X11 screen at the current desktop scale; start all device tools
in the logged-in desktop session.

## Dependencies and Python environments

UFO and the GUI MCP run with the existing UFO Python environment. On this machine:
`/home/zhou/miniconda3/envs/ufo3/bin/python` (Python 3.10).
The observation helper runs as a standalone file with `/usr/bin/python3` (Python 3.12),
which can import the distribution's AT-SPI/GObject bindings. JSON crosses this process
boundary; do not add Python 3.12 system packages to the Conda Python 3.10 path.

```bash
sudo apt install python3-pyatspi python3-gi python3-pil gir1.2-gtk-3.0 x11-utils wmctrl xclip zip
conda activate ufo3
python -m pip install 'pyautogui==0.9.54' 'pyperclip>=1.9,<2'
# Optional override if the system bindings use a different interpreter:
export UFO_LINUX_OBSERVATION_PYTHON=/usr/bin/python3
```

Verify each interpreter without mixing its package paths:

```bash
/usr/bin/python3 -c 'import pyatspi, gi; from PIL import ImageGrab; print("capture imports OK")'
python -c 'import fastmcp, pyautogui, pyperclip; print("MCP/action imports OK")'
```

Applications must expose accessibility information for controls to appear. If a window
has no AT-SPI tree, the observation still contains its screenshot and a warning; use
coordinate actions. Screenshots and trees are collected sequentially. Control numbers
are refreshed with each observation. GUI tools report their own failures through MCP.
Closed/offscreen subtrees are not expanded. Large virtual tables (e.g. Calc's entire
sheet) expose selected cells rather than millions of empty cells; the screenshot
provides the remaining visible grid. Chromium applications may need to be started with
`--force-renderer-accessibility` to expose their full tree; coordinate tools also work
with the default application launch configuration.
`type_text` uses Ctrl+V and replaces the clipboard contents; focus the intended field first.

## Start the four services manually

From the UFO3 directory, use separate terminals with the same desktop session and
`UFO_MCP_API_KEY` value. Keep these terminals running while evaluating. Retain the
existing server authentication setup; the device client URL must include its server token.

```bash
# Terminal 1: Bash service, including cp/mkdir/chmod/mv/zip
export UFO_MCP_API_KEY='<shared-mcp-key>'
python -m ufo.client.mcp.http_servers.linux_mcp_server --host localhost --port 8010

# Terminal 2: desktop service (same MCP key)
export UFO_MCP_API_KEY='<shared-mcp-key>'
python -m ufo.client.mcp.http_servers.linux_gui_mcp_server --host localhost --port 8011

# Terminal 3: device server; select a free port and a server token
python -m ufo.server.app --host 127.0.0.1 --port 5002 --platform linux --api-key '<server-token>'

# Terminal 4: Linux device client (same MCP key)
export UFO_MCP_API_KEY='<shared-mcp-key>'
python -m ufo.client.client --ws --ws-server 'ws://127.0.0.1:5002/ws?token=<server-token>' --client-id linux_device_1 --platform linux
```

Add or retain the corresponding Linux entry in `config/galaxy/devices.yaml`, keeping any
existing devices. Its ID and authenticated URL must match the running server/client:

```yaml
devices:
  - device_id: linux_device_1
    server_url: 'ws://127.0.0.1:5002/ws?token=<server-token>'
    os: linux
    capabilities: [gui, cli, linux_apps]
```

`config/ufo/mcp.yaml` registers LinuxGUI at port 8011 for collection and actions, plus
BashExecutor at port 8010. The client injects MCP credentials, not the model.
No CLI/GUI mode switch is needed. A Bash-only installation can remove LinuxGUI from
these two lists; failed/unavailable observation is reported in the model context.

## AgentArena integration

AgentArena opens the case applications and owns the test materials. It submits the
natural request through Galaxy and independently checks the saved result. Start the
services above first, then from the sibling AgentArena directory run:

```bash
# Point to a user-local LibreOffice installation if not on PATH:
export ARENA_LIBREOFFICE=/home/zhou/.local/share/agentarena/libreoffice-24.2/opt/libreoffice24.2/program/soffice
python src/linux-arena-container/run.py --case replace_text --device linux_device_1 --timeout 1800
python src/linux-arena-container/batch.py --device linux_device_1 --timeout 1800
```

Do not use `--local-services` for this integration: that existing Arena option starts
only Bash MCP. The default suite is fifteen cases (7 CLI, 8 GUI). `--timeout` is a
per-case worker deadline and does not change UFO's `MAX_STEP` (currently 50).
Screenshots and `action_stepN_ui.json` are saved in UFO's existing session log directory.
Observation failure, tool failure and task failure are reported as such; application
startup and successful MCP transport alone do not establish that a case passed.

## Verify the connection and inspect results

```bash
curl -H 'X-API-Key: <server-token>' http://127.0.0.1:5002/api/health
```

The health response should list `linux_device_1` in `online_clients`.
If collection fails, check `DISPLAY`, `XDG_SESSION_TYPE`, the session D-Bus environment,
and whether the system interpreter can import pyatspi/gi/Pillow. If Unicode input
fails, check that `xclip` is on the GUI MCP process's PATH.

For a user-local installation of `wmctrl` or `xclip`, add its executable directory to
PATH before starting the GUI service, for example `export PATH="$HOME/.local/bin:$PATH"`.
The helper prefers the application's active AT-SPI dialog when X11 still reports its
parent frame, so modal dialogs receive their own control numbers.

See [implementation steps](../linux/visual_development_plan.md) and
[actual validation results](../linux/tool_validation.md). Further service details:
[server](../server/quick_start.md), [client](../client/quick_start.md),
[BashExecutor](../mcp/servers/bash_executor.md).
