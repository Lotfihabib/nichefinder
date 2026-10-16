"""Abort a push if staged changes or unpushed commits contain Etsy credentials.

Checks `git diff --cached` and every commit not yet on any remote for:
  - the literal ETSY_API_KEY / ETSY_SHARED_SECRET values from .env
  - `ETSY_API_KEY=` or `ETSY_SHARED_SECRET=` followed by anything
  - tracked files that should never be tracked (.env, data/, reports/, captures, exports)
Never prints the secret values themselves.

Usage:  python scripts/secret_check.py      (also installed as .git/hooks/pre-push)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# a value right after "=", ignoring docs that write `ETSY_API_KEY=` or <placeholder>
ASSIGN = re.compile(r"^\+.*?ETSY_(API_KEY|SHARED_SECRET)[ \t]*=[ \t]*[\"']?([A-Za-z0-9][^\s`'\"<>]*)", re.M)
FORBIDDEN = re.compile(r"^(\.env($|\.(?!example$))|data/|reports/|inputs/(captures|exports)/|inputs/seeds\.txt|\.cache/)")


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def env_values() -> list[str]:
    vals = []
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and line.split("=", 1)[0].strip() in ("ETSY_API_KEY", "ETSY_SHARED_SECRET"):
                v = line.split("=", 1)[1].strip().strip("'\"")
                if len(v) >= 6:
                    vals.append(v)
    return vals


def main() -> int:
    text = git("diff", "--cached") + git("log", "-p", "HEAD", "--not", "--remotes")
    hits = []
    for m in ASSIGN.finditer(text):
        hits.append(f"a value assigned to ETSY_{m.group(1)} ({len(m.group(2))} chars, masked)")
    for i, v in enumerate(env_values()):
        if v in text:
            hits.append(f"the actual {'key' if i == 0 else 'secret'} value from .env")
    tracked = [f for f in git("ls-files").splitlines() if FORBIDDEN.match(f)]
    if tracked:
        hits.append(f"files that must stay local are tracked: {tracked}")
    if hits:
        print("SECRET CHECK FAILED, do not push:\n  - " + "\n  - ".join(hits))
        return 1
    print("secret check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
