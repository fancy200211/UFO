"""X11 screenshot and AT-SPI observation, also executable with system Python.

This file deliberately imports no UFO modules: the MCP process can use Conda
while /usr/bin/python3 loads the distribution's pyatspi/GObject bindings.
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import time


def active_window():
    """Read the foreground identity from the X11 window manager."""
    root = subprocess.check_output(
        ["xprop", "-root", "_NET_ACTIVE_WINDOW"], text=True, timeout=5
    )
    match = re.search(r"0x[0-9a-fA-F]+", root)
    if not match or int(match[0], 16) == 0:
        return {}
    window_id = match[0]
    props = subprocess.check_output(
        ["xprop", "-id", window_id, "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME"],
        text=True, timeout=5,
    )
    pid = re.search(r"_NET_WM_PID.*?=\s*(\d+)", props)
    title = re.search(r'(?:_NET_WM_NAME|WM_NAME)\([^)]*\) = "(.*)"', props)
    return {"window_id": window_id, "pid": int(pid[1]) if pid else None,
            "title": title[1] if title else ""}


def png_url(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def capture_observation(app_name=None):
    from PIL import ImageDraw, ImageFont, ImageGrab

    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise RuntimeError("Linux GUI MVP requires an X11 desktop session")
    screenshot = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY")).convert("RGB")
    tree, controls, warnings = [], [], []
    foreground, selected = {}, []
    try:
        foreground = active_window()
        import pyatspi
        from gi.repository import Atspi

        Atspi.set_timeout(1000, 2000)
        desktop = pyatspi.Registry.getDesktop(0)
        candidates = []
        for app in desktop:
            try:
                name, pid = app.name or "", app.get_process_id()
                if app_name and app_name.casefold() not in name.casefold():
                    continue
                for window in app:
                    title = window.name or ""
                    showing = window.getState().contains(pyatspi.STATE_SHOWING)
                    if app_name:
                        if showing or title == foreground.get("title"):
                            candidates.append((app, window, name, pid, title))
                    elif pid == foreground.get("pid") or (title and title == foreground.get("title")):
                        candidates.append((app, window, name, pid, title))
            except Exception as exc:
                warnings.append(str(exc))

        if not app_name:
            groups = [
                # LibreOffice can keep the frame's X11 title while an AT-SPI
                # dialog owns the focus. Prefer that active child window.
                [c for c in candidates if c[3] == foreground.get("pid") and c[1].getState().contains(pyatspi.STATE_ACTIVE)],
                [c for c in candidates if c[3] == foreground.get("pid") and c[4] == foreground.get("title")],
                [c for c in candidates if c[4] and c[4] == foreground.get("title")],
                [c for c in candidates if c[3] == foreground.get("pid") and c[1].getState().contains(pyatspi.STATE_SHOWING)],
            ]
            candidates = next((group for group in groups if len(group) == 1), [])
        if not candidates:
            warnings.append("No matching AT-SPI window; use the screenshot and coordinate actions.")

        interactive = {"push button", "toggle button", "check box", "radio button", "entry",
                       "password text", "combo box", "list item", "tree item", "menu item",
                       "check menu item", "radio menu item", "page tab", "link", "slider",
                       "spin button", "scroll bar", "menu"}
        structural = {"application", "frame", "window", "panel", "section", "filler", "root pane",
                      "scroll pane", "viewport", "tool bar", "document web", "page tab list", "menu",
                      "menu bar", "status bar", "list", "tree", "landmark", "document frame",
                      "embedded", "internal frame", "canvas", "paragraph", "heading", "static", "label", "text"}
        flags = {"visible": pyatspi.STATE_VISIBLE, "showing": pyatspi.STATE_SHOWING,
                 "enabled": pyatspi.STATE_ENABLED, "focused": pyatspi.STATE_FOCUSED,
                 "editable": pyatspi.STATE_EDITABLE}
        started, seen_rects = time.monotonic(), set()
        truncated = False

        def visit(node, parent_id=None, depth=0):
            nonlocal truncated
            # Same bounded traversal as the working capture script; no daemon or retry layer.
            if len(tree) >= 5000 or depth > 40 or time.monotonic() - started > 20:
                truncated = True
                return
            node_id = str(len(tree) + 1)
            record = {"id": node_id, "parent_id": parent_id, "name": "", "role": "",
                      "bounds": None, "states": []}
            tree.append(record)
            try:
                state = node.getState()
                record.update(name=node.name or "", role=node.getRoleName(),
                              states=[name for name, flag in flags.items() if state.contains(flag)])
                # Closed menus/offscreen subtrees are not part of the current view.
                # Keep their node identity, but still traverse a hidden root frame:
                # Chromium can expose a usable frame with SHOWING=False.
                if depth and "showing" not in record["states"]:
                    return
                interfaces = pyatspi.listInterfaces(node)
                if "Component" in interfaces:
                    rect = node.queryComponent().getExtents(pyatspi.XY_SCREEN)
                    record["bounds"] = [rect.x, rect.y, rect.x + rect.width, rect.y + rect.height]
                actions = []
                if "Action" in interfaces:
                    action = node.queryAction()
                    actions = [action.getName(i).lower() for i in range(action.nActions)]
                actionable = (record["role"] in interactive or "editable" in record["states"]
                              or (record["role"] not in structural and any(
                                  a not in {"clickancestor", "showcontextmenu"} for a in actions)))
                if record["bounds"] and actionable and {"visible", "showing"}.issubset(record["states"]):
                    x1, y1, x2, y2 = record["bounds"]
                    bounds = (max(0, x1), max(0, y1), min(screenshot.width, x2), min(screenshot.height, y2))
                    if bounds[2] > bounds[0] and bounds[3] > bounds[1] and bounds not in seen_rects:
                        seen_rects.add(bounds)
                        controls.append({**record, "control_id": str(len(controls) + 1), "bounds": list(bounds)})
                if "Table" in interfaces and node.childCount > 5000:
                    # Calc exposes its entire virtual sheet, including millions of
                    # offscreen cells. Keep the selected cells and use the screenshot
                    # for the remaining visible grid instead of walking empty rows.
                    record["children_omitted"] = "virtual table; selected cells only"
                    if "Selection" in interfaces:
                        selection = node.querySelection()
                        for index in range(min(selection.nSelectedChildren, 100)):
                            visit(selection.getSelectedChild(index), node_id, depth + 1)
                    return
                for index in range(node.childCount):
                    if truncated:
                        break
                    try:
                        child = node.getChildAtIndex(index)
                        if child is not None:
                            visit(child, node_id, depth + 1)
                    except Exception:
                        continue  # Applications may remove nodes while being observed.
            except Exception as exc:
                record["error"] = str(exc)

        for _, window, name, pid, title in candidates:
            selected.append({"application": name, "pid": pid, "title": title})
            visit(window)
        if truncated:
            warnings.append("AT-SPI traversal truncated; screenshot remains complete.")
    except Exception as exc:
        warnings.append(f"AT-SPI unavailable: {exc}")
    if not controls:
        warnings.append("No visible interactive AT-SPI controls; use screenshot coordinates.")

    annotated = screenshot.copy()
    draw = ImageDraw.Draw(annotated)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    for control in controls:
        x1, y1, x2, y2 = control["bounds"]
        draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline="red", width=2)
        label = control["control_id"]
        box = draw.textbbox((0, 0), label, font=font)
        width, height = box[2] - box[0] + 6, box[3] - box[1] + 6
        x, y = min(x1, screenshot.width - width), min(y1, screenshot.height - height)
        draw.rectangle((x, y, x + width, y + height), fill="yellow", outline="black")
        draw.text((x + 3 - box[0], y + 3 - box[1]), label, font=font, fill="black")
    return {"screenshot_url": png_url(screenshot), "annotated_screenshot_url": png_url(annotated),
            "ui_tree": tree, "controls": controls, "active_window": foreground,
            "selected_windows": selected, "screen_size": list(screenshot.size), "warnings": warnings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app")
    args = parser.parse_args()
    try:
        print(json.dumps(capture_observation(args.app), ensure_ascii=False))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
