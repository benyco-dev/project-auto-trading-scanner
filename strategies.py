#!/usr/bin/env python3
"""
strategies.py

시그널 생성 로직을 전략별로 분리해둔 모듈.

legacy_macd_disparity
  영상(1강)에서 쓴 원래 로직: MACD(5,25,9) 골든크로스 + 15일 이격도 85% 이하.
  손절/청산 규칙이 없다 — 언제 팔지에 대한 답이 없는 상태라 리스크 관리가
  안 되는 로직이라는 게 가장 큰 단점.

rsi2_trend
  Larry Connors & Cesar Alvarez, "Short-Term Trading Strategies That Work"
  (2008)에서 소개된 RSI(2) 평균회귀 전략을 뼈대로, Turtle Trading 식
  ATR 기반 손절/포지션 사이징을 결합한 버전.

    진입: 종가가 200일 이동평균 위(장기 상승 추세) + RSI(2)가 5 이하로
          급락(단기 과매도, 추세 안의 눌림목)
    청산: 종가가 10일 이동평균을 다시 상회하면 익절, 또는 진입가 대비
          ATR(14) x 2.5 만큼 하락하면 손절, 또는 20거래일이 지나도
          둘 다 안 나오면 시간 청산
    사이징: 계좌 자산 대비 리스크 비율(기본 1%)을 정하고,
          수량 = (계좌자산 x 리스크비율) / (진입가 - 손절가)

  추세 필터(200일선), 단기 되돌림 진입, ATR 손절/사이징은 각각 개별적으로
  가장 많이 검증된 축에 속하는 규칙들이라, 이 조합이 "제일 화려한" 전략은
  아니지만 "제일 근거 있는" 조합에 가깝다고 보고 기본값으로 선택했다.
  (아래 backtest.py로 원본 로직과 실제 성과를 비교해볼 수 있다.)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ── 공통 지표 ────────────────────────────────────────────────────────


def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": macd_line, "signal": signal_line})


def position_size(entry: float, stop: float | None, equity: float | None, risk_pct: float) -> int | None:
    """리스크 기반 수량 = (계좌자산 x 리스크비율) / 주당 손실폭."""
    if stop is None or equity is None or entry <= stop:
        return None
    risk_per_share = entry - stop
    risk_budget = equity * (risk_pct / 100.0)
    return int(risk_budget // risk_per_share)


# ── 전략 1: 영상 원본 (MACD 골든크로스 + 이격도) ──────────────────────


@dataclass
class LegacyParams:
    macd_fast: int = 5
    macd_slow: int = 25
    macd_signal: int = 9
    disparity_window: int = 15
    disparity_threshold: float = 85.0
    lookback: int = 20
    max_hold_days: int = 30  # 영상엔 청산 규칙이 없어 백테스트용으로 상한만 둠


def legacy_macd_disparity_trades(df: pd.DataFrame, p: LegacyParams) -> pd.DataFrame:
    close = df["Close"]
    macd_df = compute_macd(close, p.macd_fast, p.macd_slow, p.macd_signal)
    disparity = close / close.rolling(p.disparity_window).mean() * 100.0

    prev_diff = (macd_df["macd"] - macd_df["signal"]).shift(1)
    curr_diff = macd_df["macd"] - macd_df["signal"]
    golden_cross = (prev_diff <= 0) & (curr_diff > 0)
    death_cross = (prev_diff >= 0) & (curr_diff < 0)

    n = len(df)
    trades = []
    for i in np.where(golden_cross.to_numpy())[0]:
        start = max(0, i - p.lookback)
        # NaN(이격도 계산에 필요한 봉이 부족한 구간)은 "조건 불충족"으로 본다.
        # prior.min() > threshold 로 쓰면 NaN 비교가 False라 가드가 뚫린다.
        if not disparity.iloc[start:i].le(p.disparity_threshold).any():
            continue
        entry_price = float(close.iloc[i])
        exit_i, exit_reason = None, None
        for j in range(i + 1, min(i + 1 + p.max_hold_days, n)):
            if death_cross.iloc[j]:
                exit_i, exit_reason = j, "death_cross"
                break
        if exit_i is None:
            exit_i, exit_reason = min(i + p.max_hold_days, n - 1), "time"
        exit_price = float(close.iloc[exit_i])
        trades.append(
            {
                "entry_date": close.index[i].date(),
                "entry_price": round(entry_price, 2),
                "stop_price": None,  # 영상 로직엔 손절 규칙이 없음
                "exit_date": close.index[exit_i].date(),
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "return_pct": round((exit_price / entry_price - 1) * 100, 2),
                "r_multiple": None,
                "hold_days": exit_i - i,
            }
        )
    return pd.DataFrame(trades)


def legacy_latest_signal(df: pd.DataFrame, p: LegacyParams) -> dict | None:
    """가장 최근 봉에 새 진입 시그널이 떴는지만 확인 (실시간 스캔용)."""
    close = df["Close"]
    macd_df = compute_macd(close, p.macd_fast, p.macd_slow, p.macd_signal)
    disparity = close / close.rolling(p.disparity_window).mean() * 100.0
    prev_diff = (macd_df["macd"] - macd_df["signal"]).shift(1)
    curr_diff = macd_df["macd"] - macd_df["signal"]
    i = len(df) - 1
    if not ((prev_diff.iloc[i] <= 0) and (curr_diff.iloc[i] > 0)):
        return None
    start = max(0, i - p.lookback)
    if not disparity.iloc[start:i].le(p.disparity_threshold).any():
        return None
    entry_price = float(close.iloc[i])
    return {
        "date": close.index[i].date(),
        "entry_price": round(entry_price, 2),
        "stop_price": None,
        "strategy": "legacy_macd_disparity",
    }


# ── 전략 2: RSI(2) 평균회귀 + 추세 필터 + ATR 리스크 관리 ─────────────


@dataclass
class RSI2Params:
    trend_sma: int = 200
    rsi_period: int = 2
    # Connors 원본은 5. 10으로 완화하면 트레이드가 2배로 늘지만 백테스트상
    # 트레이드당 성과는 나빠지고 최악 손실도 커진다 (PF 1.47→1.42, 최악 -17.6%→-19.1%).
    rsi_entry_threshold: float = 5.0
    # 익절선을 SMA5에서 10으로 늘렸다. 왕복 수수료 0.2%는 트레이드당 고정인데
    # SMA5 청산은 평균 3.5일/+0.41%라 수익의 절반을 수수료가 먹는다. SMA10은
    # 평균 5.9일/+0.57%로, 독립 표본 3개 시뮬레이션에서 순수익률 7.7%→22.9%.
    exit_sma: int = 10
    atr_period: int = 14
    atr_stop_mult: float = 2.5
    max_hold_days: int = 20


def rsi2_trend_trades(df: pd.DataFrame, p: RSI2Params) -> pd.DataFrame:
    close, low = df["Close"], df["Low"]
    sma_trend = close.rolling(p.trend_sma).mean()
    sma_exit = close.rolling(p.exit_sma).mean()
    rsi = compute_rsi(close, p.rsi_period)
    atr = compute_atr(df, p.atr_period)

    entry_signal = (close > sma_trend) & (rsi < p.rsi_entry_threshold)

    n = len(df)
    trades = []
    last_exit_i = -1
    for i in np.where(entry_signal.to_numpy())[0]:
        if i <= last_exit_i or pd.isna(atr.iloc[i]) or pd.isna(sma_trend.iloc[i]):
            continue
        entry_price = float(close.iloc[i])
        stop_price = entry_price - p.atr_stop_mult * float(atr.iloc[i])
        if stop_price >= entry_price:
            continue
        exit_i, exit_price, exit_reason = None, None, None
        for j in range(i + 1, min(i + 1 + p.max_hold_days, n)):
            if float(low.iloc[j]) <= stop_price:
                exit_i, exit_price, exit_reason = j, stop_price, "stop"
                break
            if float(close.iloc[j]) > float(sma_exit.iloc[j]):
                exit_i, exit_price, exit_reason = j, float(close.iloc[j]), "target_sma"
                break
        if exit_i is None:
            exit_i = min(i + p.max_hold_days, n - 1)
            exit_price = float(close.iloc[exit_i])
            exit_reason = "time"
        last_exit_i = exit_i
        risk = entry_price - stop_price
        trades.append(
            {
                "entry_date": close.index[i].date(),
                "entry_price": round(entry_price, 2),
                "stop_price": round(stop_price, 2),
                "exit_date": close.index[exit_i].date(),
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "return_pct": round((exit_price / entry_price - 1) * 100, 2),
                "r_multiple": round((exit_price - entry_price) / risk, 2) if risk > 0 else None,
                "hold_days": exit_i - i,
            }
        )
    return pd.DataFrame(trades)


def rsi2_trend_latest_signal(df: pd.DataFrame, p: RSI2Params) -> dict | None:
    """가장 최근 봉에 새 진입 시그널이 떴는지만 확인 (실시간 스캔용)."""
    close = df["Close"]
    sma_trend = close.rolling(p.trend_sma).mean()
    rsi = compute_rsi(close, p.rsi_period)
    atr = compute_atr(df, p.atr_period)

    i = len(df) - 1
    if pd.isna(sma_trend.iloc[i]) or pd.isna(atr.iloc[i]):
        return None
    if not (close.iloc[i] > sma_trend.iloc[i] and rsi.iloc[i] < p.rsi_entry_threshold):
        return None
    entry_price = float(close.iloc[i])
    stop_price = entry_price - p.atr_stop_mult * float(atr.iloc[i])
    if stop_price >= entry_price:
        return None
    return {
        "date": close.index[i].date(),
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "strategy": "rsi2_trend",
    }


STRATEGIES = {
    "legacy": (LegacyParams, legacy_macd_disparity_trades, legacy_latest_signal),
    "rsi2_trend": (RSI2Params, rsi2_trend_trades, rsi2_trend_latest_signal),
}


# ── CLI 공통 (scanner.py / run_and_log.py가 같이 씀) ──────────────────


def add_strategy_args(parser) -> None:
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="rsi2_trend")
    parser.add_argument("--trend-sma", type=int, default=200)
    parser.add_argument("--rsi-period", type=int, default=2)
    parser.add_argument("--rsi-threshold", type=float, default=5.0)
    parser.add_argument("--exit-sma", type=int, default=10)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--atr-mult", type=float, default=2.5)
    parser.add_argument("--max-hold-days", type=int, default=20)
    parser.add_argument("--macd-fast", type=int, default=5)
    parser.add_argument("--macd-slow", type=int, default=25)
    parser.add_argument("--macd-signal", type=int, default=9)
    parser.add_argument("--disparity-window", type=int, default=15)
    parser.add_argument("--disparity-threshold", type=float, default=85.0)
    parser.add_argument("--lookback", type=int, default=20)


def build_params(strategy: str, args):
    if strategy == "legacy":
        return LegacyParams(
            macd_fast=args.macd_fast,
            macd_slow=args.macd_slow,
            macd_signal=args.macd_signal,
            disparity_window=args.disparity_window,
            disparity_threshold=args.disparity_threshold,
            lookback=args.lookback,
        )
    return RSI2Params(
        trend_sma=args.trend_sma,
        rsi_period=args.rsi_period,
        rsi_entry_threshold=args.rsi_threshold,
        exit_sma=args.exit_sma,
        atr_period=args.atr_period,
        atr_stop_mult=args.atr_mult,
        max_hold_days=args.max_hold_days,
    )
