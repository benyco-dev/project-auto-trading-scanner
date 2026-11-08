"""
weekly_ma/strategy.py

40주 이동평균 돌파 전략 (스탠 와인스타인의 "2단계 진입"과 같은 계열).

  진입: 지난주 종가가 40주선 아래였는데, 이번 주 종가가 40주선 위로 올라섬
  청산: 보유 중 주간 종가가 40주선 아래로 내려감

일봉을 금요일 기준 주봉으로 바꿔서 판단한다. 주중의 흔들림은 무시하고 주 단위
추세만 본다 — RSI(2) 전략(며칠짜리 눌림목)과 시간축이 겹치지 않는 게 의도다.

이 모듈은 순수 함수만 둔다. 데이터 조회·장부 기록은 weekly_ma/paper.py 몫.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

MA_WEEKS = 40


def to_weekly(daily: pd.DataFrame, asof: date | None = None) -> pd.DataFrame:
    """일봉 → 금요일 마감 주봉. asof가 주중이면 아직 안 끝난 이번 주는 버린다.

    주봉 판단은 '완성된 주'로만 한다. 월요일 아침에 돌리면 마지막 행은 지난 금요일
    마감 주봉이어야 하고, 수요일에 돌려도 이번 주(미완성)를 쓰면 안 된다.
    """
    w = daily.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    # 라벨은 늘 금요일이지만 금요일이 휴장(성금요일 등)이면 실제 마지막 거래일은 목요일이다.
    # 장부 체결일은 실제 거래일이어야 대시보드가 일봉과 맞춰 그린다.
    w["last_day"] = daily.index.to_series().resample("W-FRI").last().reindex(w.index).dt.date
    if asof is not None:
        w = w[w.index.date <= asof]           # 금요일이 아직 안 온 주 = 미완성
    return w


def add_signals(weekly: pd.DataFrame, ma_weeks: int = MA_WEEKS) -> pd.DataFrame:
    """주봉에 40주선과 돌파/이탈 표시를 붙인다."""
    out = weekly.copy()
    out["ma"] = out["Close"].rolling(ma_weeks).mean()
    above = out["Close"] > out["ma"]
    prev_above = above.shift(1, fill_value=False)
    valid = out["ma"].notna() & out["ma"].shift(1).notna()
    out["cross_up"] = valid & above & ~prev_above        # 아래 → 위
    out["cross_down"] = valid & ~above & prev_above      # 위 → 아래
    # 돌파 주 거래량이 최근 10주 평균의 몇 배인지. 와인스타인은 거래량 동반 돌파를
    # 더 믿었다. 같은 주에 돌파 종목이 여러 개면 이 값이 큰 것부터 담는다.
    out["vol_ratio"] = out["Volume"] / out["Volume"].rolling(10).mean().shift(1)
    return out


def backtest_trades(daily: pd.DataFrame, ma_weeks: int = MA_WEEKS) -> pd.DataFrame:
    """종목 하나의 과거 매매 내역. 돌파 주 종가에 사고, 이탈 주 종가에 판다."""
    s = add_signals(to_weekly(daily), ma_weeks)
    trades, entry = [], None
    for ts, row in s.iterrows():
        if entry is None and row["cross_up"]:
            entry = (ts, float(row["Close"]), float(row["ma"]), float(row["vol_ratio"]))
        elif entry is not None and row["cross_down"]:
            e_ts, e_px, e_ma, e_vr = entry
            x_px = float(row["Close"])
            trades.append({
                "entry_date": e_ts.date(), "entry_price": round(e_px, 2),
                "stop_price": round(e_ma, 2),    # 진입 시점 40주선 (정보용 — 실제 청산은 주간 이탈)
                "exit_date": ts.date(), "exit_price": round(x_px, 2),
                "exit_reason": "ma_cross_down", "vol_ratio": round(e_vr, 2),
                "return_pct": round((x_px / e_px - 1) * 100, 2),
                "hold_weeks": int((ts - e_ts).days // 7),
            })
            entry = None
    return pd.DataFrame(trades)
