#!/usr/bin/env python3
"""
weekly_ma/paper.py

40주선 돌파 전략 모의매매. 주 1회(토요일 새벽, 금요일 마감 뒤) 실행한다.

  1. 보유 종목 중 지난주 종가가 40주선 아래면 매도
  2. 지난주에 40주선을 아래→위로 돌파한 종목을 거래량 배수 큰 순으로 빈 슬롯만큼 매수

장부 형식은 RSI(2) 모의매매(toss/place_orders.py)와 같아서 dashboard/report.py가
그대로 읽는다. 실주문 경로는 없다 — 모의매매 전용.

사용 (저장소 루트):
  python -m weekly_ma.paper --orders-log paper_orders_ma40w_20000.csv --capital 20000
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

from toss.exit_positions import ledger_cash_flow, open_positions
from weekly_ma.strategy import MA_WEEKS, add_signals, to_weekly

STRATEGY = "ma40w"


def decide(weekly: dict[str, pd.DataFrame], held: list[dict], cash: float,
           capital_basis: float, slots: int) -> tuple[list[dict], list[dict]]:
    """완성된 주봉(add_signals 결과)만 보고 매도/매수 목록을 만든다. I/O 없음.

    capital_basis: 슬롯 크기 계산용 자산 = 현금 + 보유분 원가 (place_orders.py와 같은 기준).
    """
    sells = []
    for p in held:
        w = weekly.get(p["ticker"])
        if w is None or w.empty or pd.isna(w["ma"].iloc[-1]):
            continue                                    # 시세가 없으면 판단 보류 (다음 주에 다시)
        last = w.iloc[-1]
        if last["Close"] < last["ma"]:
            sells.append({**p, "price": round(float(last["Close"]), 2), "day": last["last_day"],
                          "reason": f"ma_cross_down | 종가 {last['Close']:.2f} < 40주선 {last['ma']:.2f}"})

    sold = {s["ticker"] for s in sells}
    keep = [p for p in held if p["ticker"] not in sold]
    cash += sum(s["quantity"] * s["price"] for s in sells)

    # 매수 후보: 이번 주에 막 돌파한 종목. 같은 주 돌파가 슬롯보다 많으면(평균 주 20개)
    # 거래량 배수 큰 순 — 백테스트에서 랜덤 순서보다 나았다 (README 참고).
    cands = [(t, w.iloc[-1]) for t, w in weekly.items()
             if not w.empty and bool(w["cross_up"].iloc[-1]) and t not in {p["ticker"] for p in held}]
    cands.sort(key=lambda c: -(c[1]["vol_ratio"] if pd.notna(c[1]["vol_ratio"]) else 0))

    buys, size = [], capital_basis / slots
    for t, row in cands:
        if len(keep) + len(buys) >= slots:
            break
        px = round(float(row["Close"]), 2)
        qty = int(size // px)
        if qty < 1 or qty * px > cash:
            continue                                    # 한 주가 슬롯보다 비싸거나 현금 부족
        cash -= qty * px
        buys.append({"ticker": t, "quantity": qty, "price": px, "ma": round(float(row["ma"]), 2),
                     "day": row["last_day"], "vol_ratio": round(float(row["vol_ratio"]), 2)})
    return sells, buys


def main() -> None:
    ap = argparse.ArgumentParser(description="40주선 돌파 전략 모의매매 (주 1회)")
    ap.add_argument("--orders-log", required=True)
    ap.add_argument("--capital", type=float, required=True, help="가정 시작 자본")
    ap.add_argument("--slots", type=int, default=20, help="동시 보유 종목 수 (자본을 균등 분할)")
    ap.add_argument("--tickers", nargs="+", help="생략하면 S&P 500 전체")
    args = ap.parse_args()

    import yfinance as yf
    from scanner import fetch_sp500_tickers

    today = datetime.now(timezone.utc).date()
    tickers = args.tickers or fetch_sp500_tickers()
    raw = yf.download(tickers, period="2y", interval="1d", progress=False,
                      auto_adjust=True, group_by="ticker", threads=True)
    weekly = {}
    for t in tickers:
        if t not in raw.columns.get_level_values(0):
            continue
        d = raw[t].dropna(subset=["Close"])
        if len(d) > MA_WEEKS * 5:
            weekly[t] = add_signals(to_weekly(d, asof=today))
    print(f"[{STRATEGY}] {today} 기준 — 시세 {len(weekly)}/{len(tickers)}종목")

    held = open_positions(args.orders_log)
    cash = args.capital + ledger_cash_flow(args.orders_log)
    basis = cash + sum(p["quantity"] * p["entry_price"] for p in held)
    sells, buys = decide(weekly, held, cash, basis, args.slots)

    rows = [{"ticker": s["ticker"], "date": s["day"].isoformat(), "strategy": STRATEGY, "side": "SELL",
             "quantity": s["quantity"], "price": s["price"], "stop_price": s["stop_price"],
             "filled_at": s["day"].isoformat(), "result": s["reason"]} for s in sells]
    rows += [{"ticker": b["ticker"], "date": b["day"].isoformat(), "strategy": STRATEGY, "side": "BUY",
              "quantity": b["quantity"], "price": b["price"], "stop_price": b["ma"],
              "filled_at": b["day"].isoformat(), "result": f"paper | 거래량 {b['vol_ratio']}배"} for b in buys]
    for r in rows:
        print(f"  [{r['side']}] {r['ticker']} {r['quantity']}주 @ {r['price']} — {r['result']}")
    if rows:
        pd.DataFrame(rows).to_csv(args.orders_log, mode="a", index=False,
                                  header=not os.path.exists(args.orders_log))
    print(f"  보유 {len(held) - len(sells) + len(buys)}/{args.slots}, 매도 {len(sells)}, 매수 {len(buys)}")


if __name__ == "__main__":
    main()
