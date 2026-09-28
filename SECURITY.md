# Security

## What this program is

ytalbum runs on your own machine. It reads public YouTube pages and the MusicBrainz API,
writes audio files and one plan file per album into a folder you choose, and serves a web UI.
It stores no passwords and has no accounts.

## The web UI has no authentication

This is a deliberate design decision, not an oversight: `ytalbum serve` binds
**`127.0.0.1` by default**, where the operating system already decides who may connect.

`--host 0.0.0.0` removes that boundary. Anyone who can reach the port can then download into
your library, delete albums and read every file in it. Put it behind a reverse proxy with
authentication, or leave it on localhost.

What is in place either way: writing calls need an `X-Ytalbum` header and a JSON content type
(so another website cannot drive it through your browser), the `Host` header must be ours (DNS
rebinding), files are served by album and video id only — never by a path from the request —
a strict CSP applies, and thumbnails are fetched by the server so the page never talks to
Google.

## `ytalbum timing-serve`

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

## Reporting a vulnerability

Use **[private vulnerability reporting](https://github.com/smtws/ytalbum/security/advisories/new)**
on this repository, or write to info@smt-webservices.de. Please do not open a public issue for
a security problem. Expect a slow but real answer: this is a personal project, not a product.
