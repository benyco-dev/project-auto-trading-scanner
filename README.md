# auto-trading-scanner

미국 주식에서 매매 시그널을 스캔해서 기록하고, (수동 실행 시) 토스증권 API로 주문까지 내는
개인 프로젝트. 유튜브 자동매매 강의([1강](https://www.youtube.com/watch?v=7VG7ugLu6qs))를
출발점으로 삼았지만, 전략·배포·주문 구조를 전부 다르게 가져갔다 —
무엇을 왜 바꿨는지는 [맨 아래 대응표](#원본-강의-시리즈와의-대응-관계) 참고.

만들면서 나온 결과들(수수료가 총수익의 40%를 먹던 것, 백테스트와 포트폴리오 시뮬레이션의
차이, 자본 규모가 수익률에 거의 영향을 주지 않는다는 것, 표본 편차가 설정 차이보다 9배 크다는 것)은
블로그 3부작으로 따로 정리했다.

---

미국 주식에서 매매 시그널을 스캔하는 스크립트. 기본 전략은
**RSI(2) 평균회귀 + 200일 추세 필터 + ATR 손절/포지션 사이징**
([`strategies.py`](strategies.py)의 `rsi2_trend`) — 근거와 백테스트 결과는 아래 참고.

## 설치

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## 사용법

```bash
# 특정 종목만 (기본 전략: rsi2_trend)
./.venv/bin/python scanner.py --tickers TSLA AAPL NVDA

# 파일에 적힌 종목 목록
./.venv/bin/python scanner.py --tickers-file tickers.txt

# S&P 500 전종목 스캔 (몇 분 소요)
./.venv/bin/python scanner.py --universe sp500

# 영상 1강의 원본 로직(MACD+이격도)으로 비교
./.venv/bin/python scanner.py --tickers TSLA --strategy legacy

# rsi2_trend 파라미터 조정
./.venv/bin/python scanner.py --tickers TSLA --rsi-threshold 5 --atr-mult 3
```

결과는 콘솔에 출력되고, `scan_results.csv`로도 저장된다.

## 매매 전략

### 기본값: `rsi2_trend`

- **추세 필터**: 종가가 200일 이동평균 위 (장기 상승 추세일 때만 매수 고려)
- **진입**: RSI(2)가 5 이하로 급락 (상승 추세 중의 단기 눌림목). Connors 원본 기준값이며,
  10으로 완화하면 트레이드 수는 2배가 되지만 백테스트상 profit factor는 1.47→1.42로 낮아진다.
- **청산**: 종가가 10일 이동평균을 다시 넘으면 익절 / 진입가 대비 ATR(14) x 2.5 하락하면 손절 / 20거래일 지나면 시간 청산
  (Connors 원본은 SMA5지만, 왕복 수수료 0.2%가 트레이드당 고정이라 짧게 끊으면 수익의 절반이 수수료로 나간다 — `portfolio_sim.py` 참고)
- **사이징**: `--equity`(계좌 평가금액)와 `--risk-pct`(트레이드당 리스크, 기본 1%)를 주면
  `수량 = (계좌자산 x 리스크비율) / (진입가 - 손절가)` 로 권장 수량까지 계산

Larry Connors & Cesar Alvarez의 RSI(2) 평균회귀 전략(추세 안에서 단기 눌림목 매수)에
Turtle Trading 식 ATR 손절/사이징을 결합한 조합이다. 화려한 전략은 아니지만, 개별 규칙 하나하나가
가장 많이 검증되고 문서화된 축에 속한다는 이유로 기본값으로 선택했다.

### 원본: `legacy` (영상 1강)

MACD(5,25,9) 골든크로스 + 15일 이격도 85% 이하. **청산/손절 규칙이 없다** — 영상에서도
언제 팔지에 대한 규칙은 다루지 않았다. 백테스트에서는 비교를 위해 "MACD 데드크로스 또는
30거래일 시간청산"을 임의로 붙였다.

### 백테스트 비교 (S&P 500 무작위 100종목, 최근 5년)

```bash
./.venv/bin/python backtest.py --universe sp500 --sample 100 --history 5y
```

| 전략 | 트레이드 수 | 승률 | 평균 수익률 | 평균 익절 | 평균 손절 | Profit Factor | 평균 보유일 | 최악의 트레이드 |
|---|---|---|---|---|---|---|---|---|
| legacy (손절 없음) | 338 | 36.1% | +1.54% | +11.56% | -4.11% | 1.59 | 11.6일 | -21.0% |
| **rsi2_trend** | 2799 | **67.8%** | +0.37% | +2.02% | -3.10% | 1.37 | 3.5일 | -15.9% |

**정직하게 읽으면**: 트레이드당 profit factor만 보면 legacy가 오히려 근소하게 앞선다 —
가끔 크게 먹는 트레이드 덕분이다. 하지만 legacy는 **명시적 손절이 전혀 없어서** 이 백테스트에
안 잡힌 더 큰 손실(급락 갭 등)에 그대로 노출된다. rsi2_trend는 모든 트레이드에 진입과 동시에
손절가가 정해져 있고, 승률이 훨씬 높고 보유 기간이 짧아 자본 회전이 빠르다 — 사람이 지켜보지
않는 자동화 시스템에서는 "가끔 크게 먹지만 손절이 없는" 전략보다 "매 트레이드마다 리스크가
정의된" 전략이 안전하다고 판단해 기본값으로 삼았다.

**한계**: 거래비용/슬리피지 미반영, 현재 S&P500 구성종목 기준(생존편향), 여러 종목을 동시에
들고 있는 상황의 포트폴리오 복리 효과는 계산하지 않고 트레이드 단위 통계만 봤다. 과거 성과가
미래 수익을 보장하지 않는다 — 실거래 전에 표본을 늘려서(`--sample` 상향) 직접 검증해볼 것을 권장.

## 매일 자동으로 돌리기 (GCE)

`run_and_log.py`는 각 종목의 **가장 최근 봉에 새로 뜬** 진입 시그널만 감지해서
`signals_history.csv`에 누적 기록한다 (이미 본 시그널은 다시 기록하지 않음, 실주문 없음).
`--equity`/`--risk-pct`를 주면 권장 수량도 같이 기록된다.

배포 파이프라인: `git push` → GitHub Actions가 [`Dockerfile`](Dockerfile)로 이미지를 빌드해
Docker Hub에 올림 → Google Compute Engine VM이 cron으로 매일 그 이미지를 pull + run.
VM 세팅 스크립트는 [`cloud/`](cloud), 자세한 내용은 [`cloud/README.md`](cloud/README.md) 참고.

## 실제 주문 실행 (토스증권 API)

[`toss/`](toss) 에 토스증권 Open API 클라이언트가 있다. API 키 발급 방법은
[`toss/SETUP.md`](toss/SETUP.md) 참고.

**의도적으로 cron에 연결하지 않았다** — 시그널을 찾는 것과 실제로 돈을 움직이는 것은
분리된 결정이어야 한다고 봐서, `toss/place_orders.py`는 사람이 직접 실행하는 수동 도구로
남겨뒀다. 기본은 항상 dry-run(미리보기)이고, 실제 주문은 `--live` 플래그 +
`TOSS_LIVE_TRADING=CONFIRM` 환경변수 두 가지를 모두 줘야 나간다 (이중 안전장치).

```bash
# 미리보기만 (실주문 없음, 자격 증명 없어도 동작)
./.venv/bin/python -m toss.place_orders --account-seq 1

# 실제 주문
TOSS_LIVE_TRADING=CONFIRM ./.venv/bin/python -m toss.place_orders --account-seq 1 --live
```

## 참고

- 데이터는 `yfinance`를 통해 무료로 가져온다 (지연 시세, 실거래용 아님).
- 실제 매수/매도는 `toss/`에 구현되어 있지만 자동 실행되지 않는다. cron(`run_and_log.py`)은
  시그널을 찾아 기록하는 것까지만 하고, 주문은 사람이 `place_orders.py`를 직접 실행할 때만 나간다.
- 투자 조언이 아니다. 위 백테스트는 참고용 통계일 뿐, 실거래 결과를 보장하지 않는다.

## 원본 강의 시리즈와의 대응 관계

출처: 유튜브 "자동매매" 강의 시리즈 (1강: https://www.youtube.com/watch?v=7VG7ugLu6qs).
이 시리즈를 참고해서 시작했지만, 세 가지를 의도적으로 다르게 구현했다:
텔레그램 알림 대신 로그 파일 누적, GitHub Actions 대신 GCE VM cron, 그리고 영상의 MACD+이격도
로직 대신 위 백테스트로 검증한 `rsi2_trend`를 기본 전략으로 사용.

| 강의 | 강의에서 하는 것 | 이 repo에서 대신하는 것 |
|---|---|---|
| 1강 | MACD+이격도 매매 로직 정의 + `scanner.py` 작성 | [`strategies.py`](strategies.py)의 `legacy`로 보존, 기본값은 `rsi2_trend`로 교체 |
| 2강 | GitHub Actions 등록 + 텔레그램 봇 알림 | [`run_and_log.py`](run_and_log.py)의 `signals_history.csv` 누적 로그 (알림 없음). 자동 실행은 GitHub Actions가 아니라 [`cloud/`](cloud) GCE VM cron이 담당, 배포는 Docker Hub 이미지를 pull하는 방식 |
| 3강 (예고) | Cloudflare Worker로 평일 정오 자동 트리거 | 이미 GCE cron이 스케줄링을 담당하므로 불필요 |
| 이후 (예상) | 토스증권 API로 실제 매수/매도 실행 | [`toss/`](toss) 구현 완료. 단, cron에는 안 붙이고 사람이 `place_orders.py`를 직접 실행할 때만 주문이 나가도록 분리 |

2강까지는 종목 리스트를 약 1,280개(강의 자료 수동 목록)로 스캔하지만, 이 repo는
`--universe sp500`으로 위키피디아에서 S&P 500 종목을 그때그때 가져와 스캔한다.
더 넓은 범위가 필요하면 `--tickers-file`에 원하는 티커 목록을 넣어 대체 가능.
