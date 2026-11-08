#!/bin/bash
# 로컬에서 미리보기할 때 쓴다. VM의 장부를 받아 docs/paper.json, docs/ma40w.json 을 만든다.
# 운영 갱신은 VM이 publish.sh로 매일 자동 수행하므로 이 스크립트는 선택 사항이다.
# 장부(csv)와 json 모두 .gitignore — 저장소에는 코드만 둔다.
set -euo pipefail

ZONE="asia-northeast3-a"
INSTANCE_NAME="auto-trading-scanner"
LEDGERS=(paper_orders_2000 paper_orders_8000 paper_orders_20000 paper_orders_ma40w_20000)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p _ledgers

for f in "${LEDGERS[@]}"; do
  echo "장부 받는 중: ${f}.csv"
  gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --tunnel-through-iap \
    --command="cat /opt/auto-trading/${f}.csv" > "_ledgers/${f}.csv"
done

./.venv/bin/python -m dashboard.report --orders-dir _ledgers --out docs/paper.json
./.venv/bin/python -m dashboard.report --orders-dir _ledgers --prefix paper_orders_ma40w \
  --capitals 20000 --out docs/ma40w.json
echo "확인: open 'docs/index.html?base=.'"
