#!/usr/bin/env python3
"""
place_orders.py

signals_history.csv(run_and_log.py가 쌓는 파일)에 있는, 아직 주문을 넣지
않은 시그널을 사람이 검토하고 (원하면) 실제로 매수 주문을 넣는 수동 도구.

의도적으로 cron/run_and_log.py와 분리해뒀다 — 시그널을 찾는 것과 실제로
돈을 움직이는 것은 별개의 결정이어야 한다고 보기 때문이다. 이 스크립트는
자동으로 스케줄되지 않는다.

기본은 항상 dry-run이다. 실제로 주문을 넣으려면:
  1. --live 플래그
  2. 환경변수 TOSS_LIVE_TRADING=CONFIRM
  둘 다 필요하다.

사용 예 (저장소 루트에서 실행):
  # 미리보기만 (실주문 없음)
  ./.venv/bin/python -m toss.place_orders --account-seq 1

  # 실제로 주문 (이중 확인 필요)
  TOSS_LIVE_TRADING=CONFIRM ./.venv/bin/python -m toss.place_orders --account-seq 1 --live
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone

import pandas as pd

from strategies import position_size
from toss.exit_positions import open_positions
from toss.toss_client import TossClient

SIGNALS_FILE = "signals_history.csv"
ORDERS_LOG_FILE = "orders_log.csv"


def load_pending(signals_path: str, orders_log_path: str) -> pd.DataFrame:
    signals = pd.read_csv(signals_path, dtype=str)
    if not os.path.exists(orders_log_path):
        already_ordered = set()
    else:
        orders = pd.read_csv(orders_log_path, dtype=str)
        if "side" in orders.columns:  # 매도 기록은 "이미 매수함" 판단에서 제외
            orders = orders[orders["side"] == "BUY"]
        already_ordered = set(zip(orders["ticker"], orders["date"], orders["strategy"]))

    mask = ~signals.apply(lambda r: (r["ticker"], r["date"], r["strategy"]) in already_ordered, axis=1)
    return signals[mask]


def append_order_log(path: str, row: dict) -> None:
    df = pd.DataFrame([row])
    write_header = not os.path.exists(path)
    df.to_csv(path, mode="a", header=write_header, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="대기 중인 시그널을 검토하고 (선택적으로) 실제 주문 실행")
    parser.add_argument("--account-seq", type=int, required=True, help="토스증권 계좌 번호 (get_accounts로 확인)")
    parser.add_argument("--signals-file", default=SIGNALS_FILE)
    parser.add_argument("--orders-log", default=ORDERS_LOG_FILE)
    parser.add_argument("--order-type", default="LIMIT", choices=["LIMIT", "MARKET"])
    parser.add_argument("--live", action="store_true", help="실제로 주문을 넣는다 (기본은 미리보기만)")
    parser.add_argument(
        "--max-orders", type=int, default=None, help="한 번에 낼 최대 주문 수 (안전장치, 기본 제한 없음)"
    )
    parser.add_argument(
        "--max-age-days", type=int, default=3,
        help="시그널 발생 후 며칠까지 주문할지 (기본 3). 지정가는 시그널 당일 종가라 오래되면 시세와 어긋난다.",
    )
    parser.add_argument(
        "--currency", default="USD", help="매수가능금액을 조회할 통화 (기본 USD)"
    )
    parser.add_argument(
        "--assume-cash", type=float,
        help="실제 잔고 대신 이 금액이 있다고 가정 (dry-run 전용 미리보기, --live와 함께 못 씀)",
    )
    parser.add_argument(
        "--paper", action="store_true",
        help="주문은 보내지 않되 체결된 것처럼 orders-log에 기록 (모의매매 성적 추적용)",
    )
    parser.add_argument(
        "--risk-pct", type=float,
        help="지정하면 시그널 파일의 suggested_qty 대신 현재 자본으로 수량을 다시 계산한다. "
             "스캔 시점 자본에 수량이 고정되는 걸 막고, 한 시그널 파일로 여러 자본 규모를 굴릴 수 있다.",
    )
    args = parser.parse_args()

    if args.assume_cash is not None and args.live:
        parser.error("--assume-cash는 미리보기 전용입니다. --live와 함께 쓸 수 없습니다.")
    if args.paper and args.live:
        parser.error("--paper와 --live는 함께 쓸 수 없습니다.")

    if not os.path.exists(args.signals_file):
        print(f"{args.signals_file} 이 없습니다. run_and_log.py를 먼저 실행하세요.")
        return

    pending = load_pending(args.signals_file, args.orders_log)
    if pending.empty:
        print("주문 대기 중인 새 시그널이 없습니다.")
        return

    # 손절폭(진입가-손절가)이 좁은 순. 리스크 기반 사이징에서는 어느 종목이든
    # 트레이드당 리스크가 --risk-pct로 동일하므로, 손절폭이 좁을수록 같은 리스크에
    # 더 많은 자본이 투입된다 (현금이 놀지 않음). 자본이 적어 --max-orders로
    # 일부만 담을 때 알파벳 순으로 잘리는 걸 막는다.
    # ponytail: 손절폭이 좁으면 그만큼 잘 털리기도 한다. 승률이 떨어지면
    # 반대로(넓은 순) 뒤집어서 비교해볼 것.
    pending = pending.assign(
        _stop_dist=pd.to_numeric(pending["entry_price"], errors="coerce")
        - pd.to_numeric(pending["stop_price"], errors="coerce")
    ).sort_values("_stop_dist", na_position="last")

    if args.max_orders:
        pending = pending.head(args.max_orders)

    mode = "실주문" if args.live else ("모의매매(paper)" if args.paper else "미리보기(dry-run)")
    print(f"[{mode}] 대상 시그널 {len(pending)}건 (계좌 {args.account_seq})\n")

    client = TossClient()

    # 시그널마다 계좌 전액 기준으로 사이징되기 때문에, 실제 현금을 확인하지 않으면
    # 시그널 20건 = 매수가능금액의 20배를 주문하게 된다. 남은 현금을 차감해가며 막는다.
    if args.assume_cash is not None:
        # 이미 들고 있는 모의 포지션에 묶인 돈은 빼야 한다. 안 그러면 매일
        # 전액이 새로 생기는 셈이라 실제보다 훨씬 많이 산 성적이 나온다.
        tied = sum(p["quantity"] * p["entry_price"] for p in open_positions(args.orders_log))
        cash = args.assume_cash - tied
        equity_basis = args.assume_cash   # 사이징은 총자본 기준 (보유분 포함)
        print(f"매수가능금액: {cash:,.2f} {args.currency} "
              f"(가정 {args.assume_cash:,.0f} - 보유 {tied:,.2f}, 실제 잔고 아님)\n")
    else:
        cash = client.get_buying_power(args.account_seq, args.currency)
        equity_basis = cash
        print(f"매수가능금액: {cash:,.2f} {args.currency}\n")

    today = datetime.now(timezone.utc).date()
    for _, row in pending.iterrows():
        age = (today - date.fromisoformat(row["date"])).days
        if age > args.max_age_days:
            print(f"  [건너뜀] {row['ticker']} {row['date']}: {age}일 지난 시그널 "
                  f"(지정가 {row['entry_price']}는 시그널 당일 종가라 현재 시세와 다를 수 있음).")
            continue
        if args.risk_pct is not None:
            # 스캔 때 박힌 수량 대신 지금 자본으로 다시 계산한다.
            qty = position_size(
                float(row["entry_price"]), float(row["stop_price"]), equity_basis, args.risk_pct
            )
        else:
            qty = row.get("suggested_qty")
        if pd.isna(qty) or qty in (None, "", "None"):
            print(f"  [건너뜀] {row['ticker']} {row['date']}: 권장 수량이 없습니다 "
                  f"(run_and_log.py를 --equity/--risk-pct와 함께 실행해야 계산됩니다).")
            continue
        qty = int(float(qty))
        if qty <= 0:
            # 계좌 자산 대비 손절폭이 커서 1주도 리스크 한도에 안 맞는 경우.
            # 0주 주문은 거래소에서 거부되므로 아예 보내지 않는다.
            print(f"  [건너뜀] {row['ticker']} {row['date']}: 권장 수량 0주 "
                  f"(진입가 {row['entry_price']}, 손절가 {row['stop_price']} — "
                  f"현재 계좌 자산과 리스크 비율로는 1주도 담을 수 없음).")
            continue
        price = float(row["entry_price"])

        cost = qty * price
        if cost > cash:
            print(f"  [건너뜀] {row['ticker']}: {qty}주 x {price} = {cost:,.2f} > 남은 현금 {cash:,.2f}")
            continue
        cash -= cost

        result = client.place_order(
            account_seq=args.account_seq,
            symbol=row["ticker"],
            side="BUY",
            quantity=qty,
            price=price,
            order_type=args.order_type,
            dry_run=not args.live,
        )

        print(f"  {row['ticker']} {row['date']} 진입 {price} x {qty}주 → {result}")

        if args.live or args.paper:
            append_order_log(
                args.orders_log,
                {
                    "ticker": row["ticker"],
                    "date": row["date"],
                    "strategy": row["strategy"],
                    "side": "BUY",
                    "quantity": qty,
                    "price": price,
                    # exit_positions.py가 청산 판단에 쓴다 (손절가 / 보유일수 계산).
                    "stop_price": row.get("stop_price"),
                    "filled_at": today.isoformat(),
                    "result": str(result),
                },
            )

    if not args.live:
        print("\n실제로 주문을 넣지 않았습니다. 실행하려면 --live 와 "
              "TOSS_LIVE_TRADING=CONFIRM 환경변수를 함께 지정하세요.")


if __name__ == "__main__":
    sys.exit(main())
