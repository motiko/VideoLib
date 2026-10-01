#!/usr/bin/env bash
# uninstall-service.sh — Remove VideoLib launchd user agent

set -euo pipefail

LABEL="com.videolib.agent"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "🗑  Uninstalling VideoLib launchd service..."

# Unload if loaded
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    echo "⏹  Stopping service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    echo "✅ Service stopped."
else
    echo "ℹ️  Service was not loaded."
fi

# Remove plist
if [[ -f "$PLIST_DST" ]]; then
    rm -f "$PLIST_DST"
    echo "✅ Removed $PLIST_DST"
else
    echo "ℹ️  Plist not found at $PLIST_DST"
fi

echo ""
echo "Note: Config ($HOME/.config/videolib/) and logs ($HOME/Library/Logs/VideoLib/) were preserved."
echo "      Delete them manually if no longer needed."
