"""Validate data/phrases.csv from the phrase-expander before Stage 1.

Checks: columns seed,phrase,buyer,job; 3-7 words; not just the seed (a head term);
buyer and job filled; no duplicates. Prints problems and a per-seed count.

Usage:  python -m src.phrases [path]
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from .common import DATA

COLUMNS = ["seed", "phrase", "buyer", "job"]


def problems(rows: list[dict]) -> list[str]:
    out, seen = [], set()
    for n, r in enumerate(rows, 2):
        p = (r.get("phrase") or "").strip().lower()
        words = len(p.split())
        if not 3 <= words <= 7:
            out.append(f"line {n}: {words} words: {p!r}")
        if p == (r.get("seed") or "").strip().lower():
            out.append(f"line {n}: head term (same as seed): {p!r}")
        if not (r.get("buyer") or "").strip() or not (r.get("job") or "").strip():
            out.append(f"line {n}: missing buyer or job: {p!r}")
        if p in seen:
            out.append(f"line {n}: duplicate: {p!r}")
        seen.add(p)
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else DATA / "phrases.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            print(f"columns must be {COLUMNS}, got {reader.fieldnames}")
            return 1
        rows = list(reader)
    errs = problems(rows)
    per_seed: dict[str, int] = {}
    for r in rows:
        per_seed[r["seed"]] = per_seed.get(r["seed"], 0) + 1
    print(f"{len(rows)} phrases: " + ", ".join(f"{s}={n}" for s, n in per_seed.items()))
    for e in errs:
        print("  " + e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
