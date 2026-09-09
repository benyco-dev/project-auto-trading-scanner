#!/usr/bin/env python3
"""
toss_client.py

토스증권 Open API 클라이언트. 공식 문서(openapi.tossinvest.com) 기준으로 작성.

인증: OAuth2 client_credentials
  POST https://openapi.tossinvest.com/oauth2/token

읽기 전용: get_prices, get_accounts, get_holdings, get_orders
주문(실주문 가능): place_order — 반드시 dry_run=True가 기본값.

자격 증명은 코드에 절대 하드코딩하지 않는다 — 환경변수(TOSS_CLIENT_ID,
TOSS_CLIENT_SECRET) 또는 .env 파일에서만 읽는다. 자세한 발급/보관 방법은
SETUP.md 참고.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_URL = f"{BASE_URL}/oauth2/token"


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def load_credentials() -> tuple[str, str]:
    client_id = os.environ.get("TOSS_CLIENT_ID")
    client_secret = os.environ.get("TOSS_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    env_file = _load_env_file(Path(__file__).resolve().parent.parent / ".env")
    client_id = client_id or env_file.get("TOSS_CLIENT_ID")
    client_secret = client_secret or env_file.get("TOSS_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 찾을 수 없습니다. "
            "환경변수로 export 하거나 .env 파일에 넣어주세요. (SETUP.md 참고)"
        )
    return client_id, client_secret


class TossAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, data: dict | None = None):
        super().__init__(f"[{status}] {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.data = data or {}


class TossClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        # 자격 증명은 실제로 API를 호출하는 시점(_fetch_token)까지 미룬다 —
        # dry-run 미리보기는 토큰/키 없이도 돌아가야 하기 때문.
        self.client_id: str | None = client_id
        self.client_secret: str | None = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self.session = requests.Session()

    # ── 인증 ──────────────────────────────────────────────────────

    def _fetch_token(self) -> None:
        if not (self.client_id and self.client_secret):
            self.client_id, self.client_secret = load_credentials()
        resp = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        self._raise_for_error(resp)
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + body.get("expires_in", 3600) - 30  # 30초 여유

    def _access_token(self) -> str:
        if self._token is None or time.time() >= self._token_expiry:
            self._fetch_token()
        return self._token

    @staticmethod
    def _raise_for_error(resp: requests.Response) -> None:
        if resp.ok:
            return
        try:
            body = resp.json()
        except ValueError:
            resp.raise_for_status()
            return

        err = body.get("error", {})
        if isinstance(err, dict):
            # 일반 API 에러 envelope: {"error": {"code", "message", "data"}}
            raise TossAPIError(
                resp.status_code, err.get("code", "unknown"), err.get("message", resp.text), err.get("data")
            )
        # OAuth2 표준 토큰 에러: {"error": "invalid_client", "error_description": "..."}
        raise TossAPIError(
            resp.status_code, str(err) or "unknown", body.get("error_description", resp.text)
        )

    def _request(
        self, method: str, path: str, account_seq: int | None = None, retries: int = 3, **kwargs
    ) -> dict:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token()}"
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)

        for attempt in range(retries):
            resp = self.session.request(method, f"{BASE_URL}{path}", headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                self._token = None  # 토큰 만료 -> 재발급 후 1회 재시도
                headers["Authorization"] = f"Bearer {self._access_token()}"
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            self._raise_for_error(resp)
            return resp.json()
        self._raise_for_error(resp)
        return {}

    # ── 시세 (토큰만 필요) ────────────────────────────────────────

    def get_prices(self, symbols: list[str]) -> dict:
        return self._request("GET", "/api/v1/prices", params={"symbols": ",".join(symbols)})

    # ── 계좌/자산 (계좌 헤더 필요) ────────────────────────────────

    def get_accounts(self) -> dict:
        return self._request("GET", "/api/v1/accounts")

    def get_holdings(self, account_seq: int) -> dict:
        return self._request("GET", "/api/v1/holdings", account_seq=account_seq)

    def get_buying_power(self, account_seq: int, currency: str = "USD") -> float:
        r = self._request(
            "GET", "/api/v1/buying-power", account_seq=account_seq, params={"currency": currency}
        )
        return float(r["result"]["cashBuyingPower"])

    def get_orders(self, account_seq: int) -> dict:
        return self._request("GET", "/api/v1/orders", account_seq=account_seq)

    # ── 주문 (실제 돈이 움직이는 부분 — dry_run 기본 True) ────────

    def place_order(
        self,
        account_seq: int,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str = "LIMIT",
        dry_run: bool = True,
    ) -> dict:
        if side not in ("BUY", "SELL"):
            raise ValueError("side는 BUY 또는 SELL이어야 합니다.")
        body = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "orderType": order_type,
        }
        if dry_run:
            return {"dry_run": True, "would_submit": body, "account_seq": account_seq}

        if os.environ.get("TOSS_LIVE_TRADING") != "CONFIRM":
            raise RuntimeError(
                "실주문을 막았습니다: 환경변수 TOSS_LIVE_TRADING=CONFIRM 이 설정되어 있지 않습니다. "
                "이건 실수로 실거래가 나가는 걸 막기 위한 이중 안전장치입니다."
            )
        return self._request("POST", "/api/v1/orders", account_seq=account_seq, json=body)

    def cancel_order(self, account_seq: int, order_id: str, dry_run: bool = True) -> dict:
        if dry_run:
            return {"dry_run": True, "would_cancel": order_id, "account_seq": account_seq}
        if os.environ.get("TOSS_LIVE_TRADING") != "CONFIRM":
            raise RuntimeError("실주문(취소)을 막았습니다: TOSS_LIVE_TRADING=CONFIRM 필요.")
        return self._request("POST", f"/api/v1/orders/{order_id}/cancel", account_seq=account_seq)


if __name__ == "__main__":
    # 연결 확인용: 토큰 발급 + 계좌 목록 조회만 해본다 (주문 없음, 읽기 전용).
    client = TossClient()
    accounts = client.get_accounts()
    print("계좌 목록:", accounts)
