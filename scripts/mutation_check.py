"""Do the tests actually guard the rules? Plant each bug in a temporary copy and expect pytest to fail.

    python scripts/mutation_check.py      # needs the dev requirements (pytest) installed

A mutant counts as killed only when the unmodified copy is green first and the mutated copy fails
with pytest exit code 1 (tests failed) — exit code 2+ means pytest broke, which proves nothing.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT, MAIN = "api/app/agent.py", "api/app/main.py"
MAILER, CONFIG = "api/app/mailer.py", "api/app/config.py"

MUTANTS = [  # (description, file, [(original text, replacement), ...])
    (
        "non-ASCII sender written as an RFC 2047 encoded-word (breaks Reply-To)",
        MAILER,
        [('msg["Reply-To"] = ascii_reply_to or mail.reply_to', 'msg["Reply-To"] = mail.reply_to')],
    ),
    ("auto picks 7B on any machine", CONFIG, [("ram_gib >= BIG_MODEL_MIN_RAM_GIB", "ram_gib >= 0")]),
    ("Reply-To set to the department instead of the sender", AGENT, [("reply_to=deps.sender", "reply_to=to")]),
    ("no nudge when the model answers with text only", AGENT, [("raise ModelRetry(NUDGE)", "pass")]),
    (
        "run not stopped after the mail is sent",
        AGENT,
        [("if deps.sent is not None:\n                    break", "if False:\n                    break")],
    ),
    (
        "second tool call sends a second mail",
        AGENT,
        [("if deps.sent is not None:\n            return", "if False:\n            return")],
    ),
    (
        "address outside the list accepted (schema and in-tool check both removed)",
        AGENT,
        [("to: Department,", "to: str,"), ("if to not in DEPARTMENTS:", "if False:")],
    ),
    ("parallel tool calls allowed (race: two mails)", AGENT, [("@agent.tool(sequential=True)", "@agent.tool")]),
    (
        "sender address written to the log",
        MAIN,
        [
            (
                '"routed to=%s nudges=%s seconds=%s", result.mail.to,',
                '"routed to=%s from=%s nudges=%s seconds=%s", result.mail.to, result.mail.reply_to,',
            )
        ],
    ),
]


def pytest(cwd: Path) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider"], cwd=cwd, capture_output=True, text=True
    )
    lines = r.stdout.strip().splitlines() or ["?"]
    failed = [line for line in lines if line.startswith("FAILED")]
    return r.returncode, " / ".join([lines[-1], *failed])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp)
        shutil.copytree(ROOT / "api", copy / "api", ignore=shutil.ignore_patterns("__pycache__", ".*cache"))
        shutil.copy(ROOT / "docker-compose.yml", copy / "docker-compose.yml")

        code, tail = pytest(copy / "api")
        print(f"baseline: exit {code} | {tail}")
        if code != 0:
            return 2

        survived = 0
        for name, target, edits in MUTANTS:
            original = (ROOT / target).read_text(encoding="utf-8")
            mutated = original
            for before, after in edits:
                if mutated.count(before) != 1:
                    break
                mutated = mutated.replace(before, after)
            else:
                edits = None
            if edits is not None:
                print(f"SKIPPED  {name}: pattern not found exactly once")
                survived += 1
                continue
            (copy / target).write_text(mutated, encoding="utf-8")
            code, tail = pytest(copy / "api")
            killed = code == 1
            survived += not killed
            print(f"{'KILLED  ' if killed else 'SURVIVED'} {name}: exit {code} | {tail}")
            (copy / target).write_text(original, encoding="utf-8")

    print(f"{len(MUTANTS) - survived}/{len(MUTANTS)} mutants killed")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
