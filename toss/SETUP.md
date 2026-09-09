# 토스증권 Open API 발급 방법

공식 문서: https://developers.tossinvest.com , https://corp.tossinvest.com/ko/open-api

## 0. 전제 조건

- **토스증권 계좌**가 있어야 한다 (Open API는 토스증권 고객 대상). 계좌가 없으면
  토스 앱에서 먼저 계좌 개설 — 이 부분은 본인 명의 인증이 필요해서 대신해드릴 수 없다.
- Open API가 아직 순차 오픈 중일 수 있다 — 계정이 없다면
  https://corp.tossinvest.com/ko/open-api 에서 사전 신청하고 오픈 알림을 기다린다.

## 1. API 키 발급

1. 토스증권 **WTS(웹 트레이딩 시스템)**에 로그인
2. 설정(Settings) → **Open API** 메뉴
3. `client_id`, `client_secret` 발급 — **client_secret은 이때 한 번만 보여준다.** 안전한 곳에 즉시 보관.
4. 같은 화면에서 **허용 IP 목록**에 IP를 등록해야 한다. 등록 안 된 IP로 호출하면 403.
   → 등록할 IP는 GCE 서버용으로 예약해둔 고정 IP (`auto-trading-scanner-ip`)다.
   아래 명령으로 직접 조회해서 등록할 것:
   ```bash
   gcloud compute addresses describe auto-trading-scanner-ip --region=asia-northeast3 --format="value(address)"
   ```

## 2. 인증 방식 (OAuth2 Client Credentials)

```bash
curl -X POST 'https://openapi.tossinvest.com/oauth2/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=client_credentials' \
  -d 'client_id=발급받은_client_id' \
  -d 'client_secret=발급받은_client_secret'
```

응답으로 `access_token`(만료 3600초)을 받는다. 만료되면 그냥 다시 발급받으면 된다
(코드에서 자동 처리하도록 구현해둠, [`toss_client.py`](toss_client.py) 참고).

초당 5회 제한(AUTH 그룹)이 있으니 토큰은 캐싱해서 재사용할 것.

## 3. 계좌 번호(accountSeq) 확인

계좌·자산·주문 API는 전부 `X-Tossinvest-Account` 헤더에 계좌 번호(accountSeq)가 필요하다.
토큰 발급 후:

```bash
curl -s 'https://openapi.tossinvest.com/api/v1/accounts' \
  -H 'Authorization: Bearer {access_token}'
```

로 내 계좌 목록과 accountSeq를 확인한다.

## 4. 자격 증명 저장 — 절대 코드/채팅에 붙여넣지 말 것

`client_id`/`client_secret`은 계좌에 대한 주문 권한을 가진 민감 정보다.
**이 대화창에 붙여넣지 말고**, 아래 중 한 방법으로 서버에만 직접 저장한다.

### 권장: GCP Secret Manager

```bash
# 로컬 터미널에서 직접 (본인만 실행, 비밀번호 채팅에 노출 안 됨)
echo -n "발급받은_client_id"     | gcloud secrets create toss-client-id     --data-file=-
echo -n "발급받은_client_secret" | gcloud secrets create toss-client-secret --data-file=-
```

VM의 서비스 계정에 `roles/secretmanager.secretAccessor`가 있어야 읽을 수 있다
(`cloud/security-hardening.sh`에서 이미 부여하도록 되어 있음).

VM 안에서 시크릿을 환경변수로 로드:

```bash
export TOSS_CLIENT_ID="$(gcloud secrets versions access latest --secret=toss-client-id)"
export TOSS_CLIENT_SECRET="$(gcloud secrets versions access latest --secret=toss-client-secret)"
```

### 대안: VM 위에 직접 .env 파일

```bash
gcloud compute ssh auto-trading-scanner --zone=asia-northeast3-a --tunnel-through-iap
# VM 안에서:
cat > /opt/auto-trading/.env << 'EOF'
TOSS_CLIENT_ID=발급받은_client_id
TOSS_CLIENT_SECRET=발급받은_client_secret
EOF
chmod 600 /opt/auto-trading/.env
```

`toss_client.py`는 두 방식 모두 지원한다 — 환경변수가 있으면 그걸 쓰고,
없으면 `.env` 파일을 찾는다.

## 5. 실주문 전 반드시 확인할 것

- `place_orders.py`는 **기본이 dry-run**이다. 실제로 주문을 내려면
  `--live` 플래그와 환경변수 `TOSS_LIVE_TRADING=CONFIRM`을 **둘 다** 설정해야 한다.
- 1억 원 이상 주문은 API가 `confirm-high-value-required` 에러를 준다 (문서 기준) —
  별도 확인 절차가 필요하다는 뜻.
- 정규장 시간 외 호출하면 `order-hours-closed` 에러.
- 이 문서의 엔드포인트/필드명은 공식 overview 문서(2026년 기준) 기준이며,
  실제 계정으로 첫 호출 전에 https://openapi.tossinvest.com/docs (인터랙티브 레퍼런스)에서
  정확한 스키마를 한 번 더 확인할 것을 권장한다.
