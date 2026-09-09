# GCE 자동 스캔 배포

**GitHub push → CI가 Docker 이미지 빌드 → Docker Hub push → GCE VM이 매일 pull + run.**
텔레그램 등 알림은 붙이지 않았고, 새로 발견된 시그널은 인스턴스 안
`signals_history.csv`에 계속 쌓인다 (이미 본 시그널은 다시 기록하지 않음).

예전엔 `scanner.py`를 scp로 직접 VM에 올려서 파이썬 venv로 돌렸지만,
지금은 [`../Dockerfile`](../Dockerfile)로 빌드된 이미지(`<your-dockerhub-user>/auto-trading-scanner`)를
그대로 받아서 컨테이너로 실행한다 — 코드가 바뀌면 `git push`만 하면 CI가 새 이미지를
Docker Hub에 올리고, VM은 다음 cron 실행 때 `docker pull`로 최신 이미지를 받는다.

## 구성

| 파일 | 역할 |
|---|---|
| `startup-script.sh` | VM 최초 부팅 시 Docker 설치, 디렉터리 준비 |
| `deploy.sh` | VM 생성 → Docker 설치까지 자동, 이후 Docker Hub 로그인은 수동 안내 |
| `check-logs.sh` | 실행 로그 / 누적 시그널 확인 |
| `delete.sh` | VM 삭제 (과금 중지) |
| `security-hardening.sh` | IAP 전용 SSH, 최소 권한 서비스 계정, Secure Boot, OS Login 적용 |

## 사전 준비

- `gcloud` CLI 설치 및 로그인 (`gcloud auth login`)
- 프로젝트에 Compute Engine API 활성화 (`gcloud services enable compute.googleapis.com`)
- 결제 계정이 연결된 프로젝트 (`gcloud config set project <PROJECT_ID>`)
- Docker Hub에 이미지가 이미 push되어 있을 것 (`.github/workflows/docker-build.yml`이 자동으로 함)
- 이미지가 private이면 VM용 **읽기 전용(Read-only)** Docker Hub 액세스 토큰

## 배포

```bash
cd cloud
./deploy.sh
```

VM 생성 + Docker 설치까지는 자동으로 되고, 마지막에 Docker Hub 로그인 관련
남은 수동 단계를 화면에 안내해준다 (자격 증명이라 스크립트에 넣지 않음).

`deploy.sh` 상단의 변수로 조정 가능:

- `ZONE` — 기본 `asia-northeast3-a` (서울)
- `MACHINE_TYPE` — 기본 `e2-small`. 무료 등급을 원하면 `ZONE`을
  `us-central1-a` 등으로, `MACHINE_TYPE`을 `e2-micro`로 바꾸면 됨
  (GCP Always Free 대상 리전+타입 조합)
- `SCAN_ARGS` — 기본 `--universe sp500` (S&P 500 전종목). 특정 종목만
  보고 싶으면 `--tickers TSLA AAPL NVDA` 등으로 변경
- `CRON_SCHEDULE` — 기본 UTC 기준 월~금 21:30 (미국 정규장 마감 직후,
  서머타임에 따라 실제 마감 시각과 ±1시간 오차 있음)

## 결과 확인

```bash
./check-logs.sh
```

또는 직접 SSH로 들어가서 확인:

```bash
gcloud compute ssh auto-trading-scanner --zone=asia-northeast3-a
cat /opt/auto-trading/signals_history.csv
```

## 비용 / 정리

`e2-small`을 서울 리전에 24시간 켜두면 대략 월 1~2만 원대 과금이 발생한다
(스캔 자체는 하루 몇 분이면 끝나므로, 비용을 줄이고 싶다면 `e2-micro` +
Always Free 리전 조합을 쓰거나, Cloud Scheduler + Cloud Run Jobs처럼
"실행할 때만 과금"되는 서버리스 구조로 바꾸는 걸 고려할 것).

다 쓰고 나면:

```bash
./delete.sh
```
