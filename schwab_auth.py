
import os
import json
import base64
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("SCHWAB_CLIENT_ID")
CLIENT_SECRET = os.getenv("SCHWAB_CLIENT_SECRET")
AUTH_URL = os.getenv("SCHWAB_AUTH_URL")
TOKEN_URL = os.getenv("SCHWAB_TOKEN_URL")
REDIRECT_URI = os.getenv("SCHWAB_REDIRECT_URI")

TOKEN_FILE = Path("schwab_tokens.json")


def validate_config():
    required = {
        "SCHWAB_CLIENT_ID": CLIENT_ID,
        "SCHWAB_CLIENT_SECRET": CLIENT_SECRET,
        "SCHWAB_AUTH_URL": AUTH_URL,
        "SCHWAB_TOKEN_URL": TOKEN_URL,
        "SCHWAB_REDIRECT_URI": REDIRECT_URI,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        raise ValueError(
            "Missing configuration in .env: " + ", ".join(missing)
        )


def authenticate():
    validate_config()

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "readonly",
    }

    authorization_url = f"{AUTH_URL}?{urlencode(params)}"

    print("Opening Schwab authorization page...")
    webbrowser.open(authorization_url)

    print("\n1. Sign in to Schwab.")
    print("2. Approve the requested access.")
    print("3. Copy the complete URL from the browser redirect.")
    print("4. Paste that URL below.\n")

    redirected_url = input("Paste redirect URL: ").strip()

    query = parse_qs(urlparse(redirected_url).query)
    code = query.get("code", [None])[0]

    if not code:
        raise ValueError(
            "Authorization code not found. Check the redirect URL."
        )

    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(
        credentials.encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(
        TOKEN_URL,
        headers=headers,
        data=data,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Schwab token request failed: "
            f"{response.status_code} {response.text}"
        )

    tokens = response.json()
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))

    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass

    print("\nSchwab authentication successful!")
    print(f"Tokens saved locally to {TOKEN_FILE}.")


if __name__ == "__main__":
    authenticate()
