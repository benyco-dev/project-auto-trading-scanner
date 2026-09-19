#!/usr/bin/env python3
"""고친 로직만 확인하는 최소 self-check.  실행: python test_checks.py"""

from datetime import date

import pandas as pd

from strategies import LegacyParams, RSI2Params, build_params, legacy_latest_signal, rsi2_trend_trades
from toss.exit_positions import exit_reason, open_positions
from toss.place_orders import select_candidates


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


def _sig(ticker, d, entry, stop):
    return {"ticker": ticker, "date": d, "strategy": "rsi2_trend",
            "entry_price": str(entry), "stop_price": str(stop)}


def test_stale_signals_do_not_starve_fresh_ones():
    # 옛 시그널이 손절폭이 제일 좁아도 새 시그널 자리를 뺏으면 안 된다.
    # (모의매매 첫 주: 손절폭 $1짜리 DOC 옛 시그널이 5자리를 매일 점령 → 8거래일 매수 0건)
    today = date(2026, 9, 19)
    old = [_sig(f"OLD{i}", "2026-09-09", 20.0, 19.0) for i in range(5)]   # 손절폭 1.0
    new = [_sig("NEW", "2026-09-18", 100.0, 95.0)]                       # 손절폭 5.0
    picked = select_candidates(pd.DataFrame(old + new), set(), today, max_age_days=3, max_orders=5)
    assert list(picked["ticker"]) == ["NEW"], picked


def test_one_position_per_ticker():
    # 같은 종목 시그널이 여러 날 나오면 최신 하나만, 보유 중이면 아예 제외.
    today = date(2026, 9, 19)
    df = pd.DataFrame([
        _sig("RF", "2026-09-16", 28.47, 26.90),
        _sig("RF", "2026-09-17", 28.48, 26.94),
        _sig("HELD", "2026-09-18", 50.0, 48.0),
        _sig("BAX", "2026-09-18", 22.8, 20.82),
    ])
    picked = select_candidates(df, {"HELD"}, today, max_age_days=3, max_orders=5)
    assert sorted(picked["ticker"]) == ["BAX", "RF"], picked
    assert picked.set_index("ticker").loc["RF", "date"] == "2026-09-17"


if __name__ == "__main__":
    test_legacy_ignores_nan_disparity()
    test_rsi2_stop_below_entry()
    test_build_params_picks_strategy()
    test_open_positions_nets_buys_and_sells()
    test_exit_rules()
    test_stale_signals_do_not_starve_fresh_ones()
    test_one_position_per_ticker()
    print("all checks passed")
