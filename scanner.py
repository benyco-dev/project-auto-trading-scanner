#!/usr/bin/env python3
"""
scanner.py

미국 주식 종목들을 대상으로 strategies.py에 정의된 전략의 최근 시그널을 스캔한다.

기본 전략은 rsi2_trend (RSI(2) 평균회귀 + 200일 추세 필터 + ATR 손절/사이징).
영상 1강의 원본 로직은 --strategy legacy 로 그대로 남겨뒀다 — backtest.py로
둘의 실제 과거 성과를 비교해볼 수 있다.

사용 예:
  python scanner.py --tickers TSLA AAPL NVDA
  python scanner.py --tickers-file tickers.txt --history 2y
  python scanner.py --universe sp500
  python scanner.py --tickers TSLA --strategy legacy   # 영상 원본 로직으로 비교
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request

import pandas as pd
import yfinance as yf

from strategies import STRATEGIES, add_strategy_args, build_params


def fetch_sp500_tickers() -> list[str]:
    """S&P 500 구성 종목을 위키피디아에서 가져온다. (미국 주식 '전체'의 근사치)"""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    symbols = tables[0]["Symbol"].tolist()
    return [s.replace(".", "-") for s in symbols]  # yfinance 표기 규칙(BRK.B -> BRK-B)에 맞춤


def fetch_history(ticker: str, period: str) -> pd.DataFrame | None:
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def recent_signals(ticker: str, df: pd.DataFrame, strategy: str, params, n: int) -> pd.DataFrame:
    _, trades_fn, _ = STRATEGIES[strategy]
    trades = trades_fn(df, params)
    if trades.empty:
        return trades
    trades = trades.sort_values("entry_date", ascending=False).head(n).copy()
    trades.insert(0, "ticker", ticker)
    return trades


def add_ticker_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tickers", nargs="+", help="스캔할 티커 목록 (예: TSLA AAPL NVDA)")
    parser.add_argument("--tickers-file", help="티커 목록이 한 줄에 하나씩 있는 텍스트 파일")
    parser.add_argument("--universe", choices=["sp500"], help="지수 구성종목 전체 스캔")


def collect_tickers(args: argparse.Namespace) -> list[str]:
    tickers: list[str] = []
    if args.tickers:
        tickers.extend(t.upper() for t in args.tickers)
    if args.tickers_file:
        with open(args.tickers_file) as f:
            tickers.extend(line.strip().upper() for line in f if line.strip())
    if args.universe == "sp500":
        tickers.extend(fetch_sp500_tickers())
    return sorted(set(tickers))


def main() -> None:
    parser = argparse.ArgumentParser(description="주식 시그널 스캐너 (rsi2_trend 기본, legacy 선택 가능)")
    add_ticker_args(parser)
    add_strategy_args(parser)
    parser.add_argument("--history", default="2y", help="가져올 히스토리 기간 (yfinance period)")
    parser.add_argument("--recent", type=int, default=5, help="종목별로 출력할 최근 시그널 개수")
    args = parser.parse_args()

    tickers = collect_tickers(args)
    if not tickers:
        parser.error("--tickers / --tickers-file / --universe 중 하나는 지정해야 합니다.")

    params = build_params(args.strategy, args)
    print(f"[설정] 전략: {args.strategy} / 파라미터: {params}\n")

    quiet = len(tickers) > 20
    all_results = []
    for n, ticker in enumerate(tickers, start=1):
        if quiet and n % 50 == 0:
            print(f"[진행] {n}/{len(tickers)} 종목 처리...")
        df = fetch_history(ticker, args.history)
        if df is None:
            if not quiet:
                print(f"[{ticker}] 데이터를 가져오지 못했습니다. 티커를 확인하세요.")
            continue
        result = recent_signals(ticker, df, args.strategy, params, args.recent)
        if result.empty:
            if not quiet:
                print(f"[{ticker}] 조건을 만족하는 시그널이 없습니다.")
            continue
        all_results.append(result)

        print(f"[{ticker}] 최근 시그널 {len(result)}건")
        for _, row in result.iterrows():
            stop = f", 손절 {row['stop_price']}" if pd.notna(row.get("stop_price")) else ""
            print(
                f"  - {row['entry_date']} 진입 {row['entry_price']}{stop} → "
                f"{row['exit_date']} 청산 {row['exit_price']} ({row['exit_reason']}), "
                f"수익률 {row['return_pct']}%"
            )
        print()

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        combined.to_csv("scan_results.csv", index=False)
        print("전체 결과를 scan_results.csv 에 저장했습니다.")
    else:
        print("모든 종목에서 조건을 만족하는 시그널을 찾지 못했습니다.")


if __name__ == "__main__":
    sys.exit(main())
