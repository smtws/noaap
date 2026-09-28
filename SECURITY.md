# Security

## What this program is

noaap runs on your own machine. It reads public YouTube pages and the MusicBrainz API,
writes audio files and one plan file per album into a folder you choose, and serves a web UI.
It stores no passwords and has no accounts.

## The web UI has no authentication

This is a deliberate design decision, not an oversight: `noaap serve` binds
**`127.0.0.1` by default**, where the operating system already decides who may connect.

`--host 0.0.0.0` removes that boundary. Anyone who can reach the port can then download into
your library, delete albums and read every file in it. Put it behind a reverse proxy with
authentication, or leave it on localhost.

What is in place either way: writing calls need an `X-Noaap` header and a JSON content type
(so another website cannot drive it through your browser), the `Host` header must be ours (DNS
rebinding), files are served by album and video id only — never by a path from the request —
a strict CSP applies, and thumbnails are fetched by the server so the page never talks to
Google.

## The recycle bin

`GET /api/recycle` lists what noaap moved aside instead of deleting. It reports an **entry id and
the track's own names** — artist, title, album, reason, size — and **no filesystem path**, so the
page never learns where the library is. Restoring and emptying are ordinary write calls and need the
same header as every other one; emptying asks first, because it is the only place in noaap that
really deletes audio.

## `noaap timing-serve`

The alignment service is a **separate exposure from the web UI, with its own default**: it binds
**`0.0.0.0`** unless you say otherwise, because its whole purpose is to be reached from another
machine. It has **no authentication**, and it **accepts audio uploads** — every alignment request
carries the caller's audio file, which it writes to a temporary file, reads and deletes.

So: run it only on a network you trust, or bind it to one interface (`--host 127.0.0.1`, or a
private address) and reach it over SSH or a VPN. It never sends anything out, and it holds nothing
after a request beyond the models it keeps loaded (`timing_idle_minutes`).

## Cookies

`cookies_from_browser` hands yt-dlp a logged-in YouTube session, which yt-dlp reads at request time. It does two things: it gets past the bot check, and it unlocks age-restricted videos. That session is read at request time and never copied into the
library, the plan files or the logs. A `cookies.txt` you point at stays wherever you put it —
treat that file as a password, because it is one.

## The watcher's way in

`noaap watch` is a separate process. It does no work itself: when a folder it watches has stopped
moving, it asks the web service to run an ordinary job, through `POST /api/arrived`.

**That call never takes a path.** It takes the *name* of a folder the config file names, and a path
relative to it. An absolute path is refused outright; the arrival is resolved beneath the configured
root and must still be beneath it afterwards, which is what stops `..` and a symlink pointing
somewhere else. A name the config does not list is refused, so an unconfigured noaap has nothing to
offer even to something that can already reach the port. It is a write like every other: without the
`X-Noaap` header and a JSON content type it is refused before any of that is read.

What the watcher remembers lives beside the config file at mode 600. It is a list of what is in
somebody's music folders, which is theirs; and nothing it holds reaches a log line the page shows —
a job is labelled by the watch's name and the album's path inside it, never by a path on the disk.

## Reporting a vulnerability

Use **[private vulnerability reporting](https://github.com/smtws/noaap/security/advisories/new)**
on this repository, or write to info@smt-webservices.de. Please do not open a public issue for
a security problem. Expect a slow but real answer: this is a personal project, not a product.
