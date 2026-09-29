# A post's captions as words beside the track — a proposal (P59, 2026-09-29)

**Nothing here ships.** R-249 asked for a proposal and R-250 kept it a proposal after the user said
they like the idea. It is written to be built from: what the format holds, whether its stamps land on
the audio as this program takes it, how such words would be marked, and what it would cost.

The case for it is narrow and real. The creator this was measured against posts **audiobooks as
video**, and every one of their posts carries `subtitles: {"en": [{"ext": "vtt", …}]}` — no
`automatic_captions`, so these are captions somebody supplied rather than a machine's guess at the
audio. For a narration, that file *is* the text of what is being read.

## 1. What the format holds

WebVTT, one `.vtt` per language, fetched from the same Mux host the renditions come from. A cue is a
start stamp, an end stamp and one or more lines of text:

```
WEBVTT

00:00:07.120 --> 00:00:11.040
He had been awake for nineteen hours when the
station finally answered.
```

What follows from that shape, and what noaap would have to decide:

- **Cues are not lines of a song.** They are reading-width fragments — usually 2 lines of ~40
  characters, broken where a caption box ends, not where a sentence or a breath does. A 30-minute
  narration is roughly 300–500 cues. Rendered into the lyrics panel as-is, a track's words become
  several hundred rows: the rolling list from slice 68 handles that (it scrolls the box and not the
  page), but nothing has ever put that many rows in it.
- **An end stamp is information noaap's format cannot hold.** `.lrc` has one stamp per line; VTT has
  two. Taking the start and dropping the end is lossless for *display* (the next cue's start is the
  same moment, near enough) and lossy for anything that later wants to know where the speech stopped.
- **Cues can overlap and can carry styling** (`<v Speaker>`, `<i>`, positioning, `NOTE` blocks,
  `STYLE` blocks). All of it would be stripped; the text is what is wanted.
- **It is the whole book.** A caption file of a chapter is the chapter. That is exactly why the rest
  of this proposal is about *not* sending it anywhere.

## 2. Do the stamps land on the audio as noaap takes it?

Almost certainly yes, and it is checkable rather than assumable — which is how it should be built.

The audio is taken with `-vn -map 0:a:0 -c:a copy` out of one rendition of the same Mux asset the
captions belong to. A stream copy keeps the samples and ffmpeg rebases the output to start at zero, so
a cue at `00:07.120` is 7.12 s into the audio **provided the audio stream's first presentation stamp
is zero in the source**. Where that is not true — an audio track that starts late, an asset assembled
with an offset — every cue is out by that constant.

So the build should not trust it: `ffprobe -show_entries stream=start_time` on the downloaded
rendition costs one process and answers it. A non-zero `start_time` is either subtracted from every
cue or, better, refuses the captions and says why — a transcript silently 1.4 s out is worse than no
transcript, and this program's own rule from slice 46 is that a clock you cannot vouch for is not
offered.

One thing this proposal cannot settle without a live fetch: whether Patreon's caption file covers the
whole post or only part of it, and whether its first cue begins at the first spoken word or after a
musical intro. Both are one fetch away and neither changes the design.

## 3. How the words would be marked

They are **the provider's captions**. Not the user's work, not LRCLIB's, not a machine's draft of the
audio — a fourth thing, and the marking has to say so without borrowing any of the three.

- `provenance["lyrics"]` = a new `Provenance.SOURCE`: *these came with the recording*.
- `lyrics_words_by` = the provider's name, as the draft mark already works — so every place that
  already reads "these words are not yours" keeps working unchanged, including the badge in the panel.
- **The claim control is not offered for them.** Slice 69's *"I have corrected these words, they are
  mine"* exists so a person can take ownership of a machine's draft of *this recording*. A caption
  file is somebody else's writing; correcting a typo in it does not make it yours. The editor hides
  the row when the words came from a source, and the refusal says why rather than pointing at a
  control that is not there.
- **Publishing is refused twice over**, which is deliberate belt and braces: once because the words
  are not the user's (slice 42), and once because the track's audio is from a private source (slice
  72) — the second refusal holds even if somebody rewrites every line.
- `lyrics_timed_by` = the provider too: the stamps are the caption file's, not an aligner's.

## 4. What it would cost

| | |
|---|---|
| **Requests** | One GET per track, to the same host the audio came from, during the same fetch. No new service, no key, no account. |
| **Time** | The file is tens of kilobytes; parsing is a regex over cues. Under a second per track, next to a download measured in minutes. |
| **Bytes on disk** | A 30-minute narration is ~35 KB of `.lrc`, against ~30 MB of audio: a thousandth. |
| **Tag size** | Writing several hundred lines into a `LYRICS` tag makes the tag the largest thing in the file's header. Some players truncate it; some phones are slow to scan it. Worth a limit — write the sidecar always, the tag only under a few hundred lines. |
| **Code** | A VTT reader (~60 lines, no dependency: `webvtt-py` is not worth a dependency for this), the `start_time` check, one new `Provenance` value, one editor branch, and the fetch hook. The provider already knows the subtitle URL — it is in the same `info` dict the rendition comes from. |
| **New failure modes** | A caption file that is machine-generated by the platform (`automatic_captions` — none for this creator, but other creators will have them) should be treated as a draft, not as the author's text. Refusing those, or marking them differently, is a decision to take before building. |
| **What it does not cost** | Nothing new leaves the machine. The captions are never published, never looked up against, never sent to LRCLIB or MusicBrainz — that is already true for everything from a private source, and this inherits it. |

## 5. Recommendation

Build it, narrowly: **captions from the same asset as the audio, start stamps only, marked as the
provider's, publish and claim both closed, and refused outright when the audio stream does not start
at zero or when the platform generated them.** Leave "what a caption file means for a track that is
not a narration" alone until somebody has one — for music, LRCLIB and the aligner are better at this,
and a caption file of a song is usually the same words with worse breaks.
