"""Telegram long-polling driver — edge code, not pack code.

Runs OUTSIDE the reactive graph: polls the Bot API's getUpdates and hands
each update to a running assistant, either over HTTP (the demo server's
POST /channels/telegram/update) or in-process via a callback. The pack
itself never polls, sleeps, or reads tokens — that is this driver's job,
exactly as the schedule pack's tick driver owns the clock.

Usage:
    TELEGRAM_BOT_TOKEN=... python -m packs.telegram.poller \
        --server http://localhost:7788

The token is read from the environment here ONLY to authenticate the
getUpdates long-poll; it is never sent to the assistant (inbound updates
are user content). Outbound sends resolve their own credential through the
Secrets Pack inside the gateway.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def _get_updates(api_base: str, token: str, offset: int, timeout: int) -> list[dict]:
    qs = urllib.parse.urlencode({"offset": offset, "timeout": timeout})
    url = f"{api_base}/bot{token}/getUpdates?{qs}"
    with urllib.request.urlopen(url, timeout=timeout + 10) as resp:
        body = json.loads(resp.read().decode())
    return body.get("result", []) if body.get("ok") else []


def _post_to_server(server: str, update: dict) -> None:
    req = urllib.request.Request(
        f"{server.rstrip('/')}/channels/telegram/update",
        data=json.dumps(update).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=60).read()


def run_poller(server: str, *, api_base: str = "https://api.telegram.org",
               poll_timeout: int = 50) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)

    offset = 0
    print(f"[telegram-poller] long-polling → {server}", flush=True)
    while True:
        try:
            for update in _get_updates(api_base, token, offset, poll_timeout):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                _post_to_server(server, update)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"[telegram-poller] {type(exc).__name__}: {exc}; retrying in 5s",
                  file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram long-polling driver")
    parser.add_argument("--server", default="http://localhost:7788",
                        help="Assistant server base URL")
    args = parser.parse_args()
    run_poller(args.server)
