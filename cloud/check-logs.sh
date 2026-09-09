#!/bin/bash
# 실행 로그 / 누적된 시그널 히스토리를 확인한다.
set -euo pipefail

ZONE="asia-northeast3-a"
INSTANCE_NAME="auto-trading-scanner"

echo "── 최근 실행 로그 (run.log) ──"
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --tunnel-through-iap --command="tail -n 50 /var/log/auto-trading/run.log 2>/dev/null || echo '아직 실행 기록이 없습니다.'"

echo ""
echo "── 누적 시그널 히스토리 (signals_history.csv) ──"
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --tunnel-through-iap --command="cat /opt/auto-trading/signals_history.csv 2>/dev/null || echo '아직 기록된 시그널이 없습니다.'"
