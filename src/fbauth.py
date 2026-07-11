"""Exchange a short-lived Facebook user token for long-lived Page tokens.

A Page token derived from a long-lived user token does not expire while you
stay an admin and the app/password are unchanged -- so this is run once per
Page to get a token that stops the hourly-expiry problem.
"""

from __future__ import annotations

import requests

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 30


class FbAuthError(RuntimeError):
    """Raised when the token exchange or lookup fails."""


def _err(resp: requests.Response) -> str:
    try:
        return (resp.json().get("error") or {}).get("message", resp.text[:200])
    except ValueError:
        return resp.text[:200]


def long_lived_page_tokens(app_id: str, app_secret: str,
                           user_token: str) -> list[dict]:
    """Return [{id, name, access_token}] with LONG-LIVED Page tokens.

    1. Exchange the short-lived user token for a long-lived user token.
    2. Call /me/accounts with it; the Page access_tokens returned are long-lived.
    """
    exch = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": user_token,
        },
        timeout=TIMEOUT,
    )
    if exch.status_code != 200:
        raise FbAuthError(f"Token exchange failed: {_err(exch)}")
    long_user = exch.json().get("access_token")
    if not long_user:
        raise FbAuthError("Facebook did not return a long-lived user token.")

    acc = requests.get(
        f"{GRAPH}/me/accounts",
        params={"fields": "id,name,access_token", "access_token": long_user},
        timeout=TIMEOUT,
    )
    if acc.status_code != 200:
        raise FbAuthError(f"Could not list your Pages: {_err(acc)}")
    return acc.json().get("data", [])
