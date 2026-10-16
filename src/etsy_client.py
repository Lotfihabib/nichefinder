"""Thin read-only Etsy Open API v3 client: auth header, rate budget, retries, TTL cache.

Only GET, only api.etsy.com, only the endpoints listed in docs/api-notes.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .common import ROOT

BASE = "https://api.etsy.com"
ALLOWED_PREFIXES = (
    "/v3/application/listings/active",
    "/v3/application/listings/batch",
    "/v3/application/listings/",          # /{listing_id}/reviews only (checked below)
)


class BudgetExceeded(RuntimeError):
    pass


class AuthError(RuntimeError):
    pass


class EtsyClient:
    """GET-only client. transport(url, params, headers) -> (status, headers, body)
    can be injected for tests. Defaults to `requests`."""

    def __init__(self, api_key: str, shared_secret: str, cfg: dict,
                 transport: Callable | None = None, cache_dir: Path | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        if not api_key or not shared_secret:
            raise AuthError("ETSY_API_KEY and ETSY_SHARED_SECRET must both be set in .env")
        self._header = {"x-api-key": f"{api_key}:{shared_secret}", "accept": "application/json"}
        self.cfg = cfg
        self.budget = cfg["budget"]
        self.ttl = cfg["cache"]["ttl_hours"]
        self.cache_dir = cache_dir or ROOT / cfg["cache"]["dir"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.call_log = self.cache_dir / "calls.log"
        self.transport = transport or self._requests_transport
        self.clock, self.sleep = clock, sleep
        self._last_call = 0.0
        self.calls_run = 0          # network requests this process
        self.calls_phrase = 0       # network requests since start_phrase()
        self.cache_hits = 0
        self.remaining_today: int | None = None

    # ------------------------------------------------------------ public

    @classmethod
    def from_env(cls, cfg: dict, **kw) -> "EtsyClient":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        return cls(os.getenv("ETSY_API_KEY", ""), os.getenv("ETSY_SHARED_SECRET", ""), cfg, **kw)

    def start_phrase(self) -> None:
        self.calls_phrase = 0

    def phrase_calls_left(self) -> int:
        return self.budget["calls_per_phrase"] - self.calls_phrase

    def get(self, path: str, params: dict[str, Any], kind: str) -> dict:
        self._check_path(path)
        key = self._cache_key(path, params)
        cached = self._cache_read(key, kind)
        if cached is not None:
            self.cache_hits += 1
            return cached
        body = self._fetch(path, params)
        self._cache_write(key, kind, body)
        return body

    # ------------------------------------------------------------ internals

    @staticmethod
    def _check_path(path: str) -> None:
        if not path.startswith(ALLOWED_PREFIXES):
            raise ValueError(f"endpoint not allowed: {path}")
        if path.startswith("/v3/application/listings/") and path not in (
                "/v3/application/listings/active", "/v3/application/listings/batch"):
            parts = path.strip("/").split("/")
            if not (len(parts) == 5 and parts[3].isdigit() and parts[4] == "reviews"):
                raise ValueError(f"endpoint not allowed: {path}")

    def _cache_key(self, path: str, params: dict) -> str:
        raw = path + "?" + json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_read(self, key: str, kind: str) -> dict | None:
        f = self.cache_dir / f"{key}.json"
        if not f.exists():
            return None
        entry = json.loads(f.read_text(encoding="utf-8"))
        if self.clock() - entry["fetched_at"] > self.ttl[kind] * 3600:
            f.unlink(missing_ok=True)       # expired: terms say don't keep it
            return None
        return entry["body"]

    def _cache_write(self, key: str, kind: str, body: dict) -> None:
        (self.cache_dir / f"{key}.json").write_text(
            json.dumps({"fetched_at": self.clock(), "kind": kind, "body": body}), encoding="utf-8")

    def _calls_last_24h(self) -> int:
        if not self.call_log.exists():
            return 0
        cutoff = self.clock() - 86400
        stamps = [float(x) for x in self.call_log.read_text().split()]
        recent = [t for t in stamps if t >= cutoff]
        if len(recent) != len(stamps):
            self.call_log.write_text("\n".join(f"{t:.3f}" for t in recent) + ("\n" if recent else ""))
        return len(recent)

    def _reserve_call(self) -> None:
        if self.calls_phrase >= self.budget["calls_per_phrase"]:
            raise BudgetExceeded(f"phrase budget of {self.budget['calls_per_phrase']} calls used")
        if self._calls_last_24h() >= self.budget["requests_per_day"]:
            raise BudgetExceeded(f"daily budget of {self.budget['requests_per_day']} calls used")
        margin = self.budget["stop_when_remaining_today_below"]
        if self.remaining_today is not None and self.remaining_today < margin:
            raise BudgetExceeded(f"Etsy reports only {self.remaining_today} calls left today")
        wait = 1.0 / self.budget["requests_per_second"] - (self.clock() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        self._last_call = self.clock()
        self.calls_run += 1
        self.calls_phrase += 1
        with open(self.call_log, "a") as f:
            f.write(f"{self._last_call:.3f}\n")

    def _fetch(self, path: str, params: dict, attempts: int = 4) -> dict:
        url = BASE + path
        delay = 1.0
        comma_arrays = False
        for attempt in range(attempts):
            self._reserve_call()
            sent = {k: (",".join(map(str, v)) if comma_arrays and isinstance(v, list) else v)
                    for k, v in params.items()}
            status, headers, body = self.transport(url, sent, self._header)
            h = {k.lower(): v for k, v in (headers or {}).items()}
            if "x-remaining-today" in h:
                try:
                    self.remaining_today = int(h["x-remaining-today"])
                except ValueError:
                    pass
            if status == 200:
                return body
            if status in (401, 403):
                raise AuthError(
                    f"HTTP {status} from Etsy. Check that the x-api-key header is "
                    f"'keystring:shared_secret' (both in .env), then whether the app is "
                    f"still pending approval. Body: {str(body)[:300]}")
            if status == 400 and not comma_arrays and any(isinstance(v, list) for v in params.values()):
                comma_arrays = True        # spec default is repeated keys; try comma-joined once
                continue
            if status == 429 or status >= 500:
                retry_after = float(h.get("retry-after", delay))
                self.sleep(max(retry_after, delay))
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {status} for {path}: {str(body)[:300]}")
        raise RuntimeError(f"giving up on {path} after {attempts} attempts")

    @staticmethod
    def _requests_transport(url: str, params: dict, headers: dict):
        r = requests.get(url, params=params, headers=headers, timeout=30)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, dict(r.headers), body
