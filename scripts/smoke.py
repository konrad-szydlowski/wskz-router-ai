"""One end-to-end check against the running stack: the README example must produce exactly one mail
in Mailpit, addressed to one of the five departments, with Reply-To = sender (standard library only).

    python scripts/smoke.py                      # API on :8000, Mailpit UI on :8025
    python scripts/smoke.py --api http://127.0.0.1:8000 --mail http://127.0.0.1:8025

The sender address carries a random token and the mail is looked up by it, so other traffic in the
same Mailpit cannot be mistaken for this request's mail. Runs on Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

DEPARTMENTS = {
    "human-resources@example.com",
    "help-desk@example.com",
    "it@example.com",
    "kadry@example.com",
    "other@example.com",
}
MESSAGE = "Dzień dobry, od rana nie mogę zalogować się do VPN."


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:  # 4xx/5xx: keep the body, it says what went wrong
        raw, code = e.read(), e.code
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"http": code, "body": raw[:200].decode(errors="replace")}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--mail", default="http://127.0.0.1:8025")
    args = p.parse_args()

    try:
        return run(args.api, args.mail)
    except urllib.error.URLError as e:  # nothing listening: stack not up or wrong port
        print(f"FAIL  cannot reach the stack ({e.reason}) — is `docker compose up -d --wait` done?")
        return 1


def run(api: str, mail: str) -> int:
    token = uuid.uuid4().hex[:12]
    sender = f"smoke{token}@firma.pl"
    reply = call("POST", f"{api}/api/v1/messages", {"email": sender, "message": MESSAGE})
    new: list[dict] = []
    for _ in range(20 if reply.get("status") == "sent" else 4):  # SMTP -> Mailpit storage is asynchronous
        time.sleep(0.25)
        new = call("GET", f"{mail}/api/v1/search?query={token}")["messages"]
        if new:
            time.sleep(0.5)  # a duplicate, if any, would arrive right behind
            new = call("GET", f"{mail}/api/v1/search?query={token}")["messages"]
            break

    checks = {
        "API answered status=sent via tool call": reply.get("status") == "sent"
        and reply.get("sent_by") == "agent_tool_call",
        "exactly one new mail": len(new) == 1,
        "recipient is one of the five departments": len(new) == 1
        and [a["Address"] for a in new[0]["To"]] == [reply.get("department")]
        and reply.get("department") in DEPARTMENTS,
        "Reply-To is the sender": len(new) == 1 and [a["Address"] for a in new[0].get("ReplyTo") or []] == [sender],
    }
    print(json.dumps(reply, ensure_ascii=False))
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
