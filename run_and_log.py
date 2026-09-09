#!/usr/bin/env python3
"""
run_and_log.py

strategies.py의 최신-시그널 탐지 함수를 실행해서, 가장 최근 봉에 새로 뜬
진입 시그널만 signals_history.csv에 누적 기록한다 (이미 본 시그널은 다시
기록하지 않음).

실제 주문은 어디에도 넣지 않는다 — 이 파일이 하는 일은 "오늘 새로 이 조건에
맞은 종목이 있다"를 기록하는 것까지다. --equity/--risk-pct를 주면 ATR 손절
폭을 기준으로 계좌 리스크 대비 권장 수량까지 같이 계산해서 기록하지만,
이것도 참고용 숫자일 뿐 자동으로 매수/매도를 실행하지 않는다.

사용 예:
  ./.venv/bin/python run_and_log.py --universe sp500
  ./.venv/bin/python run_and_log.py --tickers TSLA AAPL NVDA --equity 10000000 --risk-pct 1.0
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from scanner import add_ticker_args, collect_tickers, fetch_history
from strategies import STRATEGIES, add_strategy_args, build_params, position_size

HISTORY_FILE = "signals_history.csv"


def load_seen(path: str) -> set[tuple[str, str, str]]:
    if not os.path.exists(path):
        return set()
    df = pd.read_csv(path, dtype=str)
    if "strategy" not in df.columns:
        df["strategy"] = "legacy"  # 이전 스키마(전략 컬럼 없음) 호환
    return set(zip(df["ticker"], df["date"], df["strategy"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="최신 시그널 탐지 + 신규 시그널만 누적 로그에 기록 (실주문 없음)")
    add_ticker_args(parser)
    add_strategy_args(parser)
    parser.add_argument("--history", default="2y")
    parser.add_argument("--history-file", default=HISTORY_FILE)
    parser.add_argument("--equity", type=float, help="계좌 평가금액 (지정하면 권장 수량도 계산)")
    parser.add_argument("--account-seq", type=int, help="--equity 대신 토스 계좌의 실제 매수가능금액으로 사이징")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--risk-pct", type=float, default=1.0, help="트레이드당 리스크 비율 (기본 1%%)")
    args = parser.parse_args()

    tickers = collect_tickers(args)
    if not tickers:
        parser.error("--tickers / --tickers-file / --universe 중 하나는 지정해야 합니다.")

    equity = args.equity
    if args.account_seq is not None:
        from toss.toss_client import TossClient  # noqa: PLC0415

        equity = TossClient().get_buying_power(args.account_seq, args.currency)
        print(f"[사이징] 토스 매수가능금액 {equity:,.2f} {args.currency} 기준")

    params = build_params(args.strategy, args)
    _, _, latest_signal_fn = STRATEGIES[args.strategy]

    run_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen = load_seen(args.history_file)
    new_rows = []

    print(f"[{run_time}] 전략={args.strategy} {len(tickers)}개 종목 스캔 시작")
    for ticker in tickers:
        df = fetch_history(ticker, args.history)
        if df is None or len(df) < 30:
            continue
        sig = latest_signal_fn(df, params)
        if sig is None:
            continue
        key = (ticker, str(sig["date"]), args.strategy)
        if key in seen:
            continue
        seen.add(key)
        qty = position_size(sig["entry_price"], sig.get("stop_price"), equity, args.risk_pct)
        new_rows.append(
            {
                "detected_at": run_time,
                "ticker": ticker,
                "date": sig["date"],
                "strategy": args.strategy,
                "entry_price": sig["entry_price"],
                "stop_price": sig.get("stop_price"),
                "suggested_qty": qty,
            }
        )

    if not new_rows:
        print(f"[{run_time}] 새로운 시그널 없음")
        return

    new_df = pd.DataFrame(new_rows)
    write_header = not os.path.exists(args.history_file)
    new_df.to_csv(args.history_file, mode="a", header=write_header, index=False)

    print(f"[{run_time}] 새 시그널 {len(new_rows)}건 기록 (실주문 없음, 기록만 함):")
    for row in new_rows:
        qty_str = f", 권장수량 {row['suggested_qty']}주" if row["suggested_qty"] is not None else ""
        stop_str = f", 손절 {row['stop_price']}" if row["stop_price"] is not None else ""
        print(f"  - {row['ticker']} {row['date']} 진입 {row['entry_price']}{stop_str}{qty_str}")


if __name__ == "__main__":
    sys.exit(main())
