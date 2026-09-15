#!/usr/bin/env python3
"""Check in to the operational dead man's switch.

Usage:
  python scripts/dead_man_check_in.py https://your-app.up.railway.app "$DEAD_MAN_SWITCH_TOKEN"
  python scripts/dead_man_check_in.py http://127.0.0.1:8000 "$DEAD_MAN_SWITCH_TOKEN" --note "weekly"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Dead man's switch check-in")
    parser.add_argument("base_url", help="Deployment origin, e.g. https://app.example.com")
    parser.add_argument("token", help="DEAD_MAN_SWITCH_TOKEN value")
    parser.add_argument("--note", default="", help="Optional operator note")
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    path = "/status" if args.status_only else "/check-in"
    url = f"{base}/api/v1/ops/dead-man-switch{path}"
    headers = {
        "X-Dead-Man-Switch-Token": args.token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = None if args.status_only else json.dumps({"note": args.note or None}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if args.status_only else "POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(exc.read().decode() or str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(body, indent=2))
    if body.get("tripped"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
