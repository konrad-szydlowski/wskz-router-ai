"""End-to-end evaluation against the running stack (standard library only).

Every case goes through the real path: POST /api/v1/messages -> agent -> tool -> SMTP -> Mailpit,
and the delivered mail is checked in Mailpit (recipient, Reply-To, exactly one mail, no Bcc).
Each case runs --runs times because a language model is not deterministic.

    python eval/run_eval.py                      # 3 runs, defaults to localhost ports
    python eval/run_eval.py --dataset holdout    # 20 cases never used while tuning the prompt
    python eval/run_eval.py --runs 1 --api http://127.0.0.1:8000 --mail http://127.0.0.1:8025

Each request uses its own sender address with a random token and its mail is looked up by that token,
so other traffic in the same Mailpit cannot be counted as this case's mail. Runs on Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).parent
DEPARTMENTS = {
    "it@example.com",
    "help-desk@example.com",
    "kadry@example.com",
    "human-resources@example.com",
    "other@example.com",
}


def http(method: str, url: str, body: dict | None = None, timeout: float = 300) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mails_with(mail: str, token: str, expect_one: bool) -> list[dict]:
    """Mails carrying this request's token (in the sender address, i.e. Reply-To and body)."""
    found: list[dict] = []
    for _ in range(20 if expect_one else 4):  # SMTP -> Mailpit storage is asynchronous
        time.sleep(0.25)
        found = http("GET", f"{mail}/api/v1/search?query={token}")[1]["messages"]
        if found:
            break
    time.sleep(0.5)  # a duplicate, if any, would arrive right behind
    return http("GET", f"{mail}/api/v1/search?query={token}")[1]["messages"]


def run_case(case: dict, api: str, mail: str) -> dict:
    token = uuid.uuid4().hex[:12]
    sender = f"eval{token}@firma.pl"
    started = time.perf_counter()
    status, body = http("POST", f"{api}/api/v1/messages", {"email": sender, "message": case["message"]})
    latency = round(time.perf_counter() - started, 2)
    mails = mails_with(mail, token, expect_one=status == 200)
    to = [a["Address"] for m in mails for a in m["To"]]
    reply_to = [a["Address"] for m in mails for a in (m.get("ReplyTo") or [])]
    bcc = [a["Address"] for m in mails for a in (m.get("Bcc") or [])]
    dept = to[0] if len(to) == 1 else None
    return {
        "id": case["id"],
        "http": status,
        "latency_s": latency,
        "nudges": body.get("nudges"),
        "mails": len(mails),
        "to": to,
        "one_mail": len(mails) == (1 if status == 200 else 0),
        "valid_department": dept in DEPARTMENTS if status == 200 else None,
        "reply_to_ok": reply_to == [sender] if status == 200 else None,
        "no_bcc": not bcc,
        "hit_expected": dept == case["expected"],
        "hit_acceptable": dept in case["acceptable"],
    }


def summarise(results: list[dict], cases: list[dict], runs: int) -> dict:
    n = len(results)
    by_case = {c["id"]: [r for r in results if r["id"] == c["id"]] for c in cases}
    lat = sorted(r["latency_s"] for r in results if r["http"] == 200)
    return {
        "requests": n,
        "runs_per_case": runs,
        "sent_by_tool": sum(r["http"] == 200 for r in results),
        "http_502_agent_did_not_send": sum(r["http"] == 502 for r in results),
        "other_errors": sum(r["http"] not in (200, 502) for r in results),
        "exactly_one_mail_or_none_on_error": sum(r["one_mail"] for r in results),
        "valid_department": sum(bool(r["valid_department"]) for r in results),
        "reply_to_ok": sum(bool(r["reply_to_ok"]) for r in results),
        "no_bcc": sum(r["no_bcc"] for r in results),
        "hit_expected": sum(r["hit_expected"] for r in results),
        "hit_acceptable": sum(r["hit_acceptable"] for r in results),
        "cases_correct_in_all_runs": sum(all(r["hit_acceptable"] for r in rs) for rs in by_case.values()),
        "cases": len(cases),
        "needed_nudge": sum((r["nudges"] or 0) > 0 for r in results),
        "latency_median_s": statistics.median(lat) if lat else None,
        "latency_p90_s": lat[int(0.9 * (len(lat) - 1))] if lat else None,
        "misses": sorted({f"{r['id']}→{','.join(r['to']) or r['http']}" for r in results if not r["hit_acceptable"]}),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--mail", default="http://127.0.0.1:8025")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument(
        "--dataset", default="dataset", choices=["dataset", "holdout"], help="holdout = never used for tuning"
    )
    p.add_argument("--out", default=None, help="results JSON path (default eval/results/<model>-<dataset>.json)")
    args = p.parse_args()

    status, health = http("GET", f"{args.api}/api/v1/health")
    if status != 200:
        print(f"API not ready ({status}): {health}", file=sys.stderr)
        return 2
    model = health["model"]
    cases = [
        json.loads(line) for line in (HERE / f"{args.dataset}.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]

    results = []
    for run in range(1, args.runs + 1):
        for case in cases:
            r = {**run_case(case, args.api, args.mail), "run": run}
            results.append(r)
            mark = "OK " if r["hit_acceptable"] else "MISS"
            print(
                f"[{run}] {mark} {r['latency_s']:6.2f}s {r['http']} {','.join(r['to']) or '-':28} {case['id']}",
                flush=True,
            )

    summary = summarise(results, cases, args.runs)
    out = Path(args.out) if args.out else HERE / "results" / f"{model.replace(':', '_')}-{args.dataset}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"model": model, "dataset": args.dataset, "summary": summary, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved {out}")
    # Hard invariants (never negotiable): valid address, Reply-To, one mail per request, no Bcc.
    hard_ok = all(
        r["one_mail"] and r["no_bcc"] and (r["http"] != 200 or (r["valid_department"] and r["reply_to_ok"]))
        for r in results
    )
    return 0 if hard_ok else 1


if __name__ == "__main__":
    sys.exit(main())
