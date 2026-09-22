#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

EXIT_CODE=0
SERVICE_LABEL="com.flow2api.server"
UID_VAL=$(id -u)
TARGET_SPEC="gui/$UID_VAL/$SERVICE_LABEL"

echo "=== Flow2API Doctor Diagnostic ==="

echo "1. Checking Python Virtual Environment..."
if [ -x ".venv/bin/python" ]; then
    PY_VER=$(.venv/bin/python --version 2>&1)
    echo "   [PASS] Virtualenv python valid: $PY_VER"
else
    echo "   [FAIL] .venv/bin/python not found or not executable"
    EXIT_CODE=1
fi

echo "2. Checking Configuration file..."
if [ -f "config/setting.toml" ]; then
    PERM=$(ls -l config/setting.toml | awk '{print $1}')
    echo "   [PASS] config/setting.toml exists with permissions: $PERM"
else
    echo "   [FAIL] config/setting.toml missing"
    EXIT_CODE=1
fi

echo "3. Checking Service State via launchctl..."
if launchctl print "$TARGET_SPEC" >/dev/null 2>&1; then
    PID_INFO=$(launchctl print "$TARGET_SPEC" | grep -E "^\s*pid = " | awk '{print $3}' || true)
    if [ -n "$PID_INFO" ]; then
        echo "   [INFO] Launchd service active (PID: $PID_INFO)"
    else
        echo "   [INFO] Launchd service loaded (not currently running)"
    fi
else
    echo "   [INFO] Service not registered in launchd domain gui/$UID_VAL"
fi

echo "4. Checking HTTP Service & Account Status..."
HEALTH_RAW=$(curl -s --max-time 3 -w "\n%{http_code}" http://127.0.0.1:8000/health 2>&1 || true)
HTTP_CODE=$(echo "$HEALTH_RAW" | tail -n1)
HEALTH_BODY=$(echo "$HEALTH_RAW" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
    echo "   [PASS] Service live: HTTP 200 OK"
    ACTIVE_TOKENS=$(echo "$HEALTH_BODY" | sed -n 's/.*"active_tokens": *\([0-9][0-9]*\).*/\1/p')
    ACTIVE_TOKENS=${ACTIVE_TOKENS:-0}
    TOTAL_TOKENS=$(echo "$HEALTH_BODY" | sed -n 's/.*"total_tokens": *\([0-9][0-9]*\).*/\1/p')
    TOTAL_TOKENS=${TOTAL_TOKENS:-0}
    if [ "$ACTIVE_TOKENS" -gt 0 ]; then
        echo "   [PASS] Account ready: $ACTIVE_TOKENS/$TOTAL_TOKENS active tokens available"
    else
        echo "   [WARN] Account not ready: 0 active tokens in pool (total: $TOTAL_TOKENS). Service is live but cannot serve generations yet."
    fi
else
    if [ -z "$HTTP_CODE" ] || [ "$HTTP_CODE" = "000" ]; then
        echo "   [FAIL] Service offline or timed out: unable to connect to http://127.0.0.1:8000/health within 3s"
    else
        echo "   [FAIL] Service returned non-200 HTTP status: $HTTP_CODE"
    fi
    EXIT_CODE=1
fi

echo "5. Checking Git Hygiene..."
UNTRACKED=$(git status --porcelain | grep -E "(\.venv|config/setting\.toml|server\.log|data/)" || true)
if [ -n "$UNTRACKED" ]; then
    echo "   [FAIL] Untracked or committed sensitive/runtime files detected in git:"
    echo "$UNTRACKED"
    EXIT_CODE=1
else
    echo "   [PASS] Runtime and secret paths properly ignored"
fi

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "=== Doctor Verdict: PASS ==="
else
    echo "=== Doctor Verdict: FAIL (exit $EXIT_CODE) ==="
fi
exit "$EXIT_CODE"
