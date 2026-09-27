# Backlog

What is not broken but missing for someone who lives in the web UI. Written 2026-09-26 by the
reviewer after the QA run (`docs/qa-catalog.md`, fixes `1e3da95..bd0fe6f`, v0.2.0), from the
code and the run's evidence rather than from clicking through the UI. Ordered by how much a
daily user would feel each one. The owner agreed to start with item 1.

## 1. Lyrics editor — DONE (P8, DESIGN §9.26)

The lyrics panel is read-only ("click to read") while the whole ownership contract
(DESIGN §9.21) is about files a user writes by hand. To fix one line or add words LRCLIB lacks,
a user must find the audio file on disk, create a sidecar with the same stem plus `.lrc`, write
LRC syntax by hand, and run a pass before the UI shows the words as theirs.

Wanted: edit and create lyrics in the panel. Saving writes the sidecar, sets the user mark,
records the hash, rewrites the tag and derives the status from the text — the same step the
contract already performs for files found on disk, just triggered from the UI. Clearing removes
the sidecar with the P2 semantics (status `none`, mark dropped, `--refetch` may bring LRCLIB's
back). No lag: the panel shows ownership immediately.

**Done:** the ♪ button opens a panel that reads *and* writes, and it is now shown for a track with
no words at all (faint) and for one LRCLIB calls instrumental, so lyrics can be created rather than
only read. `POST /api/save_lyrics` runs as a write job, refuses while another job holds that album,
and refuses a track that is not `done`. Saving writes the sidecar, marks it yours, records the hash,
derives `synced`/`plain` from the text and rewrites the tag; an empty save clears. Nothing is looked
up, so the editor can never replace your words with LRCLIB's.

## 2. The UI sends people to a terminal — DONE (P10: repair; P11: the preview)

The fetch log says "`ytalbum repair` unifies them", but `repair` does not exist in the web UI:
no button, no API route. A dry run exists only on the command line too. A UI user who follows
the hint has nowhere to click.

Wanted: a repair action (with the same one-line-per-rename log) and a preview for a fetch.

**Done in P10:** a "Repair library" button beside "Update library", running the same `Service.repair`
as a write job, its one-line-per-change log and "N album(s) tidied up" summary landing in the job
log. It asks first, with README's paragraph about what repair does, because it renames folders and
files across the whole library. The fetch-time spelling hint now names the button as well as the
command, in one sentence that serves both kinds of user. **Done in P11:** the preview a URL already got is now the outcome — it merges with what is in the
library instead of showing a fresh reading (so it no longer promises names a fetch would not write),
says whether the album is already here, marks tracks that have left the source, and can be skipped
with Shift+click on Go so it never becomes a compulsory click.

## 3. Refetching lyrics is all or nothing — DONE (P9, DESIGN §9.27)

Shift-click on "Fetch lyrics" refetches the whole album. After fixing one track's title the
natural wish is "look this one up again", and the only way to reject a bad match is deleting
the file on disk.

Wanted: per-track "look up again" and "not these words", the latter behaving like deleting the
sidecar.

**Done, and the second action is stronger than "like deleting the sidecar":** rejecting remembers
the entry's id on the track (`lyrics_rejected`), so no later lookup can choose it again — a pass and
a `--refetch` included, which deleting the file never achieved. The next best candidate is taken
straight away if one fits. Neither action is offered for words marked as yours; the editor's Delete
is that path.

## 4. Reordering by typing numbers — DONE (P15, DESIGN §9.32)

Typed positions land correctly since P3, but typing numbers into 56 rows is a poor way to
reorder an album. No drag and drop; the other rows renumber only after save, so mid-edit the
column shows contradictions.

Wanted: drag to reorder, rows renumbering live.

**Done:** a grip in the position cell, dragged with pointer events so touch works too; the row moves
as the pointer passes others and every disc renumbers live, so the column never contradicts itself
mid-edit; Escape cancels; Alt+↑ / Alt+↓ is the keyboard equivalent. Nothing is saved until the
album's save. A cross-disc drop needed one correction on the server: a number the user typed now
counts in the disc the track is being put on.

## 5. No way back from an edit — DONE (P12, DESIGN §9.29)

The plan keeps the derived value (`auto`) for every field a user overrides, but the UI offers
no "reset to what ytalbum found". An edited album artist is frozen out of harmonisation and
repair with no visible way to opt back in.

Wanted: a reset affordance per edited field, shown where the "from" column already says `user`.

**Done:** the badge that says "you" is the button. It restores the value from `auto`, drops the USER
mark, and saves through the ordinary edit path so folder, file names and tags follow. The order flag
resets too, lifting the flag without renumbering anything now. Lyrics are not included — the
editor's Delete is their way back. In this library 59 of 246 albums and 83 tracks would show the
affordance.

## 6. Two silent-lag spots — DONE (P13, DESIGN §9.30)

Ownership of an edited sidecar and the status of a deleted one update only when a pass walks
the album (documented in DESIGN §9.21). Between passes the UI can show a ♪ for lyrics that are
gone. Safe, but a user would call it a bug.

Wanted: reconcile the album's lyrics state when the album view is opened (read-only check,
cheap), or a filesystem watcher. Item 1 removes the lag for edits made in the UI itself.

*After P8:* the lag is now only for changes made **outside** the UI, since the editor refreshes the
row and the panel itself. The panel also reads the file on every open, so opening it shows the
current words even when the row's ♪ is stale — which shrinks this item to the marker and the badge
counts rather than the words themselves.

**Done in P13:** opening an album reconciles its lyrics against the disk before the view is drawn,
so a sidecar edited or deleted outside ytalbum is recognised at once; a write job then makes it
durable and rewrites the tag. When plan and files agree nothing is written and no job exists. The
grid is deliberately not reconciled (246 albums per render); its counts catch up when an album is
opened. Measured cost on a 56-track album: about 2 ms added to the open.

## 7. The ⏱ chip is a diagnosis, not an action — DONE (P14, DESIGN §9.31)

The chip says a track is too long against its reference, never where to cut (a data limit,
DESIGN §9.8). The trim inputs are bare seconds fields.

Wanted: "play from here / set start / set end" next to the player, so the chip leads to a trim
instead of to arithmetic.

**Done:** marking from playback existed already; what was missing was the arithmetic. The trim bar
now shows what the pending marks would leave against the length MusicBrainz or LRCLIB knows, in the
chip's own colours, updating as the marks move — so the gap can be watched closing before saving.
Plus *▶ from start* to hear the start mark, a line saying when the untouched original is playing, and
marks rounded to a tenth of a second.

## 8. Documentation cleanup — DONE (P16)

Added 2026-09-26 by the owner, to run after every code item above is done. README, DESIGN and the
catalog grew by nine slices and four catalog sections in one day, each written as the change
landed. Read them once as a newcomer would: remove what describes states that no longer exist,
merge sentences that say the same thing twice, make the README's feature list and command table
match the UI as it is now (editor, per-track lyrics actions, repair button, preview, reset
badges), check every count, every "§9.x" pointer and every "fixed in Px" note, and keep DESIGN's
slice log as the history it is rather than rewriting it.

**Done:** README's album-view caption had grown by accretion across six packages into an unreadable
paragraph — what belongs to a newcomer moved into the feature list, the captions are captions again.
The test count and the suite's runtime were stale (458/~17 s → 516/~40 s), `POST /api/lyrics` was
missing from the API table, and the lyrics-ownership rule was stated twice. DESIGN's slice log is
untouched; §12 gained the decisions of the day, one line each with its pointer, including the one
(repair behind a confirm) that lived only in a commit message.

## 9. Retake the screenshots — DONE (P17)

Added 2026-09-26 by the owner. `docs/screenshots/` and the README's images show the UI before the
lyrics editor, the reset badges, the repair button and the preview states existed. Retake them at
the same sizes on the final code. Rule, unchanged: every screenshot shows **My Dark Lullabies**
only — the repo does not display full artist discographies.

**Done:** all four retaken on the finished code from the installed service and the real library,
read-only, same sizes (`library.jpg` 1920×1080, `search.jpg` and `settings.jpg` 1280×860) except
`album.jpg`, which is 1184×1441 rather than 1185×1142 because the album view has genuinely grown —
a grip column, the lyrics panel's three actions, the reset badges. Paths in the settings capture are
rewritten in the DOM before the shot, as the previous one did: nothing is saved, and the user's home
directory does not belong in a public README. The first capture found the invisible reset badge
(fixed in 914a22a) and a second cosmetic defect, reported separately.

## 10. JavaScript test harness for `app.js` — DONE (P19, DESIGN §9.33)

`src/ytalbum/webui/app.js` is around 1,500 lines and carries real logic: the length target while
trimming, the drag-and-drop arrangement and its live renumbering, the lyrics panel and its ownership
display, the reset affordance, the player's trim marks and the filter's folding. None of it has a
unit test. What holds today is that the arithmetic lives in Python where it is tested
(`plan.trimmed_gap`, `plan.length_gap`, `placed`, `arrange`, `reset_field`), and every browser
behaviour is exercised through Playwright in `docs/qa-catalog.md` sections H and K–Q — which is
evidence, but it is not a suite that runs on every push.

The cost of the alternative is real: a runner (node's own `--test`, or vitest), and `app.js` split
into importable pieces or given an export shim, which is a refactor of a file that currently has no
build step at all — a property worth keeping.

**The user's call**, not the reviewer's and not mine. Raised by the assistant at the end of the
backlog; deliberately left open rather than decided quietly in a feature package (DESIGN §9.31).

**Approved and done:** `webui/logic.mjs` holds what the page computes rather than draws and is
imported by `app.js` as a module — no build step, no npm. 23 tests under `node --test`, run by
`uv run pytest` through `tests/test_js.py` and by CI, and a shared case table
(`tests/shared/trim_target.json`) that both `plan.trimmed_gap` and `trimTarget` are tested against so
the twins cannot drift. What stays browser-only: anything needing a DOM, which is what the catalog's
Playwright sections are for.

## 11. The drag grip is hard to find — DONE (P21, catalog T5/T6)

Added 2026-09-26 by the assistant, from looking at `docs/screenshots/album.jpg`. Rows can be
dragged since P15, and the only sign of it is `⋮⋮` in the position cell: `var(--muted)`, `.9em`,
`letter-spacing: -.12em`, which at this size renders as one faint vertical column rather than two
of dots. It is legible — measured 5.78:1 in dark and 5.49:1 in light against the panel, well past
any text threshold — so this is discoverability, not contrast: nothing about it says *pick me up*
until the pointer is already on it, and the `title` that explains it (and names the Alt+↑ / Alt+↓
alternative) only appears on hover. A user who never hovers the leftmost 10 px of a row will not
learn the album can be reordered by hand.

Three ways out, roughly in order of cost:

- **A stronger glyph.** `⠿` (braille pattern dots-123456) or `⣿` draws as a proper two-column
  grid at this size; `⋮⋮` needs positive letter-spacing to stop merging. CSS only.
- **A hover affordance on the row**, not on the grip: the grip darkens to `--ink` and gains a
  faint background when the pointer is anywhere on the row, so it announces itself before it is
  aimed at. `.grip:hover` already does the colour half for the grip alone.
- **A one-time hint** under the track table the first time an album is opened ("Drag a row by its
  grip to reorder — or Alt+↑ / Alt+↓"), dismissed and remembered. The most discoverable and the
  only one that needs state, in `localStorage` or in settings.

Keyboard users are already served: the position field takes a typed number, and Alt+↑ / Alt+↓
moves the focused row. Nothing here is a functional gap — it is whether the feature is findable.

**Decided and done 2026-09-27:** the first two, CSS only — a stronger glyph and a row-level
affordance. No stored state: a hint that must be dismissed is a nag, and it would be the only
remembered UI state in the page. The glyph was chosen by looking: `⠿` and `⣿` were rendered in the
live rows beside `⋮⋮` and photographed at 10×, and the braille pair falls back to another font on
this system — softer, a pixel low, 18.9 px against 16.7 — so `⋮⋮` keeps its place at 1.05em with
`letter-spacing: .06em` instead of `-.12em`, and nothing depends on a font having braille. The row
now brightens its own handle on hover and on keyboard focus anywhere in it (5.78 → 14.05 in dark),
while touch, which has no hover at all, is served by the separated columns alone — verified by
listing every rule that matches `.grip`: three, of which two only add. `app.js` is untouched, so the
drag, the tooltip and the Alt+↑/↓ alternative are the ones P15 shipped; one drag was run afterwards
to prove the handle still picks a row up.

## 12. Control boundaries are below the WCAG contrast minimum — DONE (P21, catalog T1–T4)

Added 2026-09-26 by the assistant, out of P20. Fixing the borders on a *highlighted* row (they
were invisible, 1.03 dark / 1.08 light with the band composited) showed that the same borders are
thin everywhere else too — the highlighted row was the extreme of a condition the whole page has.
`button.quiet`, the text inputs and `.badge` all draw their edge in `var(--line)`, which is chosen
to divide two backgrounds rather than to outline a control:

| pair | dark | light |
|---|---|---|
| `--line` on `--panel` (buttons, inputs, badges inside a panel) | **1.20** | **1.32** |
| `--line` on `--bg` (the same controls on the page background) | **1.32** | **1.21** |
| `--muted` on `--panel` (for comparison — the grip, secondary text) | 5.78 | 5.49 |
| `--accent` on `--panel` (for comparison — the reset badge's border) | 5.71 | 5.62 |

WCAG 2.2 **1.4.11 Non-text Contrast** asks 3:1 for the visual boundary of a control, so every
value in the first two rows fails by a factor of about two and a half. The controls are not
invisible — they carry a label, and inputs also carry `--panel` against `--bg` — but their edges
are decoration rather than a boundary anyone can rely on.

A fix touches one line if it is taken at the root: `--line` in `:root` and in both dark blocks
(`style.css:2`, `:8`, `:13`). Raising it to about `#c4bfcd` in light and `#565165` in dark clears
3:1 on both backgrounds, and changes every divider on the page with it — table row rules, panel
edges, the header's underline — which is the actual decision, because those dividers are quiet on
purpose and this would make the tables look ruled. The alternative is a second token
(`--edge`, contrast-checked) used only by `button.quiet`, `input`, `.badge` and the row controls,
leaving `--line` to go on dividing; more places to keep right, but the page keeps its calm.

Not urgent and not a regression — it has been true since the first stylesheet. It is here because
it was measured, and because the same measurement is what caught the highlighted-row case.

**Decided and done 2026-09-27:** the second option — a `--edge` token used only by controls, with
`--line` left to go on dividing at its old strength. `--edge` is `--line`'s own hue and saturation
moved along the ramp until both backgrounds clear 3:1 with a little to spare: `#9185a8` in light and
`#6f6882` in dark, measured in the page at **3.16 / 3.47** dark and **3.43 / 3.16** light against
`--panel` and `--bg` (from 1.20 / 1.32 and 1.32 / 1.21). It reaches `button.quiet`, the text and
number inputs, `select`, `.badge`, the row controls and `.to-top`, plus `textarea`, which had no
border rule at all and was drawing the browser's default box.

The question the decision asked — whether this makes 2df1cee's highlighted-row rule redundant — is
answered **no** by measurement: composited over the playing band, `--edge` reaches only 2.53 in dark
and 2.82 in light, so that rule stays and still measures 4.66 / 4.51. An input never needed it; it
paints its own `--panel`, so the band is never behind its border.

Three controls were deliberately left alone, each measured and recorded rather than changed quietly:
the delete ✕, whose transparent border is what makes it a text button beside the boxed ♪ (1.00 at
rest, and the row rule already gives it an edge where the glyph would otherwise float); the player's
trim handles, whose `--panel` ring separates them from the track line while the handle itself is
accent on panel at 5.71 / 5.62; and `.card` and `.pick`, which are identified by a cover and a title
rather than by an edge. All four screenshots were retaken on the new edges.

## 13. Re-timing the lyrics you already wrote — DONE (P23, DESIGN §9.35)

Added 2026-09-27 out of P22. A track pointed at another video (§9.34) keeps the user's words, and
the panel says when their timestamps were written against the old file — but it cannot fix them, and
P22 deliberately did not try: shifting someone's stamps is a change to their work, and a silent one
would be worse than the notice. What is missing is a **shift**: "move every timestamp by −2.4 s",
applied in the editor, visible before it is saved, and undone by cancelling.

Not automatic. Two recordings of the same song rarely differ by a constant — one has a longer intro
*and* a shorter outro — so a computed offset would be right for the first verse and wrong by the
last. A number the user types, applied to every stamped line, is honest and enough.

**Superseded 2026-09-27 by TASK P23**, which takes this as one of four parts (tap-to-stamp, per-stamp
play and nudge, a file-clock readout, and this shift). The reason it grew: stamps typed from the
player's display land up to 3 s late on a *trimmed* track, because a trimmed track plays from its
original and the player's clock is the original's while the stamps belong to the cut file's. The
shift alone would only move a whole set of wrong stamps.

**Done 2026-09-27 (P23), as four parts rather than one.** The shift is there — a typed number of
seconds, every stamped line, unstamped lines left alone, reversible by shifting back — but it is the
last resort now rather than the tool. The other three remove the reason stamps are wrong in the first
place: **Ctrl/⌘+Enter** (and a button) writes the moment being heard onto the cursor's line,
*converted to the file's clock*, rounded to a tenth, and moves on; **Alt+Enter** plays from a line's
stamp and **Alt+←/→** move it by a tenth (with Shift, half a second) and play it back, so alignment
is done by ear; and a readout shows the position **in the file**, beside the player's own time
whenever the two differ. Nothing is saved until Save, and a stamp written this way is byte-for-byte
what a hand-typed one would be. Catalog section V.

## 14. Automatic lyric timing — DONE (P25, DESIGN §9.36, measured in `docs/spikes/2026-09-alignment.md`)

Added 2026-09-27 out of the P24 spike, which measured it on twenty tracks of the real library rather
than arguing about it. The short of it: **forced alignment works**. wav2vec2 CTC alignment on a
separated vocal stem placed the lines of 17 of 20 tracks within a median of **0.94 s**, twelve of
them under half a second, with **no penalty for growled vocals** (0.37 s against 0.34 s for clean
singing) and none for German. It needs **442 MB of models and no GPU** — 2.7× real time for the
separation, 8–11× for the alignment, about two minutes for a four-minute track on an ordinary
processor. It also found two `.lrc` files in the library whose stamps are six seconds early.

Three things the spike says any such feature must do, all of them measured rather than assumed:

- **Run two aligners and compare.** Neither is safe alone — the Whisper-based one is half a song out
  on 5 of 20, the CTC one on a different 1 of 20 — but where they agree within a second they are
  right, including where LRCLIB is wrong. Where they disagree, say so and place nothing.
- **Separate first.** CTC alignment on the raw mix is unusable (median 13 s); on the vocal stem it
  is the best arm there is. The same separation does *not* reliably help transcription.
- **Write into the editor, not to the disk.** P23's editor already holds the text, plays a stamp,
  nudges it and saves it; an align action that fills the textarea needs no new write path and leaves
  the ownership contract (§9.21) exactly as it is — the words stay the user's, the user presses Save.

**Transcription is the weaker half** and is a separate decision: three quarters of a clean song's
lines, half of a harsh one's, seven times the cost, always needing a human afterwards.

**The question for the user is not really "shall we do this", it is "where does the inference
live".** The spike's fifth section evaluates that: a `Timing` provider boundary with `none` as the
default, `local` as an optional extra, a self-hosted HTTP endpoint on another machine, and
commercial APIs — of which only ElevenLabs sells alignment of supplied text, at $0.22 per audio
hour, i.e. **$8.60** to time every track in this library that has words but no timings. The core
product keeps working with none of it installed, and the recommendation is to define the boundary
before building anything behind it.

**Decided and done 2026-09-27 (P25).** The boundary is a `Timing` Protocol with `capabilities()`,
`align()` and `transcribe()`; `none` is the default and the app is byte-for-byte what it was under
it. Two providers ship: **`local`** (the `ytalbum[timing]` extra — torch, torchaudio, demucs;
~1.5 GB with the CPU build of torch, no Whisper) and **`http`** with `ytalbum timing-serve`, which
is the one this household will use, because the app then needs no machine-learning dependency at
all. Alignment fills the editor's textarea and the user saves it: the words stay theirs,
`lyrics_timed_by` records whose clock it is, and the panel says "timed by local" beside "yours".
Measured in catalog W: 12.2 s per track with a GPU, 108.4 s without, and both of the `.lrc` files
the spike found to be six seconds early were corrected. Transcription, a library-wide pass and the
automatic two-aligner cross-check were deliberately left out — items 15 and 16.

## 15. A commercial timing provider — DONE (P26, DESIGN §9.37)

Added 2026-09-27 out of P25. The boundary (§9.36) makes this a small piece of code: an
`ElevenLabsTiming` with the same three methods, a key in the config, and the audio posted to their
[Forced Alignment API](https://elevenlabs.io/docs/overview/capabilities/forced-alignment) — 29
languages including German, **$0.22 per audio hour**, which is **$8.60** for every track in this
library that has words but no timings, and about **1.5 cents** for one track from the editor. It is
the only mainstream vendor that sells alignment of *supplied* text; the others sell transcription.

**What makes it the user's decision and not a technical one: the audio leaves the house.** Local and
HTTP providers keep everything on the network by construction. A commercial provider uploads the
recording — to a company, under their retention terms, for a few cents. That is a preference, not a
trade-off the code can weigh, so nothing is built until they say so.

Worth having anyway if they do: it needs no GPU, no 1.5 GB of models, no install at all, and it is
the answer for a machine that cannot run inference and has no other machine to ask.

**Decided and done 2026-09-27 (P26).** The user's answer: the audio comes from YouTube in the first
place, so it may leave the house — with two conditions, that **no money is spent building this** and
that **no key is needed to finish it**. Both held: the clients were written against the vendors'
documentation, verified against a server of my own speaking their shapes, and the live test per
vendor is skipped unless someone puts a key in the environment. Not one request has been billed.

Two vendors, because the two capabilities have two markets: **ElevenLabs** (align *and* transcribe)
and **Deepgram** (transcribe only, and it says so — with Deepgram configured the editor shows no
alignment action). Beside them the package added the **draft**: where a track has no words at all, a
transcribing provider can propose some, labelled *a machine's guess, half a song for some tracks*,
never offered where words exist, and remembered on save as `lyrics_words_by` so the panel can say
"words by elevenlabs" beside "yours". The audio leaving the machine is stated in the README, in the
settings row with the vendor's dated list price, and in a confirm before the first request of a
session; nothing is ever retried, because a retry on a metered endpoint is a second invoice.

## 16. A second aligner, for an automatic cross-check — DONE (P27, DESIGN §9.38)

Added 2026-09-27 out of P25, deferred from P24's recommendation. The spike's strongest safety result
was that **two independent aligners agree where they are right**: a Whisper-based aligner and the
wav2vec2 CTC one agreed within a second on 16 of 20 tracks and were both correct there, including on
the two tracks where LRCLIB was wrong; where they disagreed, one of them was half a song out. A
provider that ran both and refused to place anything they disagree about would be safe without a
human in the loop.

**The cost is the reason it is not in P25:** the second aligner is faster-whisper `large-v3`,
**3.09 GB** — seven times the models the whole local provider needs today, and the thing the
architecture exists to keep out of the baseline. So it belongs as its own optional extra
(`ytalbum[timing-check]`), off by default, for someone who wants a library-wide pass without
listening to every track.

What P25 ships instead is option 1 of the spike's three: one aligner, `unplaced` honoured, the
stamps labelled as a machine's proposal, and P23's ▶ on the first line as the check — which takes a
second and catches exactly the failure mode that occurs (out by half a song, never by half a second).

**Done 2026-09-27 (P27)** as its own extra, `ytalbum[timing-check]`, off unless installed. The build
corrected one assumption from the spike and paid for three defects that only running it could find
(catalog Y): the second model has to hear the **mixed** track — on the separated stem it left 11 of 42
lines unplaced and put the rest 20 s early — and a failure of the check must never take the alignment
with it, which the first three runs all did. `verified()` keeps the first method's stamp where the two
agree within `timing_verify_threshold`, drops it where they do not, and places nothing at all where
they disagree about most of a track. The same extra makes transcription local, so a draft no longer
needs a vendor.

## 17. One provider for two jobs — DONE (P28, DESIGN §9.40)

Added 2026-09-27 out of P26/P27. `timing_provider` is a single setting, but the two capabilities are
bought in different places: the machine that aligns best (`local`, free, needs the models) is rarely
the one that transcribes best (a vendor, metered, needs nothing). Today choosing Deepgram for drafts
gives up local alignment, and choosing `local` gives up drafting unless the 3 GB extra is installed.
Split it into `timing_align_provider` and `timing_draft_provider`, keep `timing_provider` readable as
both, and show only the providers that can do each job in each slot.

**Done 2026-09-27 (P28).** Both keys exist and fall back to `timing_provider`, so no config file that
was written before today changes meaning. `kind_for(cfg, capability)` is the single place that
decides; everything that depends on *which* provider — what the page may offer, whether the audio
leaves the machine, the dated list price, the name in the confirm — is answered per slot. Verified
through the page with `local` aligning and `deepgram` drafting (catalog Z): the settings panel offers
no Deepgram in the aligning list, an alignment ran locally in 8 s with no confirm at all, and the
draft button on the same album warned that the audio goes to Deepgram. Nothing was sent: the confirm
was cancelled, which is also what the case is for.

## 18. The service parks 3 GB of VRAM after one alignment — DONE (P30, DESIGN §9.41)

Found 2026-09-27 while measuring P27: the installed service, having aligned one track for the user,
was still holding **2.9 GB** of the laptop's 8 GB card minutes later with nothing queued — enough to
make the next process's separation fail with an out-of-memory. The models are cached per provider
instance for good reason (the next track is free), but an idle desktop app should not sit on a third
of the graphics card. Release them when the job queue goes idle — the reload costs seconds off a warm
disk, which is the right trade for a machine someone is also using for something else.

**Done 2026-09-27 (P30).** Measured first: the idle figure was exactly the peak — **3314 MiB** held
with an empty queue, nothing given back at all. The app's own service builds a provider per job, so
its models were already gone and what lingered was torch's pool; it is emptied the moment no lane is
busy, and the card drops to **180 MiB**, the CUDA context. `ytalbum timing-serve` keeps one engine
alive, so it lets go of the models after `timing_idle_minutes` of quiet (default 5, `0` = never), and
the next request reloads for nothing measurable (11.2 s against 11.8 s). The release never imports
torch — it is a `sys.modules` lookup where no model was loaded. One defect of my own on the way, found
by running it against a real server with a deliberately short timer: the watcher took the models out
of a request that was still being served (catalog AB).

## 19. Publish lyrics to LRCLIB — DONE (P31, DESIGN §9.42)

Decided by the user 2026-09-27. Timing lyrics by hand or checking a machine's proposal is work, and
LRCLIB is where this project takes its lyrics from; giving corrected ones back costs one request. A
**"Publish to LRCLIB"** action, offered only for a sidecar that is the user's own (`provenance.lyrics
= user`) and synced, whose text is not byte-identical to an LRCLIB entry already held — never send
LRCLIB its own words back — and never for a draft nobody has edited or for an instrumental. It uses
the public publish API with its proof-of-work challenge, so no account and no key; it sends the
**file's** duration, because the stamps belong to the cut file (§9.35). One press is one publish, with
a confirm naming everything that leaves the machine and saying that it is public and irrevocable, and
`lyrics_published` recorded so the same bytes are never offered twice.

**Done 2026-09-27 (P31).** Their proof-of-work flow, solved locally (about 17 million SHA-256 tries
for their live `000000FF…` target, six to ten seconds here), and the publish POST is never retried
because a retry is a second copy in a public database. Every refusal says which rule it was, since a
missing button explains nothing. Verified end to end through the page against a server speaking their
documented shapes (catalog AC) — nothing reached lrclib.net, and the only live request in the package
was a single `request-challenge`, which publishes nothing.

## 20. Seed MusicBrainz — OPEN (P32, queued)

Decided by the user 2026-09-27, the other half of giving back. Where an album has no release match at
all, **"Add to MusicBrainz"** opens MusicBrainz's own release editor in the user's browser, pre-filled
by the documented form-seeding mechanism: artist credit, title, type, a Digital Media medium, the
tracklist with the lengths measured from the files, the playlist URL as a relationship, and an edit
note naming ytalbum. **ytalbum submits nothing** — the user reviews and submits, logged in as
themselves, and no credentials ever enter this program. Where a matched recording's length disagrees
with the file's, a **"Correct on MusicBrainz"** deep link with the numbers in the confirm, because the
seeding mechanism does not cover recording edits and pretending otherwise would be worse than saying
so. Compilations and hand-made playlists never get the button: MusicBrainz wants releases that exist
as releases.

## 21. Verify mode cannot tell which method is lost — OPEN (from P27's own evidence)

Added 2026-09-27 out of catalog Y. When two aligners place a track 30–120 s apart, one of them has
lost the song and ytalbum has no way to say which, so §9.38 trusts the primary **by policy**. That
policy is right on the evidence — the condition fired on five of sixteen tracks and the primary was
the accurate one every time:

| track | \|primary − sidecar\| | \|second − sidecar\| |
|---|---|---|
| Lord of the Lost — Argent | median 0.31 s | median 120.21 s |
| Mono Inc. — A Love That Never Dies | median 0.36 s | median 28.26 s |
| Powerwolf — Armata Strigoi | median 0.13 s | median 54.82 s |
| Sabaton — A Lifetime of War | median 0.95 s | median 93.70 s |
| Warkings — Azrael | median 0.10 s | median 59.75 s |

But policy is not evidence about *this* track, and the one track where the second method was better
(Feuerschwanz — *Bastard of Asgard*, 0.60 s against 1.35 s) shows the primary is not always the one to
keep. Four signals are available and none is used yet: the CTC pass's per-line scores, which
`forced_align` already returns; the Whisper pass's own count of segments it failed to align (it
reported 11 of 42 on the run where it was lost); stamps that fall outside the track's length; and
breaks in monotonicity. With any of them the whole-track case could drop the method that is actually
lost instead of the one policy distrusts — and the same signals would let the per-line rule say which
of the two a dropped line should have believed.

