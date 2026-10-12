"""Run a command and watch whether it, or anything it starts, opens the graphics card (§9, slice 183).

    uv run python scripts/card_watch.py -- uv run pytest -q

Every fifth of a second it reads `/proc/<pid>/fd` of the command and all of its descendants and
notes any `/dev/nvidia*` it finds, with the process and the moment. A CUDA context keeps those files
open until its process exits, so a look this often cannot miss one. It exits with the command's own
status, or 3 if the card was opened while the command itself succeeded.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time


def children_of(root: int) -> set[int]:
    """The process and every descendant, from `/proc/*/stat`'s parent field."""
    parents: dict[int, int] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            parents[int(name)] = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
    found, grew = {root}, True
    while grew:
        more = {pid for pid, parent in parents.items() if parent in found} - found
        grew = bool(more)
        found |= more
    return found


def cards_held(pid: int) -> set[str]:
    out = set()
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return out
    for fd in fds:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if target.startswith("/dev/nvidia"):
            out.add(target)
    return out


def main(argv: list[str]) -> int:
    command = argv[argv.index("--") + 1:] if "--" in argv else argv
    if not command:
        print(__doc__)
        return 2
    started = time.monotonic()
    proc = subprocess.Popen(command)
    seen: dict[tuple[int, str], float] = {}
    looks = 0
    while proc.poll() is None:
        for pid in children_of(proc.pid):
            for card in cards_held(pid):
                seen.setdefault((pid, card), time.monotonic() - started)
        looks += 1
        time.sleep(0.2)
    status = proc.returncode
    print(f"card_watch: {looks} looks over {time.monotonic() - started:.0f} s; "
          f"{len(seen)} graphics-card file(s) seen open")
    for (pid, card), at in sorted(seen.items(), key=lambda x: x[1]):
        print(f"card_watch:   pid {pid} held {card} from {at:.1f} s")
    return status if status or not seen else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
