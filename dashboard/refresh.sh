#!/bin/bash
# 로컬에서 미리보기할 때 쓴다. VM의 장부를 받아 docs/paper.json 을 만든다.
# 운영 갱신은 VM이 publish.sh로 매일 자동 수행하므로 이 스크립트는 선택 사항이다.
# 장부(csv)와 paper.json 모두 .gitignore — 저장소에는 코드만 둔다.
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

./.venv/bin/python -m dashboard.report --orders-dir _ledgers --out docs/paper.json
echo "확인: open 'docs/index.html?data=paper.json'"
