#!/usr/bin/env python3
"""
backtest.py

strategies.py에 있는 두 전략(legacy_macd_disparity, rsi2_trend)을 실제
과거 데이터에 돌려서 트레이드 단위 성과를 비교한다.

사용 예:
  ./.venv/bin/python backtest.py --tickers-file sample_tickers.txt --history 5y
  ./.venv/bin/python backtest.py --universe sp500 --sample 100 --history 5y

주의: 이건 참고용 백테스트다. 거래비용/슬리피지/생존편향(현재 S&P500 구성
종목으로만 과거를 돌리는 것) 등을 반영하지 않았고, 과거 성과가 미래 수익을
보장하지 않는다.
"""

from __future__ import annotations

import argparse
import random

import pandas as pd

from scanner import add_ticker_args, collect_tickers, fetch_history
from strategies import LegacyParams, RSI2Params, legacy_macd_disparity_trades, rsi2_trend_trades


def summarize(trades: pd.DataFrame, label: str) -> dict:
    # 트레이드들이 서로 다른 종목에서 동시다발적으로 발생하기 때문에, 이걸
    # 하나의 순차적 자본 곡선으로 복리 계산하는 건 실제와 맞지 않는 착시
    # 숫자를 만든다 (동시 보유를 무시하고 전액을 매 트레이드에 몰빵한 것처럼
    # 계산됨). 그래서 트레이드 단위 통계만 본다 — 여러 종목을 동시에 들고
    # 있는 실제 포트폴리오 시뮬레이션은 하지 않았다.
    if trades.empty:
        return {"strategy": label, "trades": 0}
    wins = trades[trades["return_pct"] > 0]
    losses = trades[trades["return_pct"] <= 0]
    gross_win = wins["return_pct"].sum()
    gross_loss = -losses["return_pct"].sum()
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")
    row = {
        "strategy": label,
        "trades": len(trades),
        "win_rate_%": round(len(wins) / len(trades) * 100, 1),
        "avg_return_%": round(trades["return_pct"].mean(), 2),
        "avg_win_%": round(wins["return_pct"].mean(), 2) if not wins.empty else 0.0,
        "avg_loss_%": round(losses["return_pct"].mean(), 2) if not losses.empty else 0.0,
        "profit_factor": profit_factor,
        "avg_hold_days": round(trades["hold_days"].mean(), 1),
        "worst_trade_%": round(trades["return_pct"].min(), 1),
    }
    if "r_multiple" in trades.columns and trades["r_multiple"].notna().any():
        row["avg_r_multiple"] = round(trades["r_multiple"].mean(), 2)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="legacy vs rsi2_trend 백테스트 비교")
    add_ticker_args(parser)
    parser.add_argument("--sample", type=int, help="유니버스에서 무작위로 N개만 샘플링")
    parser.add_argument("--history", default="5y")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tickers = collect_tickers(args)
    if args.sample and args.sample < len(tickers):
        random.seed(args.seed)
        tickers = sorted(random.sample(tickers, args.sample))

    print(f"[백테스트] {len(tickers)}개 종목 x {args.history}\n")

    legacy_all, rsi2_all = [], []
    for n, ticker in enumerate(tickers, start=1):
        if n % 25 == 0:
            print(f"  진행 {n}/{len(tickers)}...")
        df = fetch_history(ticker, args.history)
        if df is None or len(df) < 210:
            continue
        lt = legacy_macd_disparity_trades(df, LegacyParams())
        rt = rsi2_trend_trades(df, RSI2Params())
        if not lt.empty:
            lt.insert(0, "ticker", ticker)
            legacy_all.append(lt)
        if not rt.empty:
            rt.insert(0, "ticker", ticker)
            rsi2_all.append(rt)

    legacy_trades = pd.concat(legacy_all, ignore_index=True) if legacy_all else pd.DataFrame()
    rsi2_trades = pd.concat(rsi2_all, ignore_index=True) if rsi2_all else pd.DataFrame()

    legacy_trades.to_csv("backtest_legacy_trades.csv", index=False)
    rsi2_trades.to_csv("backtest_rsi2_trend_trades.csv", index=False)

    summary = pd.DataFrame(
        [
            summarize(legacy_trades, "legacy_macd_disparity (영상 원본, 손절 없음)"),
            summarize(rsi2_trades, "rsi2_trend (RSI2 평균회귀 + ATR 손절/추세필터)"),
        ]
    )
    print("\n=== 결과 요약 ===")
    print(summary.to_string(index=False))
    summary.to_csv("backtest_summary.csv", index=False)
    print("\n트레이드 상세: backtest_legacy_trades.csv, backtest_rsi2_trend_trades.csv")
    print("요약: backtest_summary.csv")
    print(
        "\n※ 참고용 백테스트입니다 — 거래비용/슬리피지 미반영, 현재 S&P500 구성종목 기준"
        "(생존편향), 여러 종목 동시 보유를 감안한 포트폴리오 복리 계산은 하지 않고"
        "트레이드 단위 통계만 봄. 과거 성과가 미래 수익을 보장하지 않습니다."
    )


if __name__ == "__main__":
    main()
