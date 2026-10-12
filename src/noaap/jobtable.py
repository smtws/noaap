"""The server's jobs, on disk, for whoever reads them afterwards (DESIGN §9, slice 184).

Jobs live in memory and are forgotten past `KEEP_FINISHED`; after a restart nothing said what had
run. This is an append-only JSON-lines file: one record when a job starts, one when it ends. The
server never reads it to decide anything — a restart only closes what the last server left open.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROTATE_AT = 4 << 20   # bytes; past this the file becomes `.1`, replacing the one before
WHY_AT_MOST = 300


class JobTable:
    def __init__(self, path: Path, run: str | None = None) -> None:
        self.path = path
        # job ids start again at 1 with every server, so a record names its server too
        self.run = run or f"{dt.datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
        self._lock = threading.Lock()
        self._close_what_was_left_open()

    def started(self, job_id: int, kind: str, label: str, album: str | None, lane: str) -> None:
        self._write({"event": "start", "id": job_id, "kind": kind, "label": label, "album": album,
                     "lane": lane})

    def ended(self, job_id: int, kind: str, label: str, album: str | None, lane: str, outcome: str,
              seconds: float | None, why: str | None = None) -> None:
        record: dict[str, Any] = {"event": "end", "id": job_id, "kind": kind, "label": label,
                                  "album": album, "lane": lane, "outcome": outcome,
                                  "seconds": None if seconds is None else round(seconds, 3)}
        if why:
            record["why"] = why[:WHY_AT_MOST]
        self._write(record)

    def _write(self, record: dict[str, Any], run: str | None = None) -> None:
        line = json.dumps({"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                           "run": run or self.run, **record}, ensure_ascii=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > ROTATE_AT:
                    self.path.replace(self.path.with_name(self.path.name + ".1"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as e:
            # **a job never fails over its record**: one line in the journal, and the job goes on
            log.warning("the job table %s could not be written: %s", self.path, e)

    def _close_what_was_left_open(self) -> None:
        """Each start with no end, from a server that is not this one, gets an `interrupted` end."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return   # no table yet, or none that can be read: nothing to close
        open_: dict[tuple[str, int], dict[str, Any]] = {}
        for line in text.splitlines():
            try:
                r = json.loads(line)
                key = (r["run"], r["id"])
            except (ValueError, KeyError, TypeError):
                continue
            if r.get("event") == "start":
                open_[key] = r
            elif r.get("event") == "end":
                open_.pop(key, None)
        for (run, _), r in open_.items():
            if run != self.run:
                self._write({"event": "end", "id": r["id"], "kind": r.get("kind"), "label": r.get("label"),
                             "album": r.get("album"), "lane": r.get("lane"), "outcome": "interrupted",
                             "seconds": None}, run=run)


def elapsed_since(started: float | None) -> float | None:
    return None if started is None else time.monotonic() - started
