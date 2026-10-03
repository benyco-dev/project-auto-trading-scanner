#!/bin/bash
# VM에서 매일 실행된다 (cron). 모의매매 장부로 대시보드 JSON을 만들어
# 공개 GCS 버킷에 올린다. GitHub Pages의 정적 페이지가 이 JSON을 읽어 그린다.
#
# 저장소에 쓰기 권한을 주지 않으려고 이 구조를 택했다 — VM은 버킷 하나에만
# 쓸 수 있고(storage.objectAdmin, 버킷 단위), 코드 저장소는 건드리지 않는다.
set -euo pipefail

# 계정명·버킷 같은 식별자는 코드에 넣지 않는다. cron이 환경변수로 넘긴다.
BUCKET="${PAPER_BUCKET:?PAPER_BUCKET 가 필요합니다 (예: gs://my-bucket)}"
IMAGE="${PAPER_IMAGE:?PAPER_IMAGE 가 필요합니다 (예: myuser/auto-trading-scanner:latest)}"
DATA_DIR="${PAPER_DATA_DIR:-/opt/auto-trading}"

docker run --rm -v "$DATA_DIR:/data" --entrypoint python "$IMAGE" \
  -m dashboard.report --orders-dir /data --out /data/paper.json

# no-cache: 페이지가 항상 최신을 읽도록. 브라우저/CDN이 하루 지난 값을 들고 있으면
# "매일 갱신"이 눈에 안 보인다.
gcloud storage cp "$DATA_DIR/paper.json" "$BUCKET/paper.json" --cache-control="no-cache, max-age=60"

echo "업로드 완료: $BUCKET/paper.json"
