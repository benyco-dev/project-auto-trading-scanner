#!/usr/bin/env python3
"""고친 로직만 확인하는 최소 self-check.  실행: python test_checks.py"""

from datetime import date

import pandas as pd

from strategies import LegacyParams, RSI2Params, build_params, legacy_latest_signal, rsi2_trend_trades
from toss.exit_positions import exit_reason, open_positions


def _df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Close": c, "High": c * 1.01, "Low": c * 0.99})


def test_legacy_ignores_nan_disparity():
    # 이격도(15일)를 계산할 봉이 부족한 구간에서는 시그널이 나오면 안 된다.
    # 예전 코드는 NaN > 85 가 False라 가드가 뚫렸다.
    df = _df([10, 9.5, 9, 8.5, 8, 7.5, 7, 6.5, 6, 5.5, 12, 13])
    assert legacy_latest_signal(df, LegacyParams()) is None


def test_rsi2_stop_below_entry():
    # 진입가보다 손절가가 낮아야 사이징 분모가 양수가 된다.
    # 200일선 위(장기 상승) + 마지막에 급락 → RSI(2)가 10 아래로 떨어지는 형태
    closes = [100 + i for i in range(220)] + [316, 310, 304, 300, 305, 312, 318]
    trades = rsi2_trend_trades(_df(closes), RSI2Params())
    assert not trades.empty
    assert (trades["stop_price"] < trades["entry_price"]).all()


def test_build_params_picks_strategy():
    class A:
        strategy = "legacy"
        macd_fast, macd_slow, macd_signal = 5, 25, 9
        disparity_window, disparity_threshold, lookback = 15, 85.0, 20
        trend_sma, rsi_period, rsi_threshold, exit_sma = 200, 2, 10.0, 5
        atr_period, atr_mult, max_hold_days = 14, 2.5, 10

    assert isinstance(build_params("legacy", A), LegacyParams)
    assert isinstance(build_params("rsi2_trend", A), RSI2Params)


def test_open_positions_nets_buys_and_sells(tmp_path="/tmp/_ol.csv"):
    pd.DataFrame([
        {"ticker": "AAA", "date": "2026-09-01", "strategy": "rsi2_trend", "side": "BUY",
         "quantity": 10, "price": 100.0, "stop_price": 90.0, "filled_at": "2026-09-01", "result": ""},
        {"ticker": "BBB", "date": "2026-09-01", "strategy": "rsi2_trend", "side": "BUY",
         "quantity": 5, "price": 50.0, "stop_price": 45.0, "filled_at": "2026-09-01", "result": ""},
        {"ticker": "BBB", "date": "2026-09-03", "strategy": "rsi2_trend", "side": "SELL",
         "quantity": 5, "price": 55.0, "stop_price": 45.0, "filled_at": "2026-09-03", "result": ""},
    ]).to_csv(tmp_path, index=False)
    pos = {p["ticker"]: p for p in open_positions(tmp_path)}
    assert set(pos) == {"AAA"}, pos          # 전량 매도한 BBB는 빠져야 한다
    assert pos["AAA"]["quantity"] == 10


def test_exit_rules():
    today = date(2026, 9, 10)
    pos = {"ticker": "AAA", "quantity": 10, "stop_price": 90.0, "filled_at": "2026-09-09", "strategy": "x"}

    # 손절: 저가가 손절가를 깨면 사유는 stop
    df = _df([100] * 10 + [89])
    assert exit_reason(df, pos, 5, 10, today)[0] == "stop"

    # 익절: 종가가 5일선 위
    df = _df([100, 99, 98, 97, 96, 120])
    assert exit_reason(df, pos, 5, 10, today)[0] == "target_sma"

    # 미충족: 손절가 위 + 5일선 아래 + 보유일 부족 → 유지
    df = _df([100, 101, 102, 103, 104, 99])
    assert exit_reason(df, pos, 5, 10, today) is None


if __name__ == "__main__":
    test_legacy_ignores_nan_disparity()
    test_rsi2_stop_below_entry()
    test_build_params_picks_strategy()
    test_open_positions_nets_buys_and_sells()
    test_exit_rules()
    print("all checks passed")
