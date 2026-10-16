"""Delete Etsy data we no longer need (API terms s.5: don't store longer than necessary).

- raw API cache entries past their TTL (6 h listings/search, 24 h reviews)
- evidence, judgment and red-team files older than retention.evidence_days

Reports are kept: they hold derived numbers and short quotes, and are your decision record.

Usage:  python -m src.purge
"""
from __future__ import annotations

import json
import sys
import time

from .common import EVIDENCE, JUDGMENTS, REDTEAM, ROOT, load_config


def main(argv=None) -> int:
    cfg = load_config()
    now = time.time()
    cache = ROOT / cfg["cache"]["dir"]
    gone = 0
    for f in cache.glob("*.json"):
        e = json.loads(f.read_text(encoding="utf-8"))
        if now - e["fetched_at"] > cfg["cache"]["ttl_hours"][e["kind"]] * 3600:
            f.unlink()
            gone += 1
    old = 0
    limit = cfg["retention"]["evidence_days"] * 86400
    for d in (EVIDENCE, JUDGMENTS, REDTEAM):
        for f in d.glob("*.json"):
            if now - f.stat().st_mtime > limit:
                f.unlink()
                old += 1
    print(f"purged {gone} expired cache entries and {old} evidence/judgment files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
