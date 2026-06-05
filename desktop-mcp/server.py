#!/usr/bin/env python3
"""
Ubuntu Desktop MCP Server
AT-SPI2 based — no screenshots, no vision models.
All UI traversal is text/tree based → minimal token usage.
"""

import colorsys
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Optional

# Set env early before pyatspi initializes D-Bus
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":0.0"
if "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"

import pyatspi
from mcp.server.fastmcp import FastMCP

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_OK = True
except ImportError:
    _TRAY_OK = False

mcp = FastMCP(
    "gui-desk-control",
    instructions="Control Ubuntu desktop apps via AT-SPI2 accessibility tree — no vision required",
)

# ── AT-SPI state constants we care about ──────────────────────────────
_STATES = {
    pyatspi.STATE_ENABLED:   "enabled",
    pyatspi.STATE_FOCUSED:   "focused",
    pyatspi.STATE_VISIBLE:   "visible",
    pyatspi.STATE_CHECKED:   "checked",
    pyatspi.STATE_SELECTED:  "selected",
    pyatspi.STATE_EDITABLE:  "editable",
    pyatspi.STATE_EXPANDED:  "expanded",
    pyatspi.STATE_ACTIVE:    "active",
    pyatspi.STATE_PRESSED:   "pressed",
}

# Roles we collapse (pass-through to children) when unnamed
_TRANSPARENT = frozenset([
    "panel", "filler", "scroll pane", "layered pane",
    "root pane", "glass pane", "split pane",
])

# Roles we always skip
_SKIP = frozenset(["unknown", "invalid", "redundant object"])


# ── Tray icon ─────────────────────────────────────────────────────────

_TRAY_FONTS = [
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _name_to_color(name: str) -> tuple:
    """Deterministic unique color from server name via HSV."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    hue = (h % 360) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.70, 0.88)
    return (int(r * 255), int(g * 255), int(b * 255))


def _name_to_label(name: str) -> str:
    """'gui-desk-control' → 'G', 'yougile-vg' → 'VG', 'playwright' → 'P'."""
    parts = [p for p in name.split("-") if p]
    suffix = parts[-1].upper()
    # If suffix looks like an abbreviation (≤3 chars) and there's a prefix, use it
    if len(parts) > 1 and len(suffix) <= 3:
        return suffix[:2]
    return parts[0][0].upper()


class _TrayIcon:
    def __init__(self, name: str):
        self._name = name
        self._label = _name_to_label(name)
        self._color = _name_to_color(name)
        self._font = self._load_font(len(self._label))
        self._icon = None
        self.status = "idle"
        self.action = ""

    @staticmethod
    def _load_font(nchars: int):
        if not _TRAY_OK:
            return None
        from PIL import ImageFont
        size = 26 if nchars == 1 else 20
        for path in _TRAY_FONTS:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _img(self, busy: bool) -> "Image.Image":
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        color = tuple(int(c * 0.65) for c in self._color) if busy else self._color
        d.ellipse([4, 4, 60, 60], fill=color)

        # Centered label
        if self._font:
            from PIL import ImageFont
            bb = d.textbbox((0, 0), self._label, font=self._font)
            tx = (64 - (bb[2] - bb[0])) / 2 - bb[0]
            ty = (64 - (bb[3] - bb[1])) / 2 - bb[1]
            d.text((tx, ty), self._label, fill="white", font=self._font)

        # Orange dot = busy
        if busy:
            d.ellipse([43, 43, 60, 60], fill="#f97316", outline="white", width=2)

        return img

    def start(self):
        def _on_stop(icon, _item):
            icon.stop()
            os._exit(0)

        def _run():
            self._icon = pystray.Icon(
                self._name,
                self._img(False),
                self._name,
                menu=pystray.Menu(
                    pystray.MenuItem(self._name, None, enabled=False),
                    pystray.MenuItem(
                        lambda _: ("⚙ " if self.status == "busy" else "● ")
                                  + self.status
                                  + (f" · {self.action}" if self.action else ""),
                        None,
                        enabled=False,
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Sound notifications",
                        _toggle_sound,
                        checked=lambda _: _sound_enabled,
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Stop server", _on_stop),
                ),
            )
            self._icon.run()
        threading.Thread(target=_run, daemon=True).start()

    def set_busy(self, action: str):
        self.status = "busy"
        self.action = action
        if self._icon:
            self._icon.icon = self._img(True)
            self._icon.title = f"{self._name} — {action}"

    def set_idle(self):
        self.status = "idle"
        self.action = ""
        if self._icon:
            self._icon.icon = self._img(False)
            self._icon.title = self._name


_sound_enabled = True


def _toggle_sound(_icon, _item):
    global _sound_enabled
    _sound_enabled = not _sound_enabled


_tray: Optional[_TrayIcon] = _TrayIcon(mcp.name) if _TRAY_OK else None


@contextmanager
def _busy(action: str):
    if _tray:
        _tray.set_busy(action)
    try:
        yield
    finally:
        if _tray:
            _tray.set_idle()


# ── Internal helpers ──────────────────────────────────────────────────

def _desktop():
    return pyatspi.Registry.getDesktop(0)


def _find_app(name: str):
    desk = _desktop()
    # Exact match first, then partial
    for acc in desk:
        if acc and acc.name and acc.name.lower() == name.lower():
            return acc
    for acc in desk:
        if acc and acc.name and name.lower() in acc.name.lower():
            return acc
    return None


def _get_window(app_name: str, window_idx: int = 0):
    app = _find_app(app_name)
    if not app:
        return None, f"app '{app_name}' not found — run windows_list to see open apps"
    if app.childCount <= window_idx:
        return None, f"window_idx {window_idx} out of range ({app.childCount} windows)"
    return app.getChildAtIndex(window_idx), None


def _nav(root, path: str):
    """Walk '0/2/1' path from root. Returns element or None."""
    if not path or not path.strip("/"):
        return root
    try:
        parts = [int(p) for p in path.strip("/").split("/")]
    except ValueError:
        return None
    cur = root
    for idx in parts:
        if cur is None or idx >= cur.childCount:
            return None
        cur = cur.getChildAtIndex(idx)
    return cur


def _states(acc) -> list[str]:
    ss = acc.getState()
    return [label for code, label in _STATES.items() if ss.contains(code)]


def _value(acc) -> Optional[str | float]:
    role = acc.getRoleName()
    sts = _states(acc)
    if "editable" in sts or role in ("text", "entry", "password text"):
        try:
            t = acc.queryText()
            v = t.getText(0, -1)
            return v[:300] if v else None
        except Exception:
            pass
    if role in ("spin button", "slider", "progress bar", "scroll bar"):
        try:
            v = acc.queryValue()
            return v.currentValue
        except Exception:
            pass
    if role == "combo box":
        try:
            return acc.queryText().getText(0, -1)[:100]
        except Exception:
            pass
    return None


def _ser(acc, depth: int, cur: int, path: str, visible_only: bool, role_filter: str) -> Optional[dict]:
    """Serialize one AT-SPI node recursively."""
    if acc is None:
        return None
    try:
        role = acc.getRoleName()
    except Exception:
        return None
    if role in _SKIP:
        return None

    sts = _states(acc)
    if visible_only and "visible" not in sts and role not in ("application", "frame", "window", "dialog"):
        return None

    name = acc.name or ""

    # Transparent unnamed containers — just pass children through
    if not name and role in _TRANSPARENT:
        if cur >= depth:
            return None
        kids = _collect_children(acc, depth, cur, path, visible_only, role_filter)
        return {"role": role, "path": path, "children": kids} if kids else None

    # Role filter — still recurse into mismatched nodes
    if role_filter and role != role_filter:
        if cur >= depth:
            return None
        kids = _collect_children(acc, depth, cur, path, visible_only, role_filter)
        return {"_pass": True, "path": path, "children": kids} if kids else None

    node: dict = {"role": role, "name": name, "path": path}
    if sts:
        node["states"] = sts
    val = _value(acc)
    if val is not None:
        node["value"] = val

    n = acc.childCount
    if n > 0:
        if cur < depth:
            kids = _collect_children(acc, depth, cur, path, visible_only, role_filter)
            if kids:
                node["children"] = kids
        else:
            node["cc"] = n   # children_count — expand with larger depth or subtree path

    return node


def _collect_children(acc, depth, cur, path, visible_only, role_filter) -> list:
    result = []
    for i in range(acc.childCount):
        cp = f"{path}/{i}" if path and path != "root" else str(i)
        child = _ser(acc.getChildAtIndex(i), depth, cur + 1, cp, visible_only, role_filter)
        if child:
            result.append(child)
    return result


def _search(acc, name: str, role: str, state: str, out: list, path: str, limit: int):
    if len(out) >= limit or acc is None:
        return
    try:
        r = acc.getRoleName()
        n = acc.name or ""
        sts = _states(acc)
    except Exception:
        return

    ok = True
    if name and name.lower() not in n.lower():
        ok = False
    if role and r != role:
        ok = False
    if state and state not in sts:
        ok = False

    if ok and (name or role or state):
        entry: dict = {"role": r, "name": n, "path": path}
        if sts:
            entry["states"] = sts
        val = _value(acc)
        if val is not None:
            entry["value"] = val
        out.append(entry)

    for i in range(acc.childCount):
        cp = f"{path}/{i}" if path else str(i)
        _search(acc.getChildAtIndex(i), name, role, state, out, cp, limit)


def _act(acc, action: str = "click") -> tuple[bool, str]:
    """Try AT-SPI action first, xdotool coords as fallback."""
    try:
        iface = acc.queryAction()
        for i in range(iface.nActions):
            if iface.getName(i).lower() == action.lower():
                iface.doAction(i)
                return True, f"AT-SPI '{action}'"
        if iface.nActions > 0:
            first = iface.getName(0)
            iface.doAction(0)
            return True, f"AT-SPI first-action '{first}'"
    except pyatspi.NotImplementedException:
        pass
    except Exception:
        pass

    # Coordinate fallback via wmctrl + xdotool
    try:
        comp = acc.queryComponent()
        bbox = comp.getExtents(pyatspi.DESKTOP_COORDS)
        x, y = bbox.x + bbox.width // 2, bbox.y + bbox.height // 2
        if x > 0 and y > 0:
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y), "click", "1"],
                check=True, capture_output=True,
            )
            return True, f"xdotool click at ({x},{y})"
    except Exception as e:
        return False, f"all methods failed: {e}"

    return False, "no actionable interface"


def _focus_window(window) -> None:
    try:
        window.queryComponent().grabFocus()
        return
    except Exception:
        pass
    title = window.name or ""
    if title:
        subprocess.run(["wmctrl", "-a", title], capture_output=True)


# ── MCP Tools ─────────────────────────────────────────────────────────

@mcp.tool()
def windows_list() -> str:
    """
    List all open windows (app name, window titles, PIDs).
    Call this first to get app names for other tools.
    """
    with _busy("windows_list"):
        desk = _desktop()
        result = []
        for app in desk:
            if not app or not app.name:
                continue
            wins = []
            for i in range(app.childCount):
                try:
                    w = app.getChildAtIndex(i)
                    if w and w.getRoleName() in ("frame", "window", "dialog"):
                        wins.append({"idx": i, "title": w.name or "", "role": w.getRoleName()})
                except Exception:
                    pass
            if wins:
                pid = None
                try:
                    pid = app.get_process_id()
                except Exception:
                    pass
                result.append({"app": app.name, "pid": pid, "windows": wins})
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
def ui_tree(
    app: str,
    window_idx: int = 0,
    depth: int = 3,
    path: str = "",
    filter_role: str = "",
    visible_only: bool = True,
) -> str:
    """
    Return the UI accessibility tree of a window as compact JSON.
    No screenshot — all text. Use path to drill into a subtree.
    depth: how many levels to expand (default 3, max 6).
    filter_role: show only nodes of this role (e.g. 'button', 'text').
    Nodes with 'cc' have hidden children — drill with path + higher depth.
    Returned 'path' values are used in ui_click / ui_set_value / ui_find.
    """
    with _busy("ui_tree"):
        win, err = _get_window(app, window_idx)
        if err:
            return json.dumps({"error": err})
        depth = min(max(depth, 1), 6)
        root = _nav(win, path) if path else win
        if root is None:
            return json.dumps({"error": f"path '{path}' not found"})
        tree = _ser(root, depth, 0, path or "root", visible_only, filter_role)
        return json.dumps(tree, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
def ui_find(
    app: str,
    window_idx: int = 0,
    name: str = "",
    role: str = "",
    state: str = "",
    max_results: int = 20,
) -> str:
    """
    Search for UI elements by name substring, role, or state.
    Returns list of matches with paths usable in ui_click / ui_set_value.
    Common roles: button, text, entry, menu item, check box, radio button,
                  combo box, list item, label, menu, tool bar, scroll bar.
    Common states: enabled, editable, checked, selected, focused, visible.
    """
    with _busy("ui_find"):
        if not name and not role and not state:
            return json.dumps({"error": "provide at least one of: name, role, state"})
        win, err = _get_window(app, window_idx)
        if err:
            return json.dumps({"error": err})
        out: list = []
        _search(win, name, role, state, out, "", min(max_results, 50))
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
def ui_click(
    app: str,
    window_idx: int = 0,
    path: str = "",
    name: str = "",
    role: str = "",
    action: str = "click",
) -> str:
    """
    Click (or activate) a UI element.
    Use path from ui_tree/ui_find, OR find by name + optional role.
    action: 'click' | 'press' | 'expand' | 'activate' (default: click).
    """
    with _busy("ui_click"):
        win, err = _get_window(app, window_idx)
        if err:
            return json.dumps({"error": err})

        if path:
            acc = _nav(win, path)
            if acc is None:
                return json.dumps({"error": f"path '{path}' not found"})
        elif name or role:
            found: list = []
            _search(win, name, role, "enabled", found, "", 1)
            if not found:
                _search(win, name, role, "", found, "", 1)
            if not found:
                return json.dumps({"error": f"element not found: name='{name}' role='{role}'"})
            acc = _nav(win, found[0]["path"])
            if acc is None:
                return json.dumps({"error": "found element but path navigation failed"})
        else:
            return json.dumps({"error": "provide path or name/role"})

        _focus_window(win)
        time.sleep(0.05)
        ok, msg = _act(acc, action)
        return json.dumps({
            "success": ok, "msg": msg,
            "element": {"role": acc.getRoleName(), "name": acc.name or ""},
        }, separators=(",", ":"))


@mcp.tool()
def ui_type(
    text: str,
    app: str = "",
    window_idx: int = 0,
    path: str = "",
    clear_first: bool = False,
) -> str:
    """
    Type text into the focused or specified element.
    If path is given, tries AT-SPI editableText.setTextContents() first (instant, no key simulation).
    Falls back to xdotool type.
    """
    with _busy("ui_type"):
        if app and path:
            win, err = _get_window(app, window_idx)
            if not err:
                acc = _nav(win, path)
                if acc:
                    try:
                        edit = acc.queryEditableText()
                        if clear_first:
                            edit.setTextContents("")
                        edit.setTextContents(text)
                        return json.dumps({"ok": True, "method": "AT-SPI editableText"})
                    except Exception:
                        pass
                    _act(acc, "click")
                    time.sleep(0.08)

        if app and not path:
            win, err = _get_window(app, window_idx)
            if not err:
                _focus_window(win)
                time.sleep(0.08)

        if clear_first:
            subprocess.run(["xdotool", "key", "ctrl+a"], capture_output=True)
            time.sleep(0.04)

        r = subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "20", "--", text],
            capture_output=True, text=True,
        )
        return json.dumps({"ok": r.returncode == 0, "method": "xdotool type"})


@mcp.tool()
def ui_key(keys: str, app: str = "", window_idx: int = 0) -> str:
    """
    Press a keyboard shortcut.
    Examples: 'ctrl+c', 'Return', 'Escape', 'alt+F4', 'ctrl+shift+t', 'super+d'.
    Focuses the app window first if app is given.
    """
    with _busy(f"ui_key:{keys}"):
        if app:
            win, err = _get_window(app, window_idx)
            if not err:
                _focus_window(win)
                time.sleep(0.1)

        r = subprocess.run(
            ["xdotool", "key", "--clearmodifiers", keys],
            capture_output=True, text=True,
        )
        return json.dumps({"ok": r.returncode == 0, "keys": keys,
                           "err": r.stderr.strip() if r.returncode != 0 else ""},
                          separators=(",", ":"))


@mcp.tool()
def ui_set_value(app: str, path: str, value: str, window_idx: int = 0) -> str:
    """
    Set value of an editable text field or numeric spin button.
    Tries AT-SPI editableText → valueInterface → click+select+type fallback.
    """
    with _busy("ui_set_value"):
        win, err = _get_window(app, window_idx)
        if err:
            return json.dumps({"error": err})
        acc = _nav(win, path)
        if acc is None:
            return json.dumps({"error": f"path '{path}' not found"})

        try:
            acc.queryEditableText().setTextContents(value)
            return json.dumps({"ok": True, "method": "editableText"})
        except Exception:
            pass

        try:
            vi = acc.queryValue()
            vi.currentValue = float(value)
            return json.dumps({"ok": True, "method": "valueInterface"})
        except Exception:
            pass

        _act(acc, "click")
        time.sleep(0.08)
        subprocess.run(["xdotool", "key", "ctrl+a"], capture_output=True)
        time.sleep(0.04)
        r = subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", value],
            capture_output=True, text=True,
        )
        return json.dumps({"ok": r.returncode == 0, "method": "xdotool fallback"})


@mcp.tool()
def ui_get_text(app: str, path: str, window_idx: int = 0) -> str:
    """Read the full text content of any text-bearing element (label, entry, text area, etc.)."""
    with _busy("ui_get_text"):
        win, err = _get_window(app, window_idx)
        if err:
            return json.dumps({"error": err})
        acc = _nav(win, path)
        if acc is None:
            return json.dumps({"error": f"path '{path}' not found"})
        try:
            t = acc.queryText()
            return json.dumps({"text": t.getText(0, -1)})
        except Exception:
            return json.dumps({"text": acc.name or "", "note": "from name attribute"})


@mcp.tool()
def window_focus(app: str, window_idx: int = 0) -> str:
    """Bring a window to front and give it keyboard focus."""
    with _busy("window_focus"):
        win, err = _get_window(app, window_idx)
        if err:
            return json.dumps({"error": err})
        _focus_window(win)
        return json.dumps({"ok": True, "window": win.name or ""})


@mcp.tool()
def app_run(command: str, wait_seconds: float = 1.5) -> str:
    """
    Launch an application. Waits wait_seconds for it to start then returns.
    Examples: 'gedit', 'gnome-calculator', 'xterm', 'mousepad'.
    """
    with _busy(f"app_run:{command.split()[0]}"):
        try:
            env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
            subprocess.Popen(command.split(), env=env)
            time.sleep(max(wait_seconds, 0.5))
            return json.dumps({"ok": True, "command": command})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def clipboard_get() -> str:
    """Return current clipboard text content (X11 clipboard selection)."""
    with _busy("clipboard_get"):
        for tool, args in [
            ("xclip",  ["-selection", "clipboard", "-o"]),
            ("xsel",   ["--clipboard", "--output"]),
            ("xdotool", ["getclipboard"]),
        ]:
            try:
                r = subprocess.run([tool] + args, capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    return json.dumps({"text": r.stdout})
            except FileNotFoundError:
                continue
        return json.dumps({"error": "no clipboard tool found (install xclip or xsel)"})


@mcp.tool()
def clipboard_set(text: str) -> str:
    """Copy text to clipboard."""
    with _busy("clipboard_set"):
        for tool, args in [
            ("xclip", ["-selection", "clipboard"]),
            ("xsel",  ["--clipboard", "--input"]),
        ]:
            try:
                r = subprocess.run([tool] + args, input=text.encode(), capture_output=True, timeout=3)
                if r.returncode == 0:
                    return json.dumps({"ok": True, "tool": tool})
            except FileNotFoundError:
                continue
        return json.dumps({"error": "no clipboard tool found (install xclip or xsel)"})


_SOUNDS = {
    "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "bell":     "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "error":    "/usr/share/sounds/freedesktop/stereo/dialog-error.oga",
    "message":  "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
}


@mcp.tool()
def sound_notify(
    message: str = "Готово",
    sound: str = "complete",
    notify: bool = True,
) -> str:
    """
    Play a sound and/or show a desktop notification.
    Use this when the user asked to be notified when a task is done.
    Do NOT call this by default — only when explicitly requested.

    sound: 'complete' (default) | 'bell' | 'error' | 'message'
    notify: also show a desktop notification balloon (default True)
    message: text for the notification
    """
    with _busy("sound_notify"):
        if not _sound_enabled:
            return json.dumps({"sound": "disabled", "notify": "disabled"})

        results = {}

        sound_file = _SOUNDS.get(sound, _SOUNDS["complete"])
        r = subprocess.run(
            ["paplay", sound_file],
            capture_output=True, timeout=5,
        )
        results["sound"] = "ok" if r.returncode == 0 else r.stderr.decode()[:100]

        if notify:
            r2 = subprocess.run(
                ["notify-send", "--app-name=gui-desk-control",
                 "--icon=dialog-information", "gui-desk-control", message],
                capture_output=True, timeout=5,
            )
            results["notify"] = "ok" if r2.returncode == 0 else r2.stderr.decode()[:100]

        return json.dumps(results)


if __name__ == "__main__":
    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0.0"
    if _tray:
        _tray.start()
    mcp.run(transport="stdio")
