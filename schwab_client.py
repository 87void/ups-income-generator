
import os
import json
import time
import base64
import re
from pathlib import Path
from datetime import date, timedelta
from urllib.parse import quote

import requests
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

CLIENT_ID = os.getenv("SCHWAB_CLIENT_ID")
CLIENT_SECRET = os.getenv("SCHWAB_CLIENT_SECRET")
TOKEN_URL = os.getenv("SCHWAB_TOKEN_URL")

TOKEN_FILE = PROJECT_DIR / "schwab_tokens.json"
API_BASE = "https://api.schwabapi.com"


class SchwabClient:

    def __init__(self):
        if not CLIENT_ID or not CLIENT_SECRET or not TOKEN_URL:
            raise RuntimeError("Missing Schwab configuration in .env")

        self.session = requests.Session()
        self.timeout = 30
        self.tokens = {}
        self.token_obtained_at = 0

        self._load_tokens()

    def _load_tokens(self):
        if not TOKEN_FILE.exists():
            raise RuntimeError("Token file not found. Run schwab_auth.py.")

        self.tokens = json.loads(TOKEN_FILE.read_text())

        if not self.tokens.get("access_token"):
            raise RuntimeError("Access token missing.")

        if not self.tokens.get("refresh_token"):
            raise RuntimeError("Refresh token missing.")

        self.token_obtained_at = float(
            self.tokens.get("_obtained_at", TOKEN_FILE.stat().st_mtime)
        )

    def _save_tokens(self):
        self.tokens["_obtained_at"] = time.time()

        temporary_file = TOKEN_FILE.with_name(
            TOKEN_FILE.name + ".tmp"
        )

        temporary_file.write_text(
            json.dumps(self.tokens, indent=2)
        )

        temporary_file.chmod(0o600)
        os.replace(temporary_file, TOKEN_FILE)
        TOKEN_FILE.chmod(0o600)

        self.token_obtained_at = self.tokens["_obtained_at"]

    def _refresh_token(self):
        print("Refreshing Schwab access token...")

        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded = base64.b64encode(
            credentials.encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.tokens["refresh_token"],
        }

        response = self.session.post(
            TOKEN_URL,
            headers=headers,
            data=data,
            timeout=self.timeout,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Schwab token refresh failed: HTTP "
                f"{response.status_code}. "
                "You may need to authenticate again using schwab_auth.py."
            )

        updated = response.json()

        if not updated.get("access_token"):
            raise RuntimeError("Schwab did not return a new access token.")

        # Preserve the existing refresh token if Schwab
        # does not include a replacement in the response.
        if not updated.get("refresh_token"):
            updated["refresh_token"] = self.tokens["refresh_token"]

        self.tokens.update(updated)
        self._save_tokens()

        print("Schwab token refreshed successfully.")

    def _get_access_token(self):
        expires_in = int(self.tokens.get("expires_in", 1800))

        expires_at = self.token_obtained_at + expires_in

        # Refresh five minutes before expiration.
        if time.time() >= expires_at - 300:
            self._refresh_token()

        return self.tokens["access_token"]

    def _get(self, endpoint, params=None):
        headers = {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/json",
        }

        response = self.session.get(
            f"{API_BASE}{endpoint}",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )

        # Retry once after refreshing an expired access token.
        if response.status_code == 401:
            self._refresh_token()

            headers["Authorization"] = (
                f"Bearer {self.tokens['access_token']}"
            )

            response = self.session.get(
                f"{API_BASE}{endpoint}",
                headers=headers,
                params=params,
                timeout=self.timeout,
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Schwab API error: HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

        return response.json()

    @staticmethod
    def _validate_symbol(symbol):
        symbol = symbol.upper().strip()

        if not re.fullmatch(r"[A-Z0-9.^-]{1,15}", symbol):
            raise ValueError("Invalid stock symbol.")

        return symbol

    def get_quote(self, symbol):
        symbol = self._validate_symbol(symbol)

        endpoint = (
            f"/marketdata/v1/{quote(symbol, safe='')}/quotes"
        )

        return self._get(endpoint)

    def get_option_chain(self, symbol="UPS"):
        symbol = self._validate_symbol(symbol)

        start_date = date.today() + timedelta(days=30)
        end_date = date.today() + timedelta(days=45)

        params = {
            "symbol": symbol,
            "contractType": "CALL",
            "includeUnderlyingQuote": True,
            "fromDate": start_date.isoformat(),
            "toDate": end_date.isoformat(),
        }

        return self._get(
            "/marketdata/v1/chains",
            params=params,
        )


if __name__ == "__main__":
    client = SchwabClient()

    print("\nTesting UPS stock quote...")
    quote_data = client.get_quote("UPS")
    print("UPS quote request: SUCCESS")
    print("Response fields:", list(quote_data.keys()))

    print("\nTesting UPS call options...")
    chain = client.get_option_chain("UPS")

    print("UPS options request: SUCCESS")
    print("Response fields:", list(chain.keys()))

    expiration_map = chain.get("callExpDateMap", {})

    contract_count = sum(
        len(contracts)
        for strikes in expiration_map.values()
        for contracts in strikes.values()
    )

    print("Expiration groups:", len(expiration_map))
    print("Call contracts returned:", contract_count)

    print("\nSchwab market data test completed.")
