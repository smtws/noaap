"""Before noaap writes to a collection that is somebody's own (DESIGN §9, slice 99).

The user, of twenty years of music on a NAS: *"i wont let it to my collection of 20 years without
precautions prepared (no, i do not have a full backup of the nas)"*. Everything here exists for that
sentence. Three layers, cheapest first, and a pass over somebody's collection uses all three:

1. **A snapshot** — one line per file: where it is, how big, when it was last written, what its tags
   said, and a digest of what it holds: the **decoded** recording for audio, which is the one digest
   a tag block at the end of an mp3 cannot move, and the bytes themselves for everything else, which
   is how the owner's own `cover.jpg` finds its way back after a pass moved the folder it was in. A
   few hundred bytes per file, so eleven thousand tracks cost a couple of megabytes and one read of
   the tree. It is what a restore reads.
2. **A safe write** — nothing is written *into* an audio file. A copy is made beside it, the copy is
   written, the audio is proved unchanged, and only then does an atomic replace put it in place. An
   interruption at any moment leaves the file that was there.
3. **The originals kept** — with `keep=DIR`, the first write to a file copies it there first, whole.
   That is the only layer that can give a file back **byte for byte**, because a tag round-trip
   through mutagen is not byte-identical even when it writes the same values back (measured: same
   size, different bytes — the comment block is laid out differently). It costs as much disk as the
   part of the collection that is touched, so the pass measures it and says so before it starts.

**What none of this covers**, and the docs say so too: a disk that fails, a NAS that goes away
mid-pass, or somebody deleting the snapshot. It is a way back from *what noaap did*, not a backup.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .sources_folder import AUDIO, stream_sha
from .tag import (
    decoded_sha,
    decoder,
    embedded_cover,
    raw_tags,
    restore_tags,
    set_picture,
    tags_outside_ours,
)

SNAPSHOT = "snapshot.jsonl"         # the suffix; the name is the root's own (see `snapshot_for`)
PICTURES = "pictures"               # a folder beside it: one file per distinct embedded cover
KEPT = "noaap-originals"            # and where it keeps the originals, likewise


class Unsafe(Exception):
    """A write was refused because what it produced was not the same recording."""


@dataclass
class Recorded:
    """One audio file, as it was before anything was done to it.

    `tags` are the keys a writer here could touch, verbatim — those are the ones a restore puts back.
    `others` is every **other** key the file carries, as a digest of its value: a person's own
    `comment`, their replaygain, their cover. Nothing in this program writes those, so a restore has
    nothing to undo about them — but recording their fingerprint means a pass that lost one is
    **found out and named**, which a mapping of only our own keys could never do. Their values are
    not kept because a cover is a hundred kilobytes and eleven thousand of them are not a snapshot.
    """

    path: str          # relative to the root, in the root's own spelling
    size: int
    mtime_ns: int
    audio: str | None  # **the decoded recording**, not the file: see `of`
    tags: dict[str, Any]
    picture: str | None = None   # the digest of the picture inside it, or None for "there was none"
    others: dict[str, str] = field(default_factory=dict)
    # how `audio` was arrived at: the decoded recording, the bytes (for a file that is not audio),
    # or — in a snapshot written before the decoded identity — a digest of the audio packets
    how: str = "decoded"

    @property
    def is_audio(self) -> bool:
        return self.how != "bytes"

    def as_line(self) -> str:
        line: dict[str, Any] = {"path": self.path, "size": self.size, "mtime_ns": self.mtime_ns,
                                "audio": self.audio, "how": self.how,
                                "tags": self.tags, "others": self.others}
        if self.picture:
            line["picture"] = self.picture
        return json.dumps(line, ensure_ascii=False)

    @staticmethod
    def of(path: Path, root: Path) -> Recorded:
        """**The decoded identity, which costs three times a packet digest and is the only one that
        answers.** This recorded `stream_sha` until it was measured on the user's own collection: a
        pass that embedded a cover changed the packet digest of every mp3 it touched — ffmpeg's
        demuxer hands the trailing tag block over as audio data — and then the restore could neither
        *find* such a file (the search is that digest) nor say whether its recording had survived.
        One file of 2000 was left behind as missing while its audio sat there, decoding identically;
        and the survival check, where the packets had moved, had been reduced to asking whether
        ffmpeg could read the file at all, which is not a check. The read is the same read either
        way — it is the decode that costs, and over a share the read is the ceiling.
        """
        st = path.stat()
        here: dict[str, Any] = dict(path=str(path.relative_to(root)), size=st.st_size,
                                    mtime_ns=st.st_mtime_ns)
        if path.suffix.lower() not in AUDIO:
            # not audio: its bytes are its identity, there are no tags of anybody's to keep, and a
            # digest of it costs nothing (153 such files in the reference collection, 14 MB in all)
            return Recorded(**here, audio=bytes_sha(path), tags={}, how="bytes")
        # **the picture is recorded too, by its digest** (§9, slice 104). A writer here embeds one
        # and no key list ever held it, so a restore put the text tags back and left somebody's file
        # carrying a cover the pass had added. The bytes go in a store beside the snapshot, once per
        # distinct picture, because a cover is a hundred kilobytes and eleven thousand of them are
        # not a snapshot — on the reference collection 269 files carry one and they share far fewer.
        inside = embedded_cover(path)
        return Recorded(**here, audio=decoded_sha(path), tags=raw_tags(path),
                        others=tags_outside_ours(path),
                        picture=hashlib.blake2b(inside, digest_size=16).hexdigest() if inside
                        else None)


@dataclass
class Snapshot:
    """What was written down, and about which root."""

    root: Path
    at: str
    files: list[Recorded] = field(default_factory=list)
    digest_by: str = ""


@dataclass
class Summary:
    """What a pass over the files came to, in numbers a person can check."""

    files: int = 0
    bytes: int = 0
    unreadable: list[str] = field(default_factory=list)
    renamed: int = 0
    retagged: int = 0
    already: int = 0
    missing: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)   # keys of theirs a pass dropped, named
    removed: list[str] = field(default_factory=list)   # what the pass had created, taken away again


def audio_under(root: Path) -> Iterator[Path]:
    """Every audio file under a root, in a settled order — the order a report is read in."""
    yield from sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO)


def files_under(root: Path, skip: Iterable[Path] = ()) -> Iterator[Path]:
    """**Every** file under a root, audio or not, in that same settled order.

    What the snapshot walks. It recorded audio only, and a pass that moves an album into the scheme
    moves the whole folder — so the owner's `cover.jpg`, their `.url`, their `.thumb` cache went with
    it and the restore, which puts audio back by its recorded path, left them behind in a folder they
    never made. Measured on the user's own collection: 8 of their files ended up somewhere else and
    the album looked restored. They are a few hundred of a few thousand and they cost a `stat` and a
    digest of fourteen megabytes, so there was never a reason not to write them down.
    """
    away = {p.resolve() for p in skip}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.resolve() in away:
            continue
        if path.name.endswith(".part") or ".noaap-new" in path.name:
            continue    # something's temporary file, including ours
        yield path


def bytes_sha(path: Path) -> str | None:
    """A digest of the bytes themselves — for a file that is not audio, that *is* its identity."""
    try:
        digest = hashlib.blake2b(digest_size=16)
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def pictures_for(snapshot: Path) -> Path:
    """Where the pictures a snapshot records are kept: one file per distinct one, named by its digest.

    Deduplicated, because an album's tracks carry the same cover: on the reference collection 269
    files hold a picture and far fewer distinct ones. Without this folder a restore can still take
    away a picture the pass added — the snapshot says which files had none — but it cannot put back
    one the pass replaced, and it says so rather than pretending.
    """
    return snapshot.with_name(f"{snapshot.stem}.{PICTURES}")


def snapshot_for(root: Path) -> Path:
    """Where a snapshot of this root goes unless told otherwise: beside it, named after it.

    It was one fixed name beside the root, so two collections under one parent — `/mnt/nas/Music` and
    `/mnt/nas/Live`, or a copy of one beside it — would have written to the same file. Taking a
    snapshot refuses to overwrite one, so the second take-in would simply have stopped; the first
    would have been fine and the message would have been a puzzle. Named after the root, as the
    resume file now is (§9, slice 102).
    """
    root = root.resolve()
    return root.parent / f"{root.name}-{SNAPSHOT}"


def take(root: Path, out: Path | None = None, log: Callable[[str], None] = lambda s: None) -> Path:
    """Write down every audio file under `root` before anything is done to it.

    One JSON line per file — **every** file, not only the audio — plus a header line, so eleven
    thousand tracks are a file a person can read with `head` and a program can stream. Overwriting an existing snapshot is refused: the one
    that is there may be the only way back from the pass that wrote it.
    """
    out = out or snapshot_for(root)
    if out.exists():
        raise Unsafe(f"{out} is already there — name another file, or move that one away")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    seen = 0
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"noaap_snapshot": 1, "root": str(root), "digest_by": decoder(),
                             "at": datetime.now(UTC).isoformat(timespec="seconds")}) + "\n")
        store = pictures_for(out)
        for path in files_under(root, skip=(out, tmp)):
            was = Recorded.of(path, root)
            if was.picture:
                kept = store / was.picture
                if not kept.exists():
                    store.mkdir(parents=True, exist_ok=True)
                    kept.write_bytes(embedded_cover(path) or b"")
            fh.write(was.as_line() + "\n")
            seen += 1
            if seen % 250 == 0:
                log(f"  written down {seen} file(s)")
    os.replace(tmp, out)
    log(f"{seen} file(s) written down in {out}")
    return out


def read(path: Path) -> Snapshot:
    """A snapshot back from its file. A line that cannot be read stops the whole thing."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise Unsafe(f"{path} is empty")
    head = json.loads(lines[0])
    if not head.get("noaap_snapshot"):
        raise Unsafe(f"{path} is not a noaap snapshot")
    snap = Snapshot(root=Path(head["root"]), at=head.get("at", ""), digest_by=head.get("digest_by", ""))
    for n, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        try:
            got = json.loads(line)
            # a snapshot written before the decoded identity carries `packets`; it still restores,
            # with the weaker digest it was written with
            snap.files.append(Recorded(path=got["path"], size=got["size"], mtime_ns=got["mtime_ns"],
                                       audio=got.get("audio") or got.get("packets"),
                                       tags=got.get("tags") or {}, others=got.get("others") or {},
                                       picture=got.get("picture"),
                                       how=got.get("how") or ("decoded" if got.get("audio")
                                                              else "packets")))
        except (ValueError, KeyError) as e:
            raise Unsafe(f"{path} line {n}: {e}") from e
    return snap


# -- putting it back -------------------------------------------------------------------------------


def restore(snapshot: Snapshot, root: Path | None = None, apply: bool = False, kept: Path | None = None,
            made: Iterable[str] = (), folders: Iterable[str] = (), pictures: Path | None = None,
            log: Callable[[str], None] = lambda s: None) -> Summary:
    """Put the folder back as it was. Dry by default, like every other pass here.

    A file is found by its recorded path first, then — for anything renamed — by its size and the
    digest of the recording it holds among the files that are there now. What is still not found is
    **named and left**, never guessed at.

    With `kept`, a file whose original was put aside is restored by copying that back: the only way
    that is byte for byte. Otherwise the name and the tags go back and the audio is proved unchanged.

    **`made` is what the pass created and this takes away again** (R-342, ruling 3): the plan per
    album, a cover it fetched, the words it saved. Only those paths, read from the record the pass
    wrote as it went — never a file it did not write down, and the dry run lists every one of them
    before anything is removed. Without it a restored folder came back holding 136 files its owner
    never had, and "a way back" has to mean the folder as it was.

    `folders` is the same for directories — the folder a pass moved an album into. The files go back
    under their recorded paths, which leaves that one standing **empty**: 19 of them on the user's own
    collection, husks of noaap's spelling of their album names. `rmdir` is what removes them, so one
    that still holds anything at all stays.

    `pictures` is the store `take` filled, and with it the **embedded cover** goes back too: taken
    out where the snapshot says the file had none, and put back where the pass replaced one. Without
    the store only the first of those is possible, and the second is named instead.
    """
    root = root or snapshot.root
    done = Summary()
    here = {p: (p.stat().st_size, None) for p in files_under(root)}   # digests are read only if needed
    by_path = {str(p.relative_to(root)): p for p in here}
    unclaimed = dict(by_path)
    recorded = {r.path for r in snapshot.files}
    near: Path | None = None        # where the last file of this album turned up: the first guess

    for was in snapshot.files:
        done.files += 1
        want = root / was.path
        source = kept / was.path if kept and (kept / was.path).is_file() else None
        now = by_path.get(was.path)
        if now is None:
            now = _found_again(was, unclaimed, here, recorded, root, near)
            near = now.parent if now is not None else near
        if now is None and source is None:
            done.missing.append(was.path)
            continue
        if now is None:
            # **the kept original does not need the file on disk** (§9, slice 99). Where a pass renamed
            # a file *and* moved its folder, looking for it can fail — measured: 52 of 2000 — and the
            # copy put aside before the first write is the answer to all of them.
            if apply:
                want.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, want)
                os.utime(want, ns=(was.mtime_ns, was.mtime_ns))
            done.retagged += 1
            done.renamed += 1
            continue
        unclaimed.pop(str(now.relative_to(root)), None)

        if now != want:
            log(f"  {'would put' if not apply else 'put'} {now.relative_to(root)} back as {was.path}")
            done.renamed += 1
        if source is not None:
            if apply:
                want.parent.mkdir(parents=True, exist_ok=True)
                if now != want:
                    now.rename(want)
                shutil.copy2(source, want)
                os.utime(want, ns=(was.mtime_ns, was.mtime_ns))
            done.retagged += 1
            continue
        if apply and now != want:
            want.parent.mkdir(parents=True, exist_ok=True)
            now.rename(want)
            now = want
        if apply and now.exists():
            if was.is_audio and restore_tags(now, was.tags):
                done.retagged += 1
            else:
                done.already += 1
            if was.is_audio and _picture_back(was, now, pictures, done):
                done.retagged += 1
            os.utime(now, ns=(was.mtime_ns, was.mtime_ns))
            if not holds_it(was, now):
                done.changed.append(was.path)
            lost = [k for k, d in was.others.items() if tags_outside_ours(now).get(k) != d]
            if lost:
                done.lost.append(f"{was.path}: {', '.join(sorted(lost))}")
        else:
            done.retagged += 1
    # audio under the root that the snapshot never saw: a file a pass renamed and nothing could match,
    # or one added since. Named, never removed — this pass only puts back. **A snapshot records audio
    # files only**, so what a pass wrote beside them — plans, covers, `.lrc` sidecars — is not counted
    # here and is not touched either; the README says so where the precautions are described.
    ours = set(made)
    left = [name for name in unclaimed if name not in recorded and name not in ours]
    if left:
        log(f"{len(left)} file(s) under the root are not in the snapshot and were left alone "
            f"(nobody wrote down who made them): {', '.join(sorted(left)[:3])}"
            + (" …" if len(left) > 3 else ""))
    # what the pass created goes last, so nothing is removed before everything is back
    for name in sorted(dict.fromkeys(made)):
        path = root / name
        if not path.is_file():
            continue
        done.removed.append(name)
        log(f"  {'would remove' if not apply else 'removed'} {name} — the pass wrote it")
        if apply:
            path.unlink()
            with contextlib.suppress(OSError):
                path.parent.rmdir()   # only if the pass's own file was the last thing in there
    # **the parents go in the same sweep, not after each child.** Attempting `folder.parent` inline
    # asks about an artist folder while its other albums are still there, and whether it succeeds
    # then depends on the order the albums happen to come in: three artist folders were left standing
    # empty on the user's collection that way. Deepest first over the folders *and* their parents, so
    # a child is always asked before the folder holding it.
    husks = {root / name for name in folders}
    husks |= {parent for where in list(husks) for parent in where.parents
              if parent != root and root in parent.parents}
    for where in sorted(husks, key=lambda path: -len(path.parts)):
        if not where.is_dir():
            continue
        name = str(where.relative_to(root))
        if not apply:
            if not any(where.iterdir()):
                log(f"  would remove the folder {name} — the pass made it, and it would be empty")
            continue
        for inside in sorted((p for p in where.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            with contextlib.suppress(OSError):
                inside.rmdir()        # a `.thumb` of theirs whose files have gone back
        try:
            where.rmdir()
        except OSError:
            continue                  # something is still in there: it stays, and so does the folder
        done.removed.append(name + "/")
        log(f"  removed the folder {name} — the pass made it and it is empty")
    if not apply:
        log(f"{done.files} file(s) in the snapshot, {done.renamed} would be put back under their own "
            f"name, {len(done.removed)} of the pass's own would be removed; nothing was changed. "
            "`--restore … --apply` does it.")
    else:
        log(f"{done.files} file(s) restored, {done.renamed} renamed back, {len(done.removed)} of the "
            f"pass's own removed, {(done.changed and len(done.changed)) or 0} whose audio is not what "
            "it was")
    return done


def _words(name: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+", Path(name).stem.casefold()) if len(w) > 2}


def _found_again(was: Recorded, unclaimed: dict[str, Path], here: dict[Path, tuple[int, str | None]],
                 recorded: set[str] | None = None, root: Path | None = None,
                 near: Path | None = None) -> Path | None:
    """A renamed file, by the recording it holds, with the likeliest candidates asked first.

    **The size is not a gate**, because a file retagged on the way is a different size — gating on it
    left 52 of 2000 unfindable in the measured run — but it is a good first guess.

    **The order is everything, and the order decides nothing.** Only the digest says yes, so the
    candidates may be ranked by anything at all; what it costs is one ffmpeg per candidate asked, and
    with no ranking that is a digest of the whole collection per missing file. Measured: a pass that
    renamed *and* retagged all 2000 files (so the size guess never fired) left the restore digesting
    8 GB in four minutes and nowhere near done — hours for this copy, days for the collection.

    **The words the two names share come first**, because on the user's own collection they pick the
    right file out of 231 candidates *uniquely*, every time, for all 211 that had been renamed: a
    rename keeps the title, and the title is what the albums do not share. The folder the last file of
    this album turned up in is only a tiebreaker — putting it first was measurably worse, because a
    file that is *not* in that folder is then looked for behind every file that is.

    **A file that the snapshot records under its own name is never taken for another one.** Two files
    can hold the same recording (the same track on an album and on a best-of), and claiming one for
    the other renames somebody else's file away.
    """
    if not was.audio:
        return None
    mine = [(name, path) for name, path in unclaimed.items()
            if not recorded or name not in recorded]
    wanted = _words(was.path)

    def likeliest(pair: tuple[str, Path]) -> tuple[Any, ...]:
        name, path = pair
        return (-len(wanted & _words(name)),
                near is not None and path.parent != near,
                here[path][0] != was.size,
                name)

    for _, path in sorted(mine, key=likeliest):
        if holds_it(was, path):
            return path
    return None


def _picture_back(was: Recorded, now: Path, store: Path | None, done: Summary) -> bool:
    """The embedded cover as the snapshot found it: gone where there was none, back where there was.

    A pass embeds a picture and no key list ever held it, so a restore used to put the text tags back
    and leave the cover in place — 23 of 36 files in one run. What cannot be done without the store
    is replacing a picture the pass overwrote; that is named in `lost`, not guessed at.
    """
    inside = embedded_cover(now)
    here = hashlib.blake2b(inside, digest_size=16).hexdigest() if inside else None
    if here == was.picture:
        return False
    if was.picture is None:
        return set_picture(now, None)           # the pass put it there; it goes
    kept = (store / was.picture) if store else None
    if kept is None or not kept.is_file():
        done.lost.append(f"{was.path}: the picture it carried, and no store to put it back from")
        return False
    return set_picture(now, kept.read_bytes())


def holds_it(was: Recorded, now: Path) -> bool:
    """Whether this file holds the recording the snapshot wrote down — the one question, asked once.

    A snapshot written with the decoded identity is asked for it. One written before that has only a
    packet digest, which a tag block at the end of an mp3 can move, so a file that fails it is given
    the benefit of the doubt where it decodes — that is as much as such a snapshot can say.
    """
    if was.audio is None:
        return True
    if was.how == "bytes":
        return bytes_sha(now) == was.audio
    if was.how == "decoded":
        return decoded_sha(now) == was.audio
    return stream_sha(now) == was.audio or decoded_sha(now) is not None


# -- writing to somebody else's file -----------------------------------------------------------------


def same_audio(a: Path, b: Path) -> bool:
    """Whether two files hold the same recording.

    The packets first, which is cheap and, where it says yes, conclusive (§9, slice 66). Where it says
    no the answer may still be yes — a tag block at the end of an mp3 is handed over as audio — so the
    decoded identity settles it, and that is the one that costs.
    """
    packets = stream_sha(a)
    if packets is not None and packets == stream_sha(b):
        return True
    mine = decoded_sha(a)
    return mine is not None and mine == decoded_sha(b)


def safely(path: Path, write: Callable[[Path], Any], keep: Path | None = None,
           root: Path | None = None, expect: str | None = None,
           log: Callable[[str], None] = lambda s: None) -> Any:
    """Write to a copy, prove the recording survived, then replace the file in one step.

    `write` is given the path of a copy beside the original and may do what it likes to it. If it
    raises, if the copy's audio is not the original's, or if the machine stops in the middle, the file
    that was there is still there — a temporary file beside it is the whole of the mess.

    With `keep` (and `root`), the original is copied there **before** the replace and only the first
    time, so a second pass does not overwrite the first copy with an already-written file.

    **`expect` is the digest the snapshot already wrote down for this file**, and it halves the
    reading (R-342, ruling 4). Without it the proof is `same_audio(path, tmp)` — both files read.
    With it only the copy is read, and the answer is compared with a digest this same pass computed
    minutes earlier, so nothing is taken on trust that was not measured. A digest that disagrees is
    not a failure: the full comparison runs after all, which is what makes a stale expectation cost
    time instead of correctness.
    """
    # **the copy keeps the suffix.** `tag.kind` is the one place a suffix decides anything, and a
    # temporary file called `.x.mp3.noaap-new` reads as an Opus — measured on the user's own
    # collection, where 1662 of 2000 files failed to be tagged with "read b'ID3', expected b'OggS'".
    # Nothing was damaged (the write fails before the replace), and nothing was written either.
    tmp = path.with_name(f".{path.stem}.noaap-new{path.suffix}")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(path, tmp)
    try:
        answer = write(tmp)
        if not (expect is not None and decoded_sha(tmp) == expect) and not same_audio(path, tmp):
            raise Unsafe(f"{path.name}: what was written is not the same recording — nothing was changed")
        if keep is not None:
            put_aside(path, root or path.parent, keep)
        os.replace(tmp, path)
        return answer
    finally:
        if tmp.exists():
            tmp.unlink()


def empty_under(root: Path, only: Iterable[Path] = (), everything: bool = False) -> list[Path]:
    """Which empty folders a pass may take away, deepest first — and that is a setting (§9, slice 104).

    `only` is what this pass emptied itself, which is always its own to clear. With `everything` the
    owner's empty folders go too, which is `remove_empty_folders` and is off by default: sweeping the
    whole root for empty directories once removed `Der W/Autonomie`, empty in somebody's own
    collection and never touched by us. The root itself is never a candidate and nothing outside it
    ever is; `rmdir` still decides, so a folder that holds anything at all stays.
    """
    if everything:
        found = {p for p in root.rglob("*") if p.is_dir()}
    else:
        found = {p for p in only} | {p.parent for p in only}
    out = []
    for folder in sorted(found, key=lambda p: -len(p.parts)):
        if folder == root or root not in folder.parents or not folder.is_dir():
            continue
        try:
            if not any(folder.iterdir()):
                out.append(folder)
        except OSError:
            continue
    return out


def put_aside(path: Path, root: Path, keep: Path, as_path: str | None = None) -> Path | None:
    """The original, kept whole, once. Returns where it went, or None when it is already there.

    **`as_path` is the path the snapshot knows this file by**, which is not always the path it is at:
    a pass moves an album into noaap's scheme before the first file in it is written. Measured on the
    user's own collection, twice. Filing the copy under the folder the file is in *now* put 295 of
    2000 files back from their tags instead of byte for byte, and one album's originals under a name
    no snapshot had ever seen. Filing it under the *album's* recorded folder then dropped the disc
    subfolder a file was in — 67 of 2000 — and, where two discs held a track of the same name, mapped
    two files onto one kept copy, so **3 originals were never kept at all** and nothing said so. A
    recorded path is unique, which is the end of that whole class of fault.
    """
    try:
        where = keep / (as_path or str(path.relative_to(root)))
    except ValueError:
        where = keep / path.name
    if where.exists():
        return None
    where.parent.mkdir(parents=True, exist_ok=True)
    tmp = where.with_name(f".{where.name}.part")
    shutil.copy2(path, tmp)
    os.replace(tmp, where)
    return where


def room_for(root: Path, keep: Path) -> tuple[int, int]:
    """(what keeping every original would take, what is free where they would go)."""
    need = sum(p.stat().st_size for p in audio_under(root))
    where = keep
    while not where.exists() and where != where.parent:
        where = where.parent
    free = shutil.disk_usage(where).free
    return need, free


def says_room(root: Path, keep: Path) -> str:
    """One sentence about the space, for a person to read before a pass starts."""
    need, free = room_for(root, keep)
    enough = "" if need < free * 0.9 else "  ⚠ that is not enough room"
    return (f"keeping every original needs {need / 1e9:.1f} GB in {keep}; {free / 1e9:.1f} GB is free"
            f"{enough}")


__all__ = [
    "KEPT",
    "SNAPSHOT",
    "Recorded",
    "Snapshot",
    "Summary",
    "Unsafe",
    "audio_under",
    "put_aside",
    "read",
    "restore",
    "room_for",
    "safely",
    "same_audio",
    "says_room",
    "snapshot_for",
    "take",
]
