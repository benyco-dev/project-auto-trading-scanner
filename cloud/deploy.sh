#!/bin/bash
# Google Compute Engine에 Docker Hub 이미지(<your-dockerhub-user>/auto-trading-scanner)를
# 매일 pull + run 하는 VM을 만든다. (예전엔 scanner.py를 scp로 직접 올려서
# 파이썬 venv로 돌렸지만, 지금은 CI가 빌드한 이미지를 그대로 받아서 쓴다.)
# 알림은 따로 보내지 않고, 새 시그널은 인스턴스 위 signals_history.csv에 누적된다.
set -euo pipefail

# ── 설정 ─────────────────────────────────────────────────────────
PROJECT_ID="$(gcloud config get-value project)"
ZONE="asia-northeast3-a"                 # 서울 리전
INSTANCE_NAME="auto-trading-scanner"
MACHINE_TYPE="e2-small"
IMAGE="${IMAGE:-your-dockerhub-user/auto-trading-scanner:latest}"   # Docker Hub 이미지
DEPLOY_USER="${DEPLOY_USER:-$(whoami)}"   # cron이 이 계정으로 등록됨 (docker 그룹 + 로그인 필요)
SCAN_ARGS="--universe sp500"
CRON_SCHEDULE="30 21 * * 1-5"            # UTC 기준 월~금 21:30 (미국 정규장 마감 직후)
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/4] 프로젝트: $PROJECT_ID / 존: $ZONE / 인스턴스: $INSTANCE_NAME"

if gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" &>/dev/null; then
  echo "이미 인스턴스가 존재합니다. VM 생성은 건너뜁니다."
else
  echo "[2/4] VM 생성 중 (Shielded VM: Secure Boot 포함)..."
  gcloud compute instances create "$INSTANCE_NAME" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=10GB \
    --shielded-secure-boot \
    --metadata-from-file=startup-script="$SCRIPT_DIR/startup-script.sh"
fi

echo "[3/4] SSH(IAP) 준비될 때까지 대기 중..."
for i in $(seq 1 20); do
  if gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --tunnel-through-iap \
      --command="test -f /opt/auto-trading/.ready" &>/dev/null; then
    echo "준비 완료"
    break
  fi
  echo "  대기 중... ($i/20)"
  sleep 10
done

echo "[4/4] $DEPLOY_USER 계정을 docker 그룹에 추가..."
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --tunnel-through-iap \
  --command="sudo usermod -aG docker $DEPLOY_USER"

CRON_LINE="$CRON_SCHEDULE docker pull $IMAGE >> /var/log/auto-trading/run.log 2>&1 && docker run --rm -v /opt/auto-trading:/data $IMAGE $SCAN_ARGS >> /var/log/auto-trading/run.log 2>&1"

cat <<EOF

자동화는 여기까지. 아래는 Docker Hub 자격 증명이 필요해서 직접 해야 하는 단계:

1. VM 접속:
   gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --tunnel-through-iap

2. Docker Hub 로그인 (읽기 전용 토큰 사용, Write/Delete 권한 불필요):
   docker login -u <dockerhub-id>

3. 로그인 정보를 $DEPLOY_USER 계정으로 복사 (cron이 이 계정으로 돌기 때문):
   sudo mkdir -p /home/$DEPLOY_USER/.docker
   sudo cp ~/.docker/config.json /home/$DEPLOY_USER/.docker/config.json
   sudo chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER/.docker

4. cron 등록:
   sudo -u $DEPLOY_USER bash -c "{ crontab -l 2>/dev/null; echo '$CRON_LINE'; } | crontab -"

로그 확인:   ./check-logs.sh
인스턴스 삭제(과금 중지): ./delete.sh
EOF
