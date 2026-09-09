FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scanner.py strategies.py run_and_log.py backtest.py ./
COPY toss/ ./toss/

RUN useradd --create-home --uid 1000 scanner \
    && mkdir -p /data \
    && chown -R scanner:scanner /app /data
USER scanner

# 상태 파일(signals_history.csv, scan_results.csv 등)은 여기 씁니다.
# 컨테이너는 매번 새로 뜨므로, 실행할 때 이 경로를 볼륨으로 마운트해서 영속시킬 것.
WORKDIR /data
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 기본 동작: 시그널 스캔 (cron/스케줄러가 이 컨테이너를 주기적으로 실행하는 걸 전제로 함).
# 다른 스크립트를 돌리려면 --entrypoint로 덮어쓰면 됨, 예:
#   docker run --entrypoint python IMAGE /app/scanner.py --tickers TSLA
#   docker run --entrypoint python IMAGE -m toss.place_orders --account-seq 1
# (toss/는 패키지라 파일 경로가 아니라 -m으로 실행한다. PYTHONPATH=/app이 해석해준다.)
ENTRYPOINT ["python", "/app/run_and_log.py"]
CMD ["--universe", "sp500"]
