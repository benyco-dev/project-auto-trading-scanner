#!/usr/bin/env python3
"""
exit_positions.py

place_orders.py로 산 포지션을 전략의 청산 규칙에 따라 매도하는 도구.
매수(place_orders.py)와 짝을 이루는 반대편이다 — 이게 없으면 ATR 손절이
전략 문서에만 있고 실제로는 실행되지 않는다.

청산 규칙 (rsi2_trend 기준):
  1. 손절: 당일 저가가 매수 시 정한 손절가 이하로 내려감
  2. 익절: 종가가 10일 이동평균을 다시 상회
  3. 시간: 매수 후 --max-hold-days 거래일 경과

보유 상태는 orders_log.csv의 BUY/SELL 수량 차이로 계산한다.
# ponytail: 브로커 보유내역(get_holdings)이 아니라 자체 로그를 기준으로 삼는다.
# 도구 밖에서 수동 매매하면 어긋난다. get_holdings 응답 스키마가 확인되면
# 그쪽을 기준으로 바꿀 것.

기본은 dry-run. 실제 매도하려면 --live 와 TOSS_LIVE_TRADING=CONFIRM 둘 다 필요.

사용 예 (저장소 루트에서 실행):
  python -m toss.exit_positions --account-seq 1
  TOSS_LIVE_TRADING=CONFIRM python -m toss.exit_positions --account-seq 1 --live
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone

import pandas as pd

from scanner import fetch_history
from toss.toss_client import TossClient

ORDERS_LOG_FILE = "orders_log.csv"


def open_positions(orders_log_path: str) -> list[dict]:
    """orders_log.csv의 BUY/SELL 수량 차이로 아직 들고 있는 포지션을 계산한다."""
    if not os.path.exists(orders_log_path):
        return []
    orders = pd.read_csv(orders_log_path)
    if orders.empty:
        return []
    if "side" not in orders.columns:
        orders["side"] = "BUY"  # 이전 스키마(매수만 기록하던 시절) 호환

    positions = []
    for ticker, rows in orders.groupby("ticker"):
        buys = rows[rows["side"] == "BUY"]
        held = int(buys["quantity"].sum() - rows[rows["side"] == "SELL"]["quantity"].sum())
        if held <= 0 or buys.empty:
            continue
        last_buy = buys.iloc[-1]
        positions.append(
            {
                "ticker": ticker,
                "quantity": held,
                "entry_price": float(last_buy["price"]),
                "stop_price": float(last_buy["stop_price"]) if pd.notna(last_buy.get("stop_price")) else None,
                "filled_at": str(last_buy.get("filled_at", "")),
                "strategy": last_buy.get("strategy", ""),
            }
        )
    return positions


def exit_reason(df: pd.DataFrame, pos: dict, exit_sma: int, max_hold_days: int, today: date) -> tuple[str, float] | None:
    """청산해야 하면 (사유, 기준가), 아니면 None."""
    close, low = df["Close"], df["Low"]
    last_close = round(float(close.iloc[-1]), 2)  # 소수점 그대로 보내면 거부될 수 있다

    if pos["stop_price"] is not None and float(low.iloc[-1]) <= pos["stop_price"]:
        return "stop", last_close

    sma = close.rolling(exit_sma).mean().iloc[-1]
    if pd.notna(sma) and last_close > float(sma):
        return "target_sma", last_close

    if pos["filled_at"]:
        held_days = (today - date.fromisoformat(pos["filled_at"])).days
        if held_days >= max_hold_days:
            return f"time({held_days}일)", last_close

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="보유 포지션을 전략 청산 규칙에 따라 매도 (기본 dry-run)")
    parser.add_argument("--account-seq", type=int, required=True)
    parser.add_argument("--orders-log", default=ORDERS_LOG_FILE)
    parser.add_argument("--order-type", default="LIMIT", choices=["LIMIT", "MARKET"])
    parser.add_argument("--exit-sma", type=int, default=10)
    parser.add_argument("--max-hold-days", type=int, default=20)
    parser.add_argument("--live", action="store_true", help="실제로 매도 주문을 넣는다")
    parser.add_argument("--paper", action="store_true", help="주문 없이 체결된 것처럼 기록 (모의매매)")
    args = parser.parse_args()

    positions = open_positions(args.orders_log)
    if not positions:
        print("보유 중인 포지션이 없습니다.")
        return

    mode = "실매도" if args.live else ("모의매매(paper)" if args.paper else "미리보기(dry-run)")
    print(f"[{mode}] 보유 포지션 {len(positions)}건 (계좌 {args.account_seq})\n")

    client = TossClient()
    today = datetime.now(timezone.utc).date()

    for pos in positions:
        df = fetch_history(pos["ticker"], "3mo")
        if df is None or df.empty:
            print(f"  [보류] {pos['ticker']}: 시세를 가져오지 못했습니다.")
            continue

        decision = exit_reason(df, pos, args.exit_sma, args.max_hold_days, today)
        if decision is None:
            print(f"  [유지] {pos['ticker']} {pos['quantity']}주: 청산 조건 미충족 "
                  f"(종가 {float(df['Close'].iloc[-1]):.2f}, 손절가 {pos['stop_price']})")
            continue

        reason, price = decision
        result = client.place_order(
            account_seq=args.account_seq,
            symbol=pos["ticker"],
            side="SELL",
            quantity=pos["quantity"],
            price=price,
            order_type=args.order_type,
            dry_run=not args.live,
        )
        print(f"  [매도] {pos['ticker']} {pos['quantity']}주 @ {price} — 사유: {reason} → {result}")

        if args.live or args.paper:
            pd.DataFrame([{
                "ticker": pos["ticker"],
                "date": today.isoformat(),
                "strategy": pos["strategy"],
                "side": "SELL",
                "quantity": pos["quantity"],
                "price": price,
                "stop_price": pos["stop_price"],
                "filled_at": today.isoformat(),
                "result": f"{reason} | {result}",
            }]).to_csv(args.orders_log, mode="a", header=False, index=False)

    if not args.live:
        print("\n실제로 매도하지 않았습니다. 실행하려면 --live 와 "
              "TOSS_LIVE_TRADING=CONFIRM 환경변수를 함께 지정하세요.")


if __name__ == "__main__":
    sys.exit(main())
