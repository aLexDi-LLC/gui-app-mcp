#!/bin/bash
# Ubuntu Desktop MCP Server — install script
set -e

VENV="$HOME/desktop-mcp-venv"
SERVER="$(cd "$(dirname "$0")" && pwd)/server.py"

echo "=== gui-desk-control: installing ==="

# 1. System packages
echo "[1/5] System packages..."
sudo apt-get install -y \
  python3-pyatspi \
  at-spi2-core \
  xdotool \
  wmctrl \
  xclip

# 2. Python venv with system-site-packages (to access pyatspi)
echo "[2/5] Python venv..."
if [ ! -d "$VENV" ]; then
  python3 -m venv --system-site-packages "$VENV"
  curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python3"
fi

# 3. Python dependencies
echo "[3/5] Python packages..."
"$VENV/bin/pip" install --upgrade mcp pystray Pillow

# 4. Enable AT-SPI accessibility bus
echo "[4/5] AT-SPI2 bus..."
export NO_AT_BRIDGE=0
if ! pgrep -x at-spi2-registryd > /dev/null 2>&1; then
  /usr/lib/at-spi2-core/at-spi2-registryd &
fi

# 5. Register with Claude Code
echo "[5/5] Registering MCP server..."
DBUS="unix:path=/run/user/$(id -u)/bus"
claude mcp add gui-desk-control \
  -e DISPLAY="${DISPLAY:-:0.0}" \
  -e DBUS_SESSION_BUS_ADDRESS="$DBUS" \
  -- "$VENV/bin/python3" "$SERVER"

echo ""
echo "=== Done! Restart Claude Code to load the server. ==="
