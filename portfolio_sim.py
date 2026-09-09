#!/usr/bin/env python3
"""
portfolio_sim.py

"이 자본이면 이 전략을 굴릴 수 있나"에 답하기 위한 포트폴리오 시뮬레이터.

backtest.py는 트레이드 단위 통계(승률, profit factor)만 본다. 그건 자본이
무한하다는 가정이라, 자본이 적어서 시그널을 못 담는 상황을 보여주지 못한다.
이 스크립트는 현금과 --max-orders 제약을 걸고 실제로 담을 수 있는 것만
담아서 자본 곡선을 만든다.

사용 예:
  python portfolio_sim.py --universe sp500 --sample 150 --history 3y \\
      --capital 2000 8000 --max-orders 3
"""

from __future__ import annotations

import argparse
import random

import pandas as pd

from scanner import add_ticker_args, collect_tickers, fetch_history
from strategies import RSI2Params, rsi2_trend_trades


def collect_trades(tickers: list[str], history: str, params: RSI2Params) -> pd.DataFrame:
    out = []
    for n, t in enumerate(tickers, 1):
        if n % 50 == 0:
            print(f"  데이터 수집 {n}/{len(tickers)}...")
        df = fetch_history(t, history)
        if df is None or len(df) < 210:
            continue
        tr = rsi2_trend_trades(df, params)
        if not tr.empty:
            tr.insert(0, "ticker", t)
            out.append(tr)
    trades = pd.concat(out, ignore_index=True)
    return trades.sort_values("entry_date").reset_index(drop=True)


def simulate(trades: pd.DataFrame, capital: float, risk_pct: float, max_orders: int,
             commission_pct: float = 0.1, max_position_pct: float = 0.0) -> dict:
    """실제 현금/포지션 제약을 걸고 자본 곡선을 만든다.

    max_position_pct: 한 종목에 넣을 수 있는 자본 비중 상한 (0이면 무제한).
    손절폭이 좁으면 리스크 기반 수량이 커져서 몇 종목에 자본이 쏠리는 걸 막는다.
    """
    cash = capital
    open_pos = []       # {ticker, qty, entry, exit_date, exit_price}
    equity_curve = []
    held_per_day = []
    taken = skipped_cash = skipped_size = capped = 0
    fee = commission_pct / 100.0
    total_fees = 0.0

    for day in pd.date_range(trades.entry_date.min(), trades.exit_date.max(), freq="B"):
        d = day.date()

        for p in [p for p in open_pos if p["exit_date"] <= d]:      # 먼저 청산 (현금 회수)
            proceeds = p["qty"] * p["exit_price"]
            f = proceeds * fee
            cash += proceeds - f
            total_fees += f
            open_pos.remove(p)

        todays = trades[trades.entry_date == d]
        # 손절폭 좁은 순 = place_orders.py와 같은 우선순위
        todays = todays.assign(_sd=todays.entry_price - todays.stop_price).sort_values("_sd")

        equity_now = cash + sum(p["qty"] * p["entry"] for p in open_pos)
        for _, t in todays.head(max_orders).iterrows():
            qty = int((equity_now * risk_pct / 100) // (t.entry_price - t.stop_price))
            if max_position_pct:
                cap_qty = int(equity_now * max_position_pct / 100 // t.entry_price)
                if cap_qty < qty:
                    qty = cap_qty
                    capped += 1
            if qty < 1:
                skipped_size += 1
                continue
            cost = qty * t.entry_price
            if cost * (1 + fee) > cash:
                skipped_cash += 1
                continue
            f = cost * fee
            cash -= cost + f
            total_fees += f
            open_pos.append({"ticker": t.ticker, "qty": qty, "entry": t.entry_price,
                             "exit_date": t.exit_date, "exit_price": t.exit_price})
            taken += 1

        equity_curve.append(cash + sum(p["qty"] * p["entry"] for p in open_pos))
        held_per_day.append(len(open_pos))

    eq = pd.Series(equity_curve)
    dd = (eq / eq.cummax() - 1) * 100
    return {
        "자본": f"${capital:,.0f}",
        "상한%": max_position_pct or "없음",
        "총수익률%": round((eq.iloc[-1] / capital - 1) * 100, 1),
        "최대낙폭%": round(dd.min(), 1),
        "평균동시보유": round(sum(held_per_day) / len(held_per_day), 1),
        "체결": taken,
        "상한적중": capped,
        "돈부족스킵": skipped_cash,
        "체결률%": round(taken / (taken + skipped_cash + skipped_size) * 100, 1),
        "수수료/자본%": round(total_fees / capital * 100, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="자본 규모별 포트폴리오 시뮬레이션")
    add_ticker_args(parser)
    parser.add_argument("--sample", type=int)
    parser.add_argument("--history", default="3y")
    parser.add_argument("--capital", type=float, nargs="+", default=[2000, 8000])
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--max-orders", type=int, nargs="+", default=[3])
    parser.add_argument("--max-position-pct", type=float, nargs="+", default=[0],
                        help="한 종목 최대 비중(%%). 0이면 무제한. 여러 값을 주면 비교한다.")
    parser.add_argument("--commission-pct", type=float, default=0.1, help="매매당 수수료율 (토스 미국주식 0.1%%)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tickers = collect_tickers(args)
    if args.sample and args.sample < len(tickers):
        random.seed(args.seed)
        tickers = sorted(random.sample(tickers, args.sample))

    print(f"[시뮬레이션] {len(tickers)}종목 x {args.history}, 리스크 {args.risk_pct}%/트레이드\n")
    trades = collect_trades(tickers, args.history, RSI2Params())
    print(f"\n전체 시그널 {len(trades)}건 ({trades.entry_date.min()} ~ {trades.entry_date.max()})\n")

    rows = [simulate(trades, c, args.risk_pct, m, args.commission_pct, cap)
            for c in args.capital for m in args.max_orders for cap in args.max_position_pct]
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\n※ 매매수수료 {args.commission_pct}% 반영. 배당/세금/환전 미반영, 현재 S&P500 구성종목 기준(생존편향).")


if __name__ == "__main__":
    main()
