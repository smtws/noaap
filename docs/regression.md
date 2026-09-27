# The regression corpus

What P27, P33 and P35 measured, turned into tests, so that a later change has to face the evidence
rather than the summary. Two suites.

| | `tests/test_regression_corpus.py` | `tests/test_corpus_audio.py` |
|---|---|---|
| runs | in `uv run pytest`, always | opt-in only |
| needs | nothing | a library, the `timing` extra, minutes |
| reads | the recorded fixtures | real audio, real models |
| catches | a rule that changes its mind | a rule that keeps its mind while the software breaks |

```sh
uv run pytest                                          # the fast half, offline
YTALBUM_CORPUS_AUDIO=1 uv run pytest -m slow -v        # the slow half
YTALBUM_CORPUS_AUDIO=1 YTALBUM_CORPUS_LIBRARY=~/Music/Yours uv run pytest -m slow
```

## What it protects

- **Which method lost the song** (§9.44). Argent: the second is named, the first is not. Bastard of
  Asgard: neither, from span alone. The five lost methods are named and the thirteen others are not,
  including the two tracks held out until after the thresholds were fixed.
- **The whole-track rule keeps every stamp** (§9.38), because the original rule was measured
  backwards: five for five it discarded the accurate method's work, 244 correct stamps.
- **An entry that is nearly this recording** (§9.46). Gangnam Style rejected as another song; Kalte
  Spuren (Live) keeps the words and loses the stamps; Ringelpietz never aligned at all; Blackbeard
  taken whole although it is 4.4 s outside the old rule; the six Feuerschwanz inconclusives decided
  by nobody; and the five population counts — 143 / 16 / 1 / 29 / 14 — derived from the recordings
  through the shipped functions.

## What it forbids

Four heuristics were proposed and disproved. Each has a case that must keep failing it.

| heuristic | why it is dead |
|---|---|
| stamps that fall in silence | tracks are 55 to 86% singing; Argent's lost method has **0 of 46** stamps in silence |
| a method's own confidence | the kindest threshold misclassifies 3 of 32; the lost method scores **0.60** against the right one's **0.36** |
| the aligner must agree with itself on the mixed track | it would reject **11** correct alignments and rightly flag 1 — and Berzerkermode and Clocks agree while both are 6 s wrong |
| place nothing when the two disagree on the whole track | five for five it threw away the accurate method |

**A new signal has to beat every counterexample before it can decide anything.** That is the rule
this file exists for.

## Thresholds, and the room they have

A case that turns on a threshold asserts the outcome **and** the range the threshold may move in.
The span floor is 0.75 and any value from 0.70 to 0.85 gives the same eighteen verdicts, because the
measurement left a gap: lost methods span 0.41 to 0.70, the ones that followed 0.86 to 1.12. So a
retune inside the gap passes and one outside it fails, naming the gap. The span ceiling is 1.15 and
is not load-bearing between 1.15 and 1.25; removing it is, which is why the case says so.

## Adding a case

1. Put the measurement in `tests/fixtures/` as JSON with a `_source` saying where it came from.
   **Never a filesystem path** — this repository is public and the rows come from somebody's home
   directory. A slow case finds its track by artist, title and album through the album plans instead.
2. Assert the outcome, not the number. "The second method is judged lost" survives a retune; "span
   is 0.41" only says the file was copied correctly.
3. If a threshold decides, assert the range it may move in, and say what is outside it.
4. A slow case skips — never fails — when the track or the extra is absent. A library that does not
   contain somebody else's music is not a regression.

## What the corpus cannot do

P33's raw per-line stamps, sung stretches and confidences were written to a scratch directory that
was deleted with its virtualenv. Everything derived from them survives, so no outcome above depends
on it, but **a new candidate signal cannot be designed offline any more**: it needs a fresh run over
the eighteen tracks, about 40 minutes on a GPU plus the `timing-check` extra. Recorded here so that
whoever wants a new signal knows the price before starting.
