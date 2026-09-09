#!/bin/bash
# GCP 보안 점검에서 발견된 나머지 항목을 고친다.
# 이미 완료된 것(이 스크립트 실행 전에 이미 적용됨):
#   - 고정 외부 IP 예약 (auto-trading-scanner-ip)
#   - IAP API 활성화
#   - allow-ssh-iap 방화벽 규칙 생성 (IAP 대역에서만 SSH 허용)
#
# 이 스크립트가 하는 일 (자동 승인 필터가 IAM/삭제 계열을 막아서 직접 실행 필요):
#   1. 전 세계에 열려있던 위험한 기본 규칙 삭제 (SSH 전체공개, RDP, 미사용 HTTP)
#   2. 최소 권한 서비스 계정 생성 + VM에 연결 (현재는 프로젝트 Editor 권한을
#      가진 기본 서비스 계정이 VM에 붙어있음 — VM이 뚫리면 프로젝트 전체가
#      위험해지는 구조라 최소 권한 계정으로 교체)
#   3. Shielded VM Secure Boot 활성화
#   4. OS Login 활성화 (프로젝트 전역 SSH 키 대신 IAM 기반 접근으로 전환)
#
# 실행 전 확인:
#   - 이 GCP 프로젝트에 auto-trading-scanner 외에 SSH로 붙는 다른 VM이 없다는 것
#   - default-allow-http/rdp를 다른 용도로 쓰고 있지 않다는 것
#   (지금까지 점검한 바로는 인스턴스가 auto-trading-scanner 하나뿐이라 안전함)

set -euo pipefail

PROJECT_ID="$(gcloud config get-value project)"
ZONE="asia-northeast3-a"
INSTANCE_NAME="auto-trading-scanner"
NEW_SA_NAME="auto-trading-scanner-sa"
NEW_SA_EMAIL="${NEW_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEFAULT_SA_EMAIL="$(gcloud iam service-accounts list --format='value(email)' --filter='displayName:Compute Engine default')"

# 재실행해도 안전하도록: 이미 지워진/만들어진 건 건너뛴다.
delete_if_exists() {
  if gcloud compute firewall-rules describe "$1" &>/dev/null; then
    gcloud compute firewall-rules delete "$1" --quiet
  else
    echo "  ($1 은 이미 삭제됨, 건너뜀)"
  fi
}

# add-iam-policy-binding를 서비스 계정 생성 직후 바로 호출하면 전파 지연으로
# "does not exist" 에러가 날 수 있어서, 몇 번 재시도한다.
bind_with_retry() {
  local member="$1" role="$2"
  for i in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="$member" --role="$role" --condition=None &>/tmp/iam-bind.log; then
      return 0
    fi
    echo "  권한 부여 재시도 중... ($i/5)"
    sleep 5
  done
  cat /tmp/iam-bind.log
  return 1
}

echo "[1/6] 전 세계 공개된 위험한 방화벽 규칙 삭제 (SSH 전체공개는 이미 allow-ssh-iap로 대체됨)"
delete_if_exists default-allow-ssh
delete_if_exists default-allow-rdp
delete_if_exists default-allow-http

echo "[2/6] 최소 권한 서비스 계정 생성 (로그/모니터링 쓰기 + 시크릿 조회만 허용)"
if gcloud iam service-accounts describe "$NEW_SA_EMAIL" &>/dev/null; then
  echo "  (서비스 계정 이미 존재함, 생성 건너뜀)"
else
  gcloud iam service-accounts create "$NEW_SA_NAME" \
    --display-name="auto-trading-scanner (least-privilege)"
  echo "  생성 직후 전파 대기 중..."
  sleep 10
fi
bind_with_retry "serviceAccount:${NEW_SA_EMAIL}" "roles/logging.logWriter"
bind_with_retry "serviceAccount:${NEW_SA_EMAIL}" "roles/monitoring.metricWriter"
bind_with_retry "serviceAccount:${NEW_SA_EMAIL}" "roles/secretmanager.secretAccessor"

CURRENT_SA=$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --format="value(serviceAccounts[0].email)")
if [ "$CURRENT_SA" = "$NEW_SA_EMAIL" ]; then
  echo "[3/6] VM이 이미 최소 권한 서비스 계정을 쓰고 있음, 건너뜀"
else
  echo "[3/6] VM을 잠깐 멈추고 서비스 계정 교체 + Secure Boot 활성화"
  gcloud compute instances stop "$INSTANCE_NAME" --zone="$ZONE"
  gcloud compute instances set-service-account "$INSTANCE_NAME" --zone="$ZONE" \
    --service-account="$NEW_SA_EMAIL" \
    --scopes=logging-write,monitoring-write,cloud-platform
  gcloud compute instances update "$INSTANCE_NAME" --zone="$ZONE" --shielded-secure-boot
  gcloud compute instances start "$INSTANCE_NAME" --zone="$ZONE"
fi

echo "[4/6] 이제 아무 VM도 안 쓰는 기본 서비스 계정의 프로젝트 Editor 권한 제거"
gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEFAULT_SA_EMAIL}" --role="roles/editor" --condition=None \
  || echo "  (이미 제거되었거나 존재하지 않음)"

echo "[5/6] OS Login 활성화 (프로젝트 전역 정적 SSH 키 대신 IAM 기반 접근)"
gcloud compute project-info add-metadata --metadata enable-oslogin=TRUE
gcloud compute project-info remove-metadata --keys=ssh-keys || echo "  (이미 제거됨)"

echo "[6/6] SSH는 이제 IAP 터널을 통해서만 접속 가능. 확인:"
echo "  gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --tunnel-through-iap --command='echo ok'"
echo ""
echo "완료. 되돌리려면:"
echo "  방화벽 원복: gcloud compute firewall-rules create default-allow-ssh --network=default --allow=tcp:22 --source-ranges=0.0.0.0/0"
echo "  Editor 권한 원복: gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$DEFAULT_SA_EMAIL --role=roles/editor"
