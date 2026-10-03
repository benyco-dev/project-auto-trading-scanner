#!/bin/bash
# VM의 모의매매 장부를 받아 docs/index.html 을 다시 만든다.
# 장부(csv)는 .gitignore라 커밋되지 않고, 계산 결과만 HTML에 담긴다.
set -euo pipefail

ZONE="asia-northeast3-a"
INSTANCE_NAME="auto-trading-scanner"
CAPITALS=(2000 8000 20000)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p _ledgers

for cap in "${CAPITALS[@]}"; do
  echo "장부 받는 중: paper_orders_${cap}.csv"
  gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --tunnel-through-iap \
    --command="cat /opt/auto-trading/paper_orders_${cap}.csv" > "_ledgers/paper_orders_${cap}.csv"
done

./.venv/bin/python -m dashboard.report --orders-dir _ledgers --out docs/index.html
echo "확인: open docs/index.html"
