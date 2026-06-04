# App-MCP

MCP (Model Context Protocol) servers for AI assistants.

---

## gui-desk-control

> Control Ubuntu desktop apps via AT-SPI2 accessibility tree — **no screenshots, no vision models**

Controls native GUI applications on Ubuntu/XFCE/GNOME through the AT-SPI2 accessibility layer. Returns structured widget trees as compact JSON — minimal token usage, maximum functionality.

### How it works

```
Claude ──► MCP tool call ──► AT-SPI2 D-Bus ──► App widget tree
                                                      │
                                              role/name/state/path
                                              (text, not pixels)
```

### Tools

| Tool | Description |
|---|---|
| `windows_list` | List all open windows and apps |
| `ui_tree` | Get full widget tree of a window (depth-limited JSON) |
| `ui_find` | Search elements by role / name / state |
| `ui_click` | Click element by path or by name+role |
| `ui_type` | Type text (AT-SPI editableText or xdotool fallback) |
| `ui_key` | Press keyboard shortcut (`ctrl+s`, `super`, `alt+F4`, …) |
| `ui_set_value` | Set value of input field or spin button |
| `ui_get_text` | Read text content of any element |
| `window_focus` | Bring window to front |
| `app_run` | Launch an application |
| `clipboard_get` | Read clipboard text |
| `clipboard_set` | Write clipboard text |

### System tray icon

When running, the server shows a colored icon in the system tray:
- **Colored circle + letter** — unique per server name (auto-generated from hash)
- **Full color** — idle, waiting for commands
- **Dimmed + orange dot** — busy, executing a tool (tooltip shows which one)

Requires `pystray` and `Pillow` — gracefully skipped if not installed.

### Requirements

```bash
sudo apt install python3-pyatspi at-spi2-core xdotool wmctrl xclip
```

Python venv with all dependencies:
```bash
python3 -m venv --system-site-packages ~/desktop-mcp-venv
~/desktop-mcp-venv/bin/pip install mcp pystray Pillow
```

### Install (automatic)

```bash
bash desktop-mcp/install.sh
```

Installs all dependencies and registers the server with Claude Code.

### Manual registration

```bash
claude mcp add gui-desk-control \
  -e DISPLAY=:0.0 \
  -e DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  -- ~/desktop-mcp-venv/bin/python3 /path/to/desktop-mcp/server.py
```

### Usage example

```
windows_list → ["Google Chrome", "xfce4-terminal", "Thunar", ...]

ui_tree(app="Google Chrome", depth=3)
→ { role: "frame", name: "...", children: [...] }

ui_find(app="Google Chrome", role="button", name="New Tab")
→ [{ role: "button", name: "New Tab", path: "0/1/3" }]

ui_click(app="Google Chrome", path="0/1/3")
→ { success: true }

ui_key(keys="super")   # open apps menu
```

### Compatibility

| App type | Support |
|---|---|
| GTK apps (Thunar, xed, gedit, …) | Full — widget tree, click, type, read text |
| Qt/QML apps (e.g. AmneziaVPN) | Partial — structure visible, text labels often empty |
| Electron / browser apps | Use Playwright MCP instead |

- Ubuntu with XFCE, GNOME, or other AT-SPI2-compatible desktops
- X11 (`:0.0`) — tested and working
- Wayland — not tested

### Tested on

- Ubuntu 22.04 / XFCE / X11
- Claude Code CLI (MCP stdio transport)
- Verified tools: `windows_list`, `ui_tree`, `ui_find`, `ui_click`, `ui_type`, `ui_key`, `window_focus`, `app_run`
- Demo: opened `xed --new-window`, wrote text via AT-SPI `editableText` interface
- Demo: opened XFCE app menu via `ui_key(keys="super")`
- Qt/QML apps (AmneziaVPN): partial AT-SPI2 support confirmed — buttons/checkboxes named, text labels empty
