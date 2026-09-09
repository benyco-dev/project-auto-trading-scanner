#!/bin/bash
# VM에 자동 매매 cron 2개를 설치한다. VM 안에서 실행할 것.
#
# 왜 2개인가: 스캔은 미국장 마감 후에 해야 그날 종가로 시그널이 확정되고,
# 주문은 다음 장이 열린 뒤에 넣어야 체결된다. 마감 후에 주문하면 API가
# order-hours-closed로 거부한다.
#
#   21:30 UTC (평일)  스캔 → signals_history.csv 기록
#   13:45 UTC (평일)  매도(청산 규칙) → 매수(현금 한도 내) 순서로 실행
#                     매도를 먼저 돌려서 현금을 확보한 뒤 매수한다.
#
# 실거래 스위치: 이 cron은 --live를 붙이지만, 컨테이너에 넘기는
# /opt/auto-trading/.env 에 아래 줄이 있어야 실제 주문이 나간다.
#
#   TOSS_LIVE_TRADING=CONFIRM
#
# 그 줄이 없으면 주문 직전에 막히고 에러만 로그에 남는다 (안전한 기본값).
# 실거래를 시작할 준비가 됐을 때 본인이 직접 추가할 것.
set -euo pipefail

IMAGE="${IMAGE:-your-dockerhub-user/auto-trading-scanner:latest}"
DATA="/opt/auto-trading"
ACCOUNT_SEQ="${ACCOUNT_SEQ:-1}"
# 하루에 새로 담을 최대 종목 수. 한 종목당 리스크 1%가 쌓이므로 5건이면 총 5%.
# 표본 5개 시뮬레이션에서 3건은 모든 자본 구간($2k/$8k/$20k)에서 최하위였고,
# 5건 이상은 차이가 없었다 — 놓친 신호의 비용은 실재하지만 6번째부터는 가치가 없다.
MAX_ORDERS=5

RUN="docker run --rm -v $DATA:/data --env-file $DATA/.env"

SCAN="30 21 * * 1-5 docker pull -q $IMAGE && $RUN $IMAGE --universe sp500 --account-seq $ACCOUNT_SEQ >> /var/log/auto-trading/run.log 2>&1"
SELL="45 13 * * 1-5 $RUN --entrypoint python $IMAGE -m toss.exit_positions --account-seq $ACCOUNT_SEQ --live >> /var/log/auto-trading/trade.log 2>&1"
BUY="50 13 * * 1-5 $RUN --entrypoint python $IMAGE -m toss.place_orders --account-seq $ACCOUNT_SEQ --max-orders $MAX_ORDERS --live >> /var/log/auto-trading/trade.log 2>&1"

{ echo "$SCAN"; echo "$SELL"; echo "$BUY"; } | crontab -

echo "등록된 cron:"
crontab -l
echo
echo "실거래 상태: $(grep -q TOSS_LIVE_TRADING "$DATA/.env" && echo '켜짐 (실제 주문 나감)' || echo '꺼짐 (dry-run, .env에 TOSS_LIVE_TRADING=CONFIRM 추가하면 켜짐)')"
