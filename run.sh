#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source .env 2>/dev/null || true

LOG_FILE="logs/cron_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs

echo "[$(date)] 파이프라인 시작" >> "$LOG_FILE"
/usr/bin/python3 main.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date)] 파이프라인 실패 (exit code: $EXIT_CODE)" >> "$LOG_FILE"
fi

echo "[$(date)] 파이프라인 종료" >> "$LOG_FILE"
exit $EXIT_CODE
