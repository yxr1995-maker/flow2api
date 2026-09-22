#!/usr/bin/env bash
set -euo pipefail

SERVICE_LABEL="com.flow2api.server"
UID_VAL=$(id -u)
TARGET_DOMAIN="gui/$UID_VAL"
TARGET_SPEC="$TARGET_DOMAIN/$SERVICE_LABEL"

echo "Attempting to stop $SERVICE_LABEL via launchctl..."
if launchctl print "$TARGET_SPEC" >/dev/null 2>&1; then
    launchctl bootout "$TARGET_DOMAIN" "$HOME/Library/LaunchAgents/$SERVICE_LABEL.plist" 2>/dev/null || \
    launchctl bootout "$TARGET_SPEC" 2>/dev/null || {
        echo "[ERROR] Failed to bootout $TARGET_SPEC via launchctl."
        exit 1
    }
    echo "[OK] Successfully unloaded $SERVICE_LABEL from $TARGET_DOMAIN."
else
    echo "[INFO] $SERVICE_LABEL is not loaded in $TARGET_DOMAIN (service not active). No action taken."
fi
