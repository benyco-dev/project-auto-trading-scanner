#!/usr/bin/env python3
"""
dashboard/report.py

모의매매 장부(paper_orders_*.csv)를 읽어 정적 대시보드(docs/index.html)를 만든다.

장부는 VM에만 있고 저장소에는 커밋하지 않는다(*.csv는 .gitignore). 대신 계산한
결과를 HTML 안에 넣어 한 파일로 배포한다 — GitHub Pages가 그대로 서빙한다.

구조:
  build_report()  순수 함수. 장부 + 시세를 받아 지표/자산곡선을 계산한다.
                  I/O 없음 — 테스트에서 가짜 데이터로 그대로 호출할 수 있다.
  render_html()   순수 함수. 계산 결과를 HTML 문자열로.
  main()          I/O 껍데기. CSV 읽기, yfinance 조회, 파일 쓰기.

사용:
  python -m dashboard.report --orders-dir ./_ledgers --out docs/index.html
"""

from __future__ import annotations

import argparse
import html
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


# ── 렌더링 ──────────────────────────────────────────────────────────


def _sparkline(curve: list[dict], capital: float, w=560, h=120) -> str:
    if len(curve) < 2:
        return '<p class="muted">자산 곡선을 그리기엔 데이터가 부족합니다.</p>'
    vals = [c["equity"] for c in curve]
    lo, hi = min(vals + [capital]), max(vals + [capital])
    span = (hi - lo) or 1
    pts = " ".join(
        f"{i / (len(vals) - 1) * w:.1f},{h - (v - lo) / span * h:.1f}"
        for i, v in enumerate(vals)
    )
    base = h - (capital - lo) / span * h
    up = vals[-1] >= capital
    color = "var(--up)" if up else "var(--down)"
    return (
        f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" '
        f'aria-label="자산 곡선 {curve[0]["date"]}~{curve[-1]["date"]}">'
        f'<line x1="0" y1="{base:.1f}" x2="{w}" y2="{base:.1f}" class="baseline"/>'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'
        f"</svg>"
    )


def _esc(v) -> str:
    """HTML에 넣는 모든 외부 유래 값은 이스케이프한다. 티커는 위키피디아 S&P500
    목록을 긁어온 값이라(fetch_sp500_tickers) 제3자가 바꿀 수 있는 입력이고,
    이 페이지는 공개 사이트로 배포된다."""
    return html.escape(str(v), quote=True)


def _fmt(v, suffix="", plus=False):
    if v is None:
        return '<span class="muted">—</span>'
    cls = "up" if v > 0 else ("down" if v < 0 else "")
    sign = "+" if plus and v > 0 else ""
    return f'<span class="{cls}">{sign}{v:,.2f}{suffix}</span>'


def render_html(report: dict) -> str:
    cards = []
    for t in report["tracks"]:
        closed_rows = "".join(
            f"<tr><td>{_esc(c['ticker'])}</td><td class='num'>{c['qty']}</td>"
            f"<td class='num'>{c['entry']:,.2f}</td><td class='num'>{c['exit']:,.2f}</td>"
            f"<td><span class='tag tag-{_esc(c['reason'].split('(')[0])}'>{_esc(c['reason'])}</span></td>"
            f"<td class='num'>{_fmt(c['pct'], '%', True)}</td>"
            f"<td class='num'>{_fmt(c['pnl'], '', True)}</td>"
            f"<td class='num muted'>{_esc(c['entry_date'])} → {_esc(c['exit_date'])}</td></tr>"
            for c in sorted(t["closed"], key=lambda x: x["exit_date"], reverse=True)
        ) or "<tr><td colspan='8' class='muted'>아직 청산된 트레이드가 없습니다.</td></tr>"

        open_rows = "".join(
            f"<tr><td>{_esc(p['ticker'])}</td><td class='num'>{p['qty']}</td>"
            f"<td class='num'>{p['entry']:,.2f}</td>"
            # 시세를 못 가져온 종목(상폐·티커 변경·조회 실패)이 하나만 있어도
            # 페이지 전체 생성이 실패하면 안 된다. 그 칸만 비운다.
            f"<td class='num'>{_fmt(p['now'])}</td>"
            f"<td class='num'>{_fmt(p['pct'], '%', True)}</td>"
            f"<td class='num'>{_fmt(p['pnl'], '', True)}</td>"
            f"<td class='num'>{p['stop']:,.2f}</td>"
            f"<td class='num'>{_fmt(p['to_stop'], '%')}</td>"
            f"<td class='num muted'>{_esc(p['entry_date'])}</td></tr>"
            for p in sorted(t["open"], key=lambda x: x["entry_date"], reverse=True)
        ) or "<tr><td colspan='9' class='muted'>보유 중인 포지션이 없습니다.</td></tr>"

        cards.append(f"""
<section class="card">
  <header class="card-head">
    <h2>가정 자본 ${t['capital']:,}</h2>
    <div class="kpi">
      <div><span class="label">현재 자산</span><strong>${t['equity']:,.2f}</strong></div>
      <div><span class="label">수익률 (수수료 후)</span><strong>{_fmt(t['total_pct'], '%', True)}</strong></div>
      <div><span class="label">수수료 차감 전</span><span>{_fmt(t['gross_pct'], '%', True)}</span></div>
      <div><span class="label">낸 수수료</span><span>${t['fees']:,.2f}</span></div>
      <div><span class="label">승률</span><span>{'—' if t['win_rate'] is None else f"{t['win_rate']}%"} <span class="muted">({len(t['closed'])}건)</span></span></div>
    </div>
  </header>
  {_sparkline(t['curve'], t['capital'])}
  <h3>청산 완료 <span class="muted">실현 {_fmt(t['realized'], '', True)}</span></h3>
  <div class="scroll"><table>
    <thead><tr><th>종목</th><th class="num">수량</th><th class="num">진입</th><th class="num">청산</th>
    <th>사유</th><th class="num">수익률</th><th class="num">손익(수수료후)</th><th class="num">기간</th></tr></thead>
    <tbody>{closed_rows}</tbody>
  </table></div>
  <h3>보유 중 <span class="muted">평가 {_fmt(t['unrealized'], '', True)}</span></h3>
  <div class="scroll"><table>
    <thead><tr><th>종목</th><th class="num">수량</th><th class="num">진입</th><th class="num">현재가</th>
    <th class="num">수익률</th><th class="num">평가손익</th><th class="num">손절가</th>
    <th class="num">손절까지</th><th class="num">매수일</th></tr></thead>
    <tbody>{open_rows}</tbody>
  </table></div>
</section>""")

    reasons = " · ".join(f"{_esc(k)} {v}건" for k, v in report["reasons"].items()) or "—"
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RSI(2) 자동매매 — 모의매매 현황</title>
<meta name="description" content="RSI(2) 평균회귀 전략 자동매매 봇의 모의매매(paper trading) 결과. 실제 주문은 체결되지 않습니다.">
<link rel="preconnect" href="https://cdn.jsdelivr.net">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<style>
  :root {{
    --bg:#0f1115; --card:#171a21; --line:#242934; --text:#e6e8ec; --muted:#8b93a3;
    --up:#2ecc71; --down:#ff6b6b; --accent:#4c8dff;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:Pretendard,system-ui,sans-serif;
         line-height:1.6; padding:32px 20px 64px; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:1.6rem; margin:0 0 4px; }}
  h2 {{ font-size:1.15rem; margin:0; }}
  h3 {{ font-size:0.95rem; margin:24px 0 8px; font-weight:600; }}
  .muted {{ color:var(--muted); font-weight:400; }}
  .banner {{ background:#1d2430; border:1px solid var(--accent); border-radius:10px;
             padding:12px 16px; margin:16px 0 28px; font-size:0.9rem; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
           padding:20px; margin-bottom:24px; }}
  .card-head {{ display:flex; flex-wrap:wrap; gap:16px; justify-content:space-between; align-items:flex-start; }}
  .kpi {{ display:flex; flex-wrap:wrap; gap:18px; }}
  .kpi .label {{ display:block; font-size:0.72rem; color:var(--muted); }}
  .kpi strong {{ font-size:1.05rem; }}
  .spark {{ width:100%; height:auto; margin:16px 0 4px; }}
  .baseline {{ stroke:var(--muted); stroke-dasharray:3 4; stroke-width:1; }}
  .scroll {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.86rem; }}
  th, td {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
  th {{ color:var(--muted); font-weight:500; font-size:0.78rem; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .up {{ color:var(--up); }} .down {{ color:var(--down); }}
  .tag {{ font-size:0.72rem; padding:2px 7px; border-radius:20px; border:1px solid var(--line); }}
  .tag-stop {{ color:var(--down); border-color:var(--down); }}
  .tag-target_sma {{ color:var(--up); border-color:var(--up); }}
  footer {{ color:var(--muted); font-size:0.8rem; margin-top:32px; }}
  a {{ color:var(--accent); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>RSI(2) 자동매매 — 모의매매 현황</h1>
  <p class="muted">200일선 위 + RSI(2)&lt;5 진입 · SMA10 익절 / ATR×2.5 손절 / 20거래일 시간청산 ·
     트레이드당 리스크 1% · 하루 최대 5종목</p>

  <div class="banner">
    <strong>이건 모의매매입니다.</strong> 실제 주문은 단 한 건도 나가지 않았고, 아래 숫자는
    같은 전략을 가정 자본으로 굴렸을 때의 기록입니다. 수수료는 토스증권 미국주식 기준
    {report['commission_pct']}%(왕복 {report['commission_pct'] * 2:.1f}%)를 모든 체결에 반영했습니다.
  </div>

  <p class="muted">청산 사유 분포 · {reasons}</p>
  {''.join(cards)}

  <footer>
    마지막 갱신 {_esc(report['generated_at'])} · 시세 출처 Yahoo Finance(지연) ·
    글꼴 <a href="https://github.com/orioncactus/pretendard">Pretendard</a> (SIL OFL 1.1) ·
    <a href="https://github.com/benyco-dev/project-auto-trading-scanner">소스 코드</a>
  </footer>
</div>
</body>
</html>
"""


# ── I/O 껍데기 ──────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="모의매매 장부로 정적 대시보드 생성")
    ap.add_argument("--orders-dir", default="_ledgers", help="paper_orders_*.csv 가 있는 폴더")
    ap.add_argument("--out", default="docs/index.html")
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
        ledgers[cap] = df
        tickers |= set(df["ticker"])
    if not ledgers:
        raise SystemExit(f"{orders_dir} 에서 장부를 찾지 못했습니다.")

    start = min(pd.to_datetime(df["filled_at"]).min() for df in ledgers.values())
    px = yf.download(sorted(tickers), start=start - pd.Timedelta(days=5),
                     progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(sorted(tickers)[0])
    last_price = {t: round(float(px[t].dropna().iloc[-1]), 2) for t in px.columns if px[t].notna().any()}

    report = build_report(ledgers, px, last_price,
                          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report), encoding="utf-8")
    print(f"{out} 생성 완료 — 트랙 {len(report['tracks'])}개, "
          f"청산 {sum(len(t['closed']) for t in report['tracks'])}건, "
          f"보유 {sum(len(t['open']) for t in report['tracks'])}건")


if __name__ == "__main__":
    main()
