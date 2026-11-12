"""Small X11 desktop backend for the Linux GUI MCP service."""

import json
import os
from pathlib import Path
import subprocess


class LinuxDesktop:
    def __init__(self, observation_python=None):
        self.observation_python = observation_python or os.environ.get("UFO_LINUX_OBSERVATION_PYTHON", "/usr/bin/python3")
        self.controls = {}

    def get_desktop_observation(self, app_name=None):
        self.controls = {}
        command = [self.observation_python, str(Path(__file__).with_name("observation.py"))]
        if app_name:
            command.extend(["--app", app_name])
        result = subprocess.run(command, text=True, capture_output=True, timeout=45)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Desktop observation failed")
        observation = json.loads(result.stdout)
        self.controls = {c["control_id"]: c for c in observation["controls"]}
        return observation

    def list_windows(self):
        output = subprocess.check_output(["wmctrl", "-lpG"], text=True, timeout=5)
        windows = []
        for line in output.splitlines():
            parts = line.split(None, 8)
            if len(parts) < 8:
                continue
            wid, desktop, pid, x, y, width, height, host = parts[:8]
            windows.append({"window_id": wid, "title": parts[8] if len(parts) > 8 else "",
                            "pid": int(pid), "bounds": [int(x), int(y), int(x) + int(width), int(y) + int(height)]})
        return windows

    def activate_window(self, window_id):
        subprocess.run(["wmctrl", "-ia", window_id], check=True, capture_output=True, text=True, timeout=5)
        self.controls = {}
        return {"success": True, "window_id": window_id}

    def click(self, x, y, button="left", clicks=1):
        import pyautogui
        pyautogui.click(x, y, clicks=clicks, interval=0.1, button=button)
        return {"success": True, "x": x, "y": y}

    def click_control(self, control_id, button="left", clicks=1):
        control = self.controls.get(str(control_id))
        if control is None:
            raise ValueError(f"Unknown control {control_id}; collect a fresh desktop observation")
        x1, y1, x2, y2 = control["bounds"]
        return self.click((x1 + x2) // 2, (y1 + y2) // 2, button, clicks)

    def scroll(self, x, y, amount):
        import pyautogui
        pyautogui.moveTo(x, y)
        pyautogui.scroll(amount)
        return {"success": True}

    def type_text(self, text):
        import pyautogui
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        return {"success": True}

    def press_keys(self, keys):
        import pyautogui
        if not keys or any(key not in pyautogui.KEYBOARD_KEYS for key in keys):
            raise ValueError("Provide PyAutoGUI key names, e.g. ['enter'] or ['ctrl', 's']")
        pyautogui.hotkey(*keys)
        return {"success": True}

    def drag(self, start_x, start_y, end_x, end_y, duration=0.5):
        import pyautogui
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=duration, button="left")
        return {"success": True}
