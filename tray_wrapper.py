#!/usr/bin/env python3
"""
Generic MCP stdio tray wrapper.
Spawns any MCP binary as a child process, proxies stdio,
and shows a system tray icon with busy/idle state.

Usage:
  python3 tray_wrapper.py /path/to/binary [binary-args...]

Env:
  MCP_SERVER_NAME  — display name for the tray icon (default: binary basename)
  DISPLAY          — X11 display (default: :0.0)
"""

import asyncio
import colorsys
import hashlib
import json
import os
import sys
import threading
from typing import Optional

if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":0.0"

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    _TRAY_OK = True
except ImportError:
    _TRAY_OK = False

_TRAY_FONTS = [
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _name_to_color(name: str) -> tuple:
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, 0.70, 0.88)
    return (int(r * 255), int(g * 255), int(b * 255))


def _name_to_label(name: str) -> str:
    parts = [p for p in name.split("-") if p]
    suffix = parts[-1].upper()
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
        if self._font:
            bb = d.textbbox((0, 0), self._label, font=self._font)
            tx = (64 - (bb[2] - bb[0])) / 2 - bb[0]
            ty = (64 - (bb[3] - bb[1])) / 2 - bb[1]
            d.text((tx, ty), self._label, fill="white", font=self._font)
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


# ── Async stdio proxy ─────────────────────────────────────────────────

async def _proxy(cmd: list[str], tray: Optional[_TrayIcon]):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
        env=os.environ,
    )

    loop = asyncio.get_event_loop()
    stdin_reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(stdin_reader),
        sys.stdin.buffer,
    )

    pending: dict[str, str] = {}

    async def stdin_to_proc():
        while True:
            line = await stdin_reader.readline()
            if not line:
                proc.stdin.write_eof()
                break
            try:
                msg = json.loads(line)
                if msg.get("method") == "tools/call":
                    tool_name = msg.get("params", {}).get("name", "?")
                    req_id = str(msg.get("id", ""))
                    if req_id:
                        pending[req_id] = tool_name
                    if tray:
                        tray.set_busy(tool_name)
            except Exception:
                pass
            proc.stdin.write(line)
            await proc.stdin.drain()

    async def proc_to_stdout():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
                req_id = str(msg.get("id", ""))
                if req_id and req_id in pending:
                    del pending[req_id]
                    if not pending and tray:
                        tray.set_idle()
            except Exception:
                pass
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    await asyncio.gather(stdin_to_proc(), proc_to_stdout())
    await proc.wait()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: tray_wrapper.py /path/to/binary [args...]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1:]
    server_name = os.environ.get("MCP_SERVER_NAME") or os.path.basename(cmd[0]).split(".")[0]

    tray = _TrayIcon(server_name) if _TRAY_OK else None
    if tray:
        tray.start()

    asyncio.run(_proxy(cmd, tray))
