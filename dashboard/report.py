#!/usr/bin/env python3
"""
dashboard/report.py

모의매매 장부(paper_orders_*.csv)를 읽어 대시보드용 JSON을 만든다.

장부는 VM에만 있고 저장소에는 커밋하지 않는다(*.csv는 .gitignore). VM이 매일
이 JSON을 만들어 공개 GCS 버킷에 올리고, GitHub Pages의 정적 페이지가 그걸
읽어 그린다 — 저장소에 쓰기 권한을 주지 않고도 매일 갱신된다.

구조:
  build_report()  순수 함수. 장부 + 시세를 받아 지표/자산곡선을 계산한다.
                  I/O 없음 — 테스트에서 가짜 데이터로 그대로 호출할 수 있다.
  main()          I/O 껍데기. CSV 읽기, yfinance 조회, JSON 쓰기.

화면은 docs/index.html이 이 JSON을 읽어 그린다. 렌더러를 파이썬과 JS 양쪽에
두면 둘이 어긋나므로 브라우저 한 곳에만 둔다.

사용:
  python -m dashboard.report --orders-dir ./_ledgers --out docs/paper.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

COMMISSION = 0.001  # 토스증권 미국주식 매매 수수료 0.1% (왕복 0.2%)


# ── 순수 계산 ────────────────────────────────────────────────────────


def _round_trips(orders: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """장부를 청산 완료 트레이드와 보유 중 포지션으로 나눈다."""
    closed, open_pos = [], []
    for ticker, rows in orders.groupby("ticker", sort=False):
        buys = rows[rows["side"] == "BUY"].to_dict("records")
        sells = rows[rows["side"] == "SELL"].to_dict("records")
        for i, b in enumerate(buys):
            s = sells[i] if i < len(sells) else None
            if s is None:
                open_pos.append({
                    "ticker": ticker, "qty": int(b["quantity"]),
                    "entry": float(b["price"]), "stop": float(b["stop_price"]),
                    "entry_date": str(b["filled_at"]),
                })
                continue
            qty, entry, exit_ = int(b["quantity"]), float(b["price"]), float(s["price"])
            fee = qty * (entry + exit_) * COMMISSION
            closed.append({
                "ticker": ticker, "qty": qty, "entry": entry, "exit": exit_,
                "entry_date": str(b["filled_at"]), "exit_date": str(s["filled_at"]),
                "reason": str(s.get("result", "")).split(" |")[0],
                "pnl": round(qty * (exit_ - entry) - fee, 2),
                "fee": round(fee, 2),
                "pct": round((exit_ / entry - 1) * 100, 2),
            })
    return closed, open_pos


def _equity_curve(orders: pd.DataFrame, prices: pd.DataFrame, capital: float) -> list[dict]:
    """일자별 자산 = 현금 + 보유 포지션 평가액. 수수료는 체결 시점에 차감."""
    if orders.empty or prices.empty:
        return []
    days = [d.date() for d in prices.index]
    cash, holdings, curve = capital, {}, []
    by_day: dict[str, list[dict]] = {}
    for r in orders.to_dict("records"):
        by_day.setdefault(str(r["filled_at"]), []).append(r)

    for d in days:
        for r in by_day.get(d.isoformat(), []):
            qty, px = int(r["quantity"]), float(r["price"])
            amount, fee = qty * px, qty * px * COMMISSION
            if r["side"] == "BUY":
                cash -= amount + fee
                holdings[r["ticker"]] = holdings.get(r["ticker"], 0) + qty
            else:
                cash += amount - fee
                holdings[r["ticker"]] = holdings.get(r["ticker"], 0) - qty
                if holdings[r["ticker"]] <= 0:
                    holdings.pop(r["ticker"], None)
        mv = 0.0
        for t, q in holdings.items():
            if t in prices.columns:
                px = prices.loc[: pd.Timestamp(d), t].dropna()
                if not px.empty:
                    mv += q * float(px.iloc[-1])
        curve.append({"date": d.isoformat(), "equity": round(cash + mv, 2)})
    return curve


def build_report(ledgers: dict[int, pd.DataFrame], prices: pd.DataFrame,
                 last_price: dict[str, float], generated_at: str) -> dict:
    """자본 규모별 지표를 계산한다. 외부 I/O 없음."""
    tracks = []
    for capital in sorted(ledgers):
        orders = ledgers[capital]
        closed, open_pos = _round_trips(orders)

        for p in open_pos:
            now = last_price.get(p["ticker"])
            p["now"] = now
            p["pnl"] = round(p["qty"] * (now - p["entry"]), 2) if now else None
            p["pct"] = round((now / p["entry"] - 1) * 100, 2) if now else None
            # 손절가까지 남은 거리. 음수면 이미 손절선 아래 (다음 실행에서 청산된다).
            p["to_stop"] = round((now / p["stop"] - 1) * 100, 2) if now else None

        realized = round(sum(c["pnl"] for c in closed), 2)
        # 청산분 왕복 수수료 + 보유분 매수 수수료. 보유분 매수 수수료도 이미 현금에서
        # 나갔으므로 빼면 "수수료 차감 전" 수치가 실제보다 좋아 보인다.
        fees = round(float((orders["quantity"] * orders["price"]).sum()) * COMMISSION, 2)
        unrealized = round(sum(p["pnl"] for p in open_pos if p["pnl"] is not None), 2)
        wins = [c for c in closed if c["pnl"] > 0]
        curve = _equity_curve(orders, prices, float(capital))
        # 자산곡선이 없으면(시세 없음) 장부만으로 근사한다 — 집계가 어긋나지 않게.
        equity = curve[-1]["equity"] if curve else capital + realized + unrealized

        tracks.append({
            "capital": capital,
            "equity": round(equity, 2),
            "total_pct": round((equity / capital - 1) * 100, 2),
            "realized": realized,
            "unrealized": unrealized,
            "fees": fees,
            "gross_pct": round((equity - capital + fees) / capital * 100, 2),
            "closed": closed,
            "open": open_pos,
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "curve": curve,
        })

    # 자본이 작은 트랙은 현금이 모자라 거래를 건너뛴다. 가장 큰 자본 트랙이 전략의
    # 신호를 가장 많이 담으므로 그쪽을 분포의 기준으로 삼는다.
    reasons: dict[str, int] = {}
    for c in (max(tracks, key=lambda t: t["capital"])["closed"] if tracks else []):
        reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1

    return {"generated_at": generated_at, "tracks": tracks, "reasons": reasons,
            "commission_pct": COMMISSION * 100}


# ── I/O 껍데기 ──────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="모의매매 장부로 대시보드 JSON 생성")
    ap.add_argument("--orders-dir", default="_ledgers", help="paper_orders_*.csv 가 있는 폴더")
    ap.add_argument("--out", default="docs/paper.json")
    ap.add_argument("--capitals", type=int, nargs="+", default=[2000, 8000, 20000])
    args = ap.parse_args()

    import yfinance as yf

    orders_dir = Path(args.orders_dir)
    ledgers, tickers = {}, set()
    for cap in args.capitals:
        f = orders_dir / f"paper_orders_{cap}.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f)
        if df.empty:
            # 장부를 막 초기화하면 헤더만 있는 상태가 된다. 이걸 그대로 두면
            # start가 NaT가 되어 전 종목 시세 조회가 통째로 실패하는데도
            # 스크립트는 성공으로 끝나(exit 0) 망가진 JSON이 배포된다.
            print(f"  (건너뜀) {f.name}: 거래 기록 없음")
            continue
        ledgers[cap] = df
        tickers |= set(df["ticker"])
    if not ledgers:
        raise SystemExit(f"{orders_dir} 에서 거래 기록이 있는 장부를 찾지 못했습니다.")

    start = min(pd.to_datetime(df["filled_at"]).min() for df in ledgers.values())
    if pd.isna(start):
        raise SystemExit("장부의 filled_at을 날짜로 읽지 못했습니다.")
    px = yf.download(sorted(tickers), start=start - pd.Timedelta(days=5),
                     progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(sorted(tickers)[0])
    last_price = {t: round(float(px[t].dropna().iloc[-1]), 2) for t in px.columns if px[t].notna().any()}

    report = build_report(ledgers, px, last_price,
                          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{out} 생성 완료 — 트랙 {len(report['tracks'])}개, "
          f"청산 {sum(len(t['closed']) for t in report['tracks'])}건, "
          f"보유 {sum(len(t['open']) for t in report['tracks'])}건")


if __name__ == "__main__":
    main()
