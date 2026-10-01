#!/usr/bin/env bash
# install-service.sh — Install VideoLib as a macOS launchd user agent
# Usage: ./scripts/install-service.sh [/path/to/videolib]

set -euo pipefail

LABEL="com.videolib.agent"
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/../service/com.videolib.agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/VideoLib"
CONFIG_DIR="$HOME/.config/videolib"

# Determine videolib binary path
VIDEOLIB_BIN="${1:-$(command -v videolib 2>/dev/null || echo "")}"
if [[ -z "$VIDEOLIB_BIN" ]]; then
    echo "❌ Error: 'videolib' binary not found."
    echo "   Install via: brew install motiko/videolib/videolib"
    echo "   Or specify path: $0 /path/to/videolib"
    exit 1
fi

echo "📦 Installing VideoLib launchd service..."
echo "   Binary:  $VIDEOLIB_BIN"
echo "   Plist:   $PLIST_DST"
echo "   Logs:    $LOG_DIR"
echo "   Config:  $CONFIG_DIR"
echo ""

# Create directories
mkdir -p "$LOG_DIR" "$CONFIG_DIR" "$HOME/Library/LaunchAgents"

# Create config from example if not present
if [[ ! -f "$CONFIG_DIR/.env" ]]; then
    EXAMPLE_ENV="$(cd "$(dirname "$0")" && pwd)/../.env.example"
    if [[ -f "$EXAMPLE_ENV" ]]; then
        cp "$EXAMPLE_ENV" "$CONFIG_DIR/.env"
        echo "📝 Created $CONFIG_DIR/.env from template."
        echo "   ⚠️  Edit it now: vim $CONFIG_DIR/.env"
    else
        echo "⚠️  No .env.example found. Create $CONFIG_DIR/.env manually."
    fi
fi

# Unload existing service if loaded
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    echo "🔄 Unloading existing service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Generate plist from template by replacing placeholders
sed -e "s|__HOMEBREW_PREFIX__/bin/videolib|${VIDEOLIB_BIN}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "$PLIST_SRC" > "$PLIST_DST"

echo "✅ Plist installed to $PLIST_DST"

# Load the service
launchctl load -w "$PLIST_DST"
echo "🚀 Service loaded and started!"
echo ""
echo "Management commands:"
echo "   Status:   launchctl list | grep $LABEL"
echo "   Stop:     launchctl unload $PLIST_DST"
echo "   Restart:  launchctl unload $PLIST_DST && launchctl load -w $PLIST_DST"
echo "   Logs:     tail -f $LOG_DIR/videolib.stdout.log"
