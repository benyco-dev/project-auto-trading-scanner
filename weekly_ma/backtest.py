#!/usr/bin/env python3
"""
weekly_ma/backtest.py

40주선 돌파 전략을 S&P 500 전체에 균등 비중 포트폴리오로 돌려본다 (수수료 0.1%/회 반영).
자산은 매주 종가로 시가평가하므로 낙폭(MDD)이 원가 기준보다 정직하게 나온다.

  python -m weekly_ma.backtest --history 10y --capital 20000 --slots 5 10 20
  python -m weekly_ma.backtest --point-in-time   # 각 시점의 실제 구성종목만 (생존편향 제거)
"""

from __future__ import annotations

import argparse

import pandas as pd

from weekly_ma.strategy import backtest_trades


def simulate(trades: pd.DataFrame, weekly_close: pd.DataFrame, capital: float, slots: int,
             fee: float = 0.001, order: str | None = "vol_ratio") -> dict:
    cash, pos, curve, taken = capital, [], [], 0
    for ts in weekly_close.index[weekly_close.index.date >= trades.entry_date.min()]:
        d = ts.date()
        for p in [p for p in pos if p["exit_date"] <= d]:            # 매도 먼저 (현금 회수)
            cash += p["qty"] * p["exit_price"] * (1 - fee)
            pos.remove(p)
        today = trades[trades.entry_date == d]
        if order:
            today = today.sort_values(order, ascending=False)
        equity = cash + sum(p["qty"] * weekly_close.at[ts, p["t"]] for p in pos)
        for t in today.itertuples():
            if len(pos) >= slots:
                break
            qty = int(equity / slots // t.entry_price)
            if qty < 1 or qty * t.entry_price * (1 + fee) > cash:
                continue
            cash -= qty * t.entry_price * (1 + fee)
            pos.append({"t": t.ticker, "qty": qty, "exit_date": t.exit_date, "exit_price": t.exit_price})
            taken += 1
        curve.append(cash + sum(p["qty"] * weekly_close.at[ts, p["t"]] for p in pos))
    eq = pd.Series(curve)
    years = len(eq) / 52
    return {"슬롯": slots, "정렬": order or "랜덤",
            "총수익%": round((eq.iloc[-1] / capital - 1) * 100, 1),
            "연%": round(((eq.iloc[-1] / capital) ** (1 / years) - 1) * 100, 1),
            "MDD%": round((eq / eq.cummax() - 1).min() * 100, 1), "체결": taken}


def main() -> None:
    ap = argparse.ArgumentParser(description="40주선 돌파 전략 포트폴리오 백테스트")
    ap.add_argument("--history", default="10y")
    ap.add_argument("--capital", type=float, default=20000)
    ap.add_argument("--slots", type=int, nargs="+", default=[5, 10, 20])
    ap.add_argument("--point-in-time", action="store_true",
                    help="진입 시점에 실제로 S&P500 구성종목이던 것만 산다 (빠진 종목 시세도 받는다)")
    ap.add_argument("--random-seeds", type=int, default=5, help="정렬 기준 대신 무작위 순서로 몇 번 돌릴지")
    args = ap.parse_args()

    import yfinance as yf
    from scanner import fetch_sp500_changes, fetch_sp500_tickers, sp500_members_at

    tickers = fetch_sp500_tickers()
    members = None
    if args.point_in_time:
        weeks = pd.date_range(end=pd.Timestamp.today(), periods=int(args.history.rstrip("y")) * 52 + 1, freq="W-FRI")
        members = sp500_members_at(tickers, fetch_sp500_changes(), weeks)
        tickers = sorted(set().union(*members.values()))
    raw = yf.download(tickers, period=args.history, interval="1d", progress=False,
                      auto_adjust=True, group_by="ticker", threads=True)
    out = []
    for t in raw.columns.get_level_values(0).unique():
        d = raw[t].dropna(subset=["Close"])
        tr = backtest_trades(d) if len(d) > 300 else pd.DataFrame()
        if not tr.empty:
            out.append(tr.assign(ticker=t))
    trades = pd.concat(out).sort_values("entry_date").reset_index(drop=True)
    if members is not None:
        keys = pd.DatetimeIndex(sorted(members))
        trades = trades[trades.entry_date >= keys[0].date()]
        at = keys[keys.searchsorted(pd.to_datetime(trades.entry_date), side="right") - 1]  # 진입일 직전 주
        trades = trades[[t in members[k] for t, k in zip(trades.ticker, at)]].reset_index(drop=True)
        print(f"시점별 구성종목: 후보 티커 {len(tickers)}개 중 시세 있는 {len(out)}개")
    wc = raw.xs("Close", axis=1, level=1).resample("W-FRI").last().ffill()

    r = trades.return_pct
    print(f"트레이드 {len(trades)}건 ({trades.entry_date.min()} ~ {trades.exit_date.max()}) · "
          f"승률 {(r > 0).mean() * 100:.1f}% · 평균 {r.mean():+.2f}% · 평균익 {r[r > 0].mean():+.1f}% · "
          f"평균손 {r[r <= 0].mean():+.1f}% · 2주 안에 털림 {(trades.hold_weeks <= 2).mean() * 100:.0f}%\n")
    rows = [simulate(trades, wc, args.capital, s) for s in args.slots]
    rows += [simulate(trades.sample(frac=1, random_state=k), wc, args.capital, 10, order=None)
             for k in range(args.random_seeds)]
    print(pd.DataFrame(rows).to_string(index=False))
    spy = yf.download("SPY", start=str(trades.entry_date.min()), progress=False,
                      auto_adjust=True)["Close"].squeeze()
    years = (spy.index[-1] - spy.index[0]).days / 365.25
    print(f"\n비교: SPY 보유 총 {(spy.iloc[-1] / spy.iloc[0] - 1) * 100:.1f}% · "
          f"연 {((spy.iloc[-1] / spy.iloc[0]) ** (1 / years) - 1) * 100:.1f}% · "
          f"MDD {(spy / spy.cummax() - 1).min() * 100:.1f}%")
    print("\n※ " + ("시점별 구성종목 기준. 상장폐지로 시세가 없는 옛 종목은 빠져 있다." if members is not None
                     else "현재 S&P500 구성종목 기준(생존편향 — 추세추종에 특히 유리하게 작용).")
          + " 배당·세금·환전 미반영.")


if __name__ == "__main__":
    main()
