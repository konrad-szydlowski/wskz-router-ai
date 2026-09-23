"""One end-to-end check against the running stack: the README example must produce exactly one mail
in Mailpit, addressed to one of the five departments, with Reply-To = sender (standard library only).

    python scripts/smoke.py                      # API on :8000, Mailpit UI on :8025
    python scripts/smoke.py --api http://127.0.0.1:8000 --mail http://127.0.0.1:8025
"""

import argparse
import json
import sys
import time
import urllib.request

DEPARTMENTS = {
    "human-resources@example.com",
    "help-desk@example.com",
    "it@example.com",
    "kadry@example.com",
    "other@example.com",
}
EXAMPLE = {"email": "jan.kowalski@firma.pl", "message": "Dzień dobry, od rana nie mogę zalogować się do VPN."}


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read() or b"{}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--mail", default="http://127.0.0.1:8025")
    args = p.parse_args()

    before = call("GET", f"{args.mail}/api/v1/messages?limit=1")["total"]
    reply = call("POST", f"{args.api}/api/v1/messages", EXAMPLE)
    time.sleep(0.5)  # SMTP -> Mailpit storage is asynchronous
    inbox = call("GET", f"{args.mail}/api/v1/messages?limit=5")
    new = inbox["messages"][: inbox["total"] - before]

    checks = {
        "API answered status=sent via tool call": reply.get("status") == "sent"
        and reply.get("sent_by") == "agent_tool_call",
        "exactly one new mail": len(new) == 1,
        "recipient is one of the five departments": len(new) == 1
        and [a["Address"] for a in new[0]["To"]] == [reply.get("department")]
        and reply.get("department") in DEPARTMENTS,
        "Reply-To is the sender": len(new) == 1
        and [a["Address"] for a in new[0].get("ReplyTo") or []] == [EXAMPLE["email"]],
    }
    print(json.dumps(reply, ensure_ascii=False))
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
