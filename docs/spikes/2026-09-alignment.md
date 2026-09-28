# Can a local model time the lyrics? — a measurement (P24, 2026-09-27)

A spike, not a feature. It answers four questions with numbers, and ends in a recommendation and an
evaluation of where such a thing would belong if it were ever built. **Nothing here ships**: no code
in `src/`, no new dependency, no change to the installed service. Everything was installed into a
throwaway virtual environment outside the repository and removed afterwards.

The questions, from the task:

1. **Forced alignment** — given the plain text of a song and the audio, how accurately does a local
   model place each line? Measured against LRCLIB's synced entries.
2. **Transcription** — for a track with no words, how close does a local model's transcript come to
   the known text?
3. **Separation** — does pulling the voice out of the mix first change either answer enough to be
   worth a second model?
4. **Cost** — model sizes, install footprint, seconds per track on the RTX 4060 and on the CPU.

## The short version

- **Forced alignment works, and works well enough to be useful — but only with two models and only
  if you check them against each other.** The best single arm (wav2vec2 CTC alignment on a
  separated vocal stem) puts **17 of 20 tracks** within a median of **0.94 s** per line, twelve of
  them under half a second. Of the three it does not, two are out by a *constant* offset — and
  those two turn out to be LRCLIB's error rather than the model's — and one is a real failure.
- **No single method is trustworthy on its own.** Whisper-based alignment (stable-ts) failed
  catastrophically — by half a song — on 5 of 20; CTC alignment failed on 1 of 20, a different one.
  Where the two methods **agree with each other** (16 of 20, within 0.13–0.73 s) they are also right
  about the audio. Agreement between two independent methods is the safety valve this feature needs;
  the library's own "failed to align" count is a weaker signal that both over- and under-reports.
- **Separation is not optional.** CTC alignment on the raw mix is unusable (median 13–18 s); on the
  vocal stem it is the best arm there is. Demucs costs ~11 s per track on this notebook's GPU
  and ~61 s on its CPU.
- **The ground truth is not always right either.** On two tracks all four arms agree with each other
  and sit ~6.3 s from LRCLIB — the `.lrc` in the library is early, not the model. An align button
  would *correct* those files rather than break them.
- **Transcription is the weaker half.** A transcript of the raw mix contains about **three
  quarters** of a clean song's unique lines and **half** of a harsh one's, at seven times the cost
  of alignment, and separation does not reliably improve it. It is a draft that always needs a
  human, and for some tracks a draft of half a song.
- **The cost is a machine, not an amount — but the useful half of it is small.** Everything
  together is 13 GB and a CUDA version trap. Alignment alone, which is the part that works, is
  **442 MB of weights and no Whisper**, and it runs at 8–11× real time on a CPU. That distinction is
  what the architecture section is about. Note what this machine is: a high-end notebook with an
  RTX 4060: **the GPU numbers here are the favourable case, not the baseline.**

## Method

### The machine, and why it flatters the result

Everything below ran on a **Dell XPS 16 9640**: an **RTX 4060 Laptop GPU with 8 GB of VRAM**, an
Intel Core Ultra 9 185H (22 threads), 30 GB of RAM and a fast NVMe. That is a high-end notebook of
last year, near the top of what a ytalbum user is likely to own — **not a baseline**. Every GPU
figure in this report is therefore the *favourable* case, and should be read as "on a strong
notebook GPU". The CPU figures, taken on the same machine's processor, are the ones closer to a
typical install; on an older desktop they will be worse again, and the user's own older desktop may
not be a sensible inference machine at all. Where this report says "fifteen seconds", read "fifteen
seconds here, minutes elsewhere".

### The twenty tracks

Chosen from the real library, read-only: the audio and the `.lrc` were **copied** into a scratch
directory and nothing in `~/Music/YouTube Downloads` was opened for writing at any point. All twenty
are `state: done`, `lyrics: synced`, lrclib-owned (not the user's own words), untrimmed, with at
least twelve stamped lines whose last stamp falls inside the file.

Ten are songs I judged to contain **growled or screamed** passages and ten to be sung **clean**;
both languages are in both classes (6 English + 4 German each). The class is my judgement from
knowing the bands, which is exactly the sort of thing that should not go unchecked in a measurement,
so the separated vocal stem was also measured for noisiness (median spectral flatness and
zero-crossing rate over the voiced frames) as a second opinion. It agrees at the extremes — the four
noisiest stems are all "harsh", the cleanest by a distance is *Ben* — and disagrees in the middle,
where a song has harsh *passages* inside mostly clean singing (Lord of the Lost). Read the class as
"contains harsh vocals", not "is harsh throughout".

| id | class | lang | artist | title | length | sung lines | stem flatness |
|---|---|---|---|---|---|---|---|
| 00 | harsh | en | DOMINUM | Die for the Devil | 218.6 s | 45 | 0.038 |
| 01 | harsh | en | DOMINUM | Killed by Life | 210.6 s | 42 | 0.044 |
| 02 | harsh | en | DOMINUM | Can't Kill a Dead Man | 209.3 s | 49 | 0.039 |
| 03 | harsh | en | Lord of the Lost | Argent | 314.5 s | 46 | 0.010 |
| 04 | harsh | en | Lord of the Lost | 2000 Years a Pyre | 261.2 s | 37 | 0.019 |
| 05 | harsh | en | Lord of the Lost | A War Within | 293.1 s | 40 | 0.009 |
| 06 | harsh | de | Feuerschwanz | Berzerkermode | 219.1 s | 65 | 0.039 |
| 07 | harsh | de | Feuerschwanz | Bastard of Asgard | 234.8 s | 61 | 0.034 |
| 08 | harsh | de | Kupfergold | Zombie Malone | 220.6 s | 51 | 0.009 |
| 09 | harsh | de | Die Legende von Nord | Bösewicht | 210.6 s | 39 | 0.033 |
| 10 | clean | en | Sabaton | A Lifetime of War | 350.0 s | 69 | 0.014 |
| 11 | clean | en | Powerwolf | Armata Strigoi | 239.7 s | 31 | 0.010 |
| 12 | clean | en | Mono Inc. | A Love That Never Dies | 284.2 s | 43 | 0.013 |
| 13 | clean | en | Michael Jackson | Ben | 164.8 s | 28 | 0.001 |
| 14 | clean | en | Warkings | Azrael | 252.9 s | 55 | 0.015 |
| 15 | clean | en | Visions of Atlantis | Clocks | 318.9 s | 48 | 0.017 |
| 16 | clean | de | Saltatio Mortis | Brunhild | 219.1 s | 52 | 0.022 |
| 17 | clean | de | Schandmaul | Anderswelt | 233.2 s | 62 | 0.011 |
| 18 | clean | de | Versengold | Butter bei die Fische | 222.4 s | 40 | 0.028 |
| 19 | clean | de | dArtagnan | Alles aus Liebe | 209.0 s | 43 | 0.043 |

946 sung lines in all. The language was decided by counting stopwords in the words themselves.

### What was run

Everything used **faster-whisper `large-v3`** (3.09 GB, `float16` on the GPU) or the wav2vec2
aligners, at temperature 0, with the language given rather than detected.

| arm | tool | what it is |
|---|---|---|
| `align-raw` | stable-ts 2.19.1 `align()` | Whisper's own token timings, forced onto the given lines, on the mixed track |
| `align-vocals` | the same | on the separated vocal stem |
| `wx-align-raw` | whisperX 3.8.6 `align()`, wav2vec2 CTC | CTC segmentation of the whole text over the whole file |
| `wx-align-vocals` | the same | on the separated vocal stem |
| `transcribe-raw` / `-vocals` | faster-whisper via stable-ts | no words given; temperature 0 |
| separation | Demucs `htdemucs`, `--two-stems=vocals` | vocals only, down to 16 kHz mono |

**Why these two aligners.** The task allowed either stable-ts or whisperX; both were run, because
they are different algorithms and it turned out to matter. stable-ts aligns *given text* directly
and, with `original_split=True`, keeps the line breaks as segment boundaries — one stamp per lyric
line, which is the artefact a `.lrc` needs. whisperX's `align()` is normally fed the output of a
transcription pass, because it aligns each segment inside that segment's own time window; given
**one** segment spanning the whole file it does the textbook thing instead — CTC segmentation of the
whole word sequence over the whole audio — and the lyric lines are then the word list cut at the
boundaries it came in with, which is exact. Neither needs pyannote or a Hugging Face token;
diarisation was never loaded.

### How the error is measured

Per line: the model's line start minus LRCLIB's stamp, in the file's clock. Two figures are reported
and they are not the same thing:

- **bias** — the median *signed* error: a constant offset of the whole set. The editor's "shift all"
  (§9, slice 35) removes a constant offset in one action, so a large bias with a small spread is a
  near-miss, not a failure.
- **spread** — the median absolute deviation *around* that bias. Nothing removes this; it is what
  the method actually costs you.

The task's median and 95th-percentile absolute errors and the worst line are reported too.

## 1 + 3. Forced alignment, and what separation does to it

Median absolute error per line / spread around the bias, in seconds. "lines 60/62" is a track where
the aligner merged two lines and there is nothing to compare line by line.

| id | class | lang | artist — title | stable-ts raw | stable-ts vocals | wav2vec2 raw | **wav2vec2 vocals** |
|---|---|---|---|---|---|---|---|
| 00 | harsh | en | DOMINUM — Die for the Devil | 0.85 / 0.45 | 0.74 / 0.40 | 18.88 / 10.43 | **0.19 / 0.08** |
| 01 | harsh | en | DOMINUM — Killed by Life | 0.36 / 0.24 | 0.36 / 0.26 | 38.23 / 14.53 | **0.13 / 0.08** |
| 02 | harsh | en | DOMINUM — Can't Kill a Dead Man | 0.70 / 0.19 | 0.75 / 0.29 | 17.24 / 8.57 | **0.41 / 0.28** |
| 03 | harsh | en | Lord of the Lost — Argent | 122.31 / 29.33 | 125.47 / 33.60 | 34.58 / 7.34 | **0.31 / 0.20** |
| 04 | harsh | en | Lord of the Lost — 2000 Years a Pyre | 0.90 / 0.51 | 0.21 / 0.21 | 9.42 / 8.92 | **0.61 / 0.68** |
| 05 | harsh | en | Lord of the Lost — A War Within | 0.55 / 0.48 | 0.23 / 0.24 | 0.45 / 0.41 | **0.24 / 0.14** |
| 06 | harsh | de | Feuerschwanz — Berzerkermode | 6.17 / 0.21 | 6.19 / 0.30 | 6.08 / 6.24 | **6.38 / 0.26** |
| 07 | harsh | de | Feuerschwanz — Bastard of Asgard | 0.60 / 0.35 | 0.63 / 0.42 | 59.40 / 31.75 | **18.20 / 13.42** |
| 08 | harsh | de | Kupfergold — Zombie Malone | 0.45 / 0.39 | 0.22 / 0.26 | 0.24 / 0.09 | **0.24 / 0.09** |
| 09 | harsh | de | Die Legende von Nord — Bösewicht | 0.46 / 0.23 | 0.29 / 0.32 | 0.28 / 0.11 | **0.29 / 0.09** |
| 10 | clean | en | Sabaton — A Lifetime of War | 93.70 / 25.00 | 76.87 / 31.37 | 57.40 / 24.05 | **0.94 / 0.09** |
| 11 | clean | en | Powerwolf — Armata Strigoi | 54.82 / 11.26 | 0.42 / 0.42 | 6.06 / 4.22 | **0.13 / 0.10** |
| 12 | clean | en | Mono Inc. — A Love That Never Dies | 28.26 / 27.33 | 14.40 / 14.71 | 14.39 / 12.62 | **0.38 / 0.16** |
| 13 | clean | en | Michael Jackson — Ben | 1.36 / 0.24 | 1.29 / 0.33 | 0.67 / 0.37 | **0.67 / 0.34** |
| 14 | clean | en | Warkings — Azrael | 59.75 / 14.66 | 0.63 / 0.49 | 28.94 / 22.60 | **0.12 / 0.07** |
| 15 | clean | en | Visions of Atlantis — Clocks | 6.27 / 0.30 | 6.27 / 0.18 | 6.20 / 5.58 | **6.52 / 0.08** |
| 16 | clean | de | Saltatio Mortis — Brunhild | 0.27 / 0.28 | 0.30 / 0.20 | 0.43 / 0.14 | **0.42 / 0.11** |
| 17 | clean | de | Schandmaul — Anderswelt | lines 60/62 | lines 60/62 | 0.25 / 0.18 | **0.23 / 0.10** |
| 18 | clean | de | Versengold — Butter bei die Fische | 0.38 / 0.19 | 0.30 / 0.30 | 22.51 / 21.32 | **0.17 / 0.20** |
| 19 | clean | de | dArtagnan — Alles aus Liebe | 0.60 / 0.13 | 0.48 / 0.16 | 30.64 / 19.21 | **0.15 / 0.11** |

| arm | tracks scored | median ≤ 1 s | spread ≤ 1 s | median of medians | median spread | s / track |
|---|---|---|---|---|---|---|
| stable-ts, raw mix | 19 | 11 | 14 | 0.85 | 0.35 | 5.4 |
| stable-ts, vocal stem | 19 | 13 | 16 | 0.63 | 0.30 | 4.2 |
| wav2vec2 CTC, raw mix | 20 | 6 | 6 | 11.90 | 7.96 | 5.3 |
| **wav2vec2 CTC, vocal stem** | **20** | **17** | **19** | **0.30** | **0.11** | **4.2** |

Per class and language, pooling all 946 lines, for the best arm:

| group | lines | bias | median abs | p95 abs | spread |
|---|---|---|---|---|---|
| harsh | 475 | +0.13 | 0.37 | 21.48 | 0.34 |
| clean | 471 | +0.22 | 0.34 | 6.59 | 0.29 |
| English | 533 | +0.13 | 0.36 | 6.61 | 0.35 |
| German | 413 | +0.22 | 0.37 | 22.62 | 0.26 |

**Harsh vocals are not the problem.** With separation and CTC alignment, growled and screamed
tracks align as well as sung ones — 0.37 s against 0.34 s — and German as well as English. That is
the opposite of what the classes were chosen to test, and it is the strongest single result here.
The p95 columns carry the two broken tracks and say nothing about the class.

**Separation is what makes CTC alignment work.** On the raw mix it is the worst arm by a distance
(median 11.9 s); on the vocal stem it is the best (0.30 s). CTC segmentation over a sparse word
sequence has nothing to hold on to during an instrumental passage and drifts; separation turns those
passages into silence. Demucs `htdemucs` cost a median of **10.9 s per track** on this notebook's GPU (61 s on its
CPU) — about a fifth of the alignment budget, and it changes the answer completely.

For Whisper-based alignment separation is a smaller, mixed effect: two of its five catastrophes were
fixed (Powerwolf 54.8 s → 0.42 s, Warkings 59.8 s → 0.63 s), one improved, two unchanged.

### The failures, with a line each

- **Whisper-based alignment loses the thread and compresses the rest of the song into what is
  left.** Powerwolf's *Armata Strigoi* (raw mix): the first line is sung at 56.1 s and the model put
  it at 20.0 s — it heard the organ-and-choir intro as words — after which every line followed, and
  the last line, sung at 232.3 s, landed at 93.8 s. The whole song inside the first 95 seconds of a
  240-second file. It is not a stamp being a second out; it is a different song.
- **The library says so, but not precisely.** stable-ts warns "6/31 segments failed to align", and
  those six are visible in the output as zero-length segments. The count separates the two
  populations on the raw mix (all five catastrophes had ≥ 6, no good track did), but it is not
  reliable: on the vocal stem Warkings reported 8 failures and was perfectly aligned.
- **CTC alignment fails the other way.** Feuerschwanz's *Bastard of Asgard* on the vocal stem is the
  one track where wav2vec2 is badly wrong (18.2 s) and stable-ts is right (0.63 s).
- **Line counts are not guaranteed.** On Schandmaul's *Anderswelt* stable-ts returned 60 segments
  for 62 lines: it merged two. The CTC arm cannot do this — it maps words back onto the lines they
  came from — which is a structural advantage for the job, not a matter of accuracy.

### Where the two methods agree, they are right — and LRCLIB is sometimes wrong

| id | artist | the two methods differ by | wav2vec2 vs LRCLIB | stable-ts vs LRCLIB |
|---|---|---|---|---|
| 00–02, 04, 05, 08, 09, 11, 13, 14, 16, 18, 19 | (twelve tracks) | 0.13 – 0.73 s | −0.67 … +0.42 | −1.28 … +0.21 |
| 06 | Feuerschwanz — Berzerkermode | 0.13 s | **+6.29** | **+6.13** |
| 15 | Visions of Atlantis — Clocks | 0.28 s | **+6.52** | **+6.23** |
| 03 | Lord of the Lost — Argent | 124.55 s | +0.26 | −124.22 |
| 10 | Sabaton — A Lifetime of War | 77.91 s | +0.93 | −76.87 |
| 12 | Mono Inc. — A Love That Never Dies | 23.83 s | +0.22 | −14.40 |
| 07 | Feuerschwanz — Bastard of Asgard | −19.11 s | −18.20 | +0.40 |

Two independent methods — a Whisper decoder and a wav2vec2 CTC aligner, different architectures,
different failure modes — agree with each other to within a second on 16 of 20 tracks, and on those
tracks both also agree with LRCLIB. On **Berzerkermode** and **Clocks** they agree with each other
to within 0.3 s and both sit **+6.3 s** from LRCLIB: the `.lrc` in the library is early by six
seconds, and the models are right. An align button would have *fixed* those two files.

On the four tracks where the methods disagree, one of them is badly wrong — and which one varies.
**That is the safety valve**: run both, apply only where they agree, and say so where they do not.
It is more reliable than either model's own confidence signal, and it costs a second model.

## 2. Transcription, where no words are known

Transcribing a song and comparing it to LRCLIB's text measures two different things at once, and the
first one is an artefact: **LRCLIB entries write a chorus once where the singer repeats it four
times**, so a transcript that heard every word perfectly scores a word error rate above 100 % on
insertions alone. Mono Inc.'s *A Love That Never Dies* produced 610 words against the entry's 222,
and its opening lines are word-for-word correct.

Both numbers are therefore reported: the word error rate the task asked for, and — the one that
answers "could this be a draft?" — the share of the entry's **unique lines** that appear somewhere
in the transcript, allowing a fifth of the characters to differ.

| group | WER, raw mix | WER, vocal stem | lines found, raw | lines found, stem |
|---|---|---|---|---|
| clean | 82.4 % | 99.6 % | 76.1 % | 70.2 % |
| harsh | 112.1 % | 92.5 % | 55.6 % | 59.2 % |
| English | 121.2 % | 98.1 % | 64.8 % | 60.6 % |
| German | 79.2 % | 96.2 % | 86.2 % | 63.8 % |

Read the right-hand columns. On the raw mix a transcript contains **three quarters of a clean
song's lines and a little over half of a harsh one's**; four tracks came back essentially complete
(*Ben* 96 %, *Butter bei die Fische* 100 %, *Bösewicht* 100 %, *Alles aus Liebe* 97 %) and two came
back nearly empty, because the model stopped after a few seconds (DOMINUM's *Die for the Devil*
produced 0.11× as many words as the entry has, and found none of its lines).

**Separation does not help transcription.** It rescues some harsh tracks (*Die for the Devil* 0 % →
81 %) and destroys others (*Armata Strigoi* 74 % → 0 %, *Azrael* 20 % → 0 %, *Argent* 75 % → 0 %,
each time because the model produced almost nothing from the stem). The medians move by a few points
in both directions and the variance grows. This is the cleanest contrast in the spike: separation is
**decisive for alignment and a wash for transcription**.

Transcription is also the expensive half — a median of **28 s per track** on the raw mix and 22 s on
the stem, against 4 s for alignment.

**What that means for a feature.** A transcript is a draft that always needs a human, and for a
quarter of tracks it is a draft of half a song. It is worth offering where the alternative is an
empty editor; it is not worth offering as something that "fetches the lyrics" the way LRCLIB does.

## 4. What it costs

### On disk

| | size | what it is |
|---|---|---|
| `faster-whisper large-v3` | **3.09 GB** | the ASR model, for transcription and Whisper-based alignment |
| `wav2vec2` aligner, English | 361 MB | `wav2vec2_fairseq_base_ls960_asr_ls960`, via torchaudio |
| `wav2vec2` aligner, German | 361 MB | `wav2vec2_voxpopuli_base_10k_asr_de` |
| Demucs `htdemucs` | 81 MB | the separator |
| the virtual environment | **9.3 GB** | torch with its bundled CUDA libraries, ctranslate2, whisperX, Demucs and their dependencies |

Total for a working install of everything: **≈ 13 GB**. The medium ASR model would save 1.6 GB of
that (`faster-whisper-medium` is 1.53 GB) and was not measured.

The interesting subtotal is the other one: **alignment alone — the best arm — needs 442 MB of
weights and no Whisper at all.** With a CPU-only torch wheel (≈ 200 MB rather than the CUDA build's
555 MB plus some 4 GB of `nvidia-*` packages) that configuration is roughly **1.5 GB in total**.

One cost that no size table shows: the default PyPI `torch` now ships CUDA 13 libraries and
`ctranslate2` 4.8 wants `libcublas.so.12`, so the first run died with a missing library and needed
`nvidia-cublas-cu12` and `nvidia-cudnn-cu12` installed alongside and put on `LD_LIBRARY_PATH`.
Anything that offers local inference inherits that class of problem on every machine.

### Per track

Median over the twenty tracks (mean length 3.98 minutes): a **strong notebook GPU** (RTX 4060
Laptop, 8 GB) against the same notebook's **CPU** (Core Ultra 9 185H). The CPU runs used `int8` for
Whisper and the same torch build with `device=cpu`. Read the GPU column as the best case a user
might have and the CPU column as the one nearer to a normal install.

| step | strong notebook GPU | CPU | of real time |
|---|---|---|---|
| Demucs separation | 10.9 s | 61.3 s (a 2:45 track) | 20× / **2.7×** |
| wav2vec2 CTC alignment | 4.2 s | 14.7 s (2:45), 43.4 s (5:50) | 40× / **8–11×** |
| Whisper-based alignment | 4.2–5.4 s | 36.1 s (2:45) | 30–50× / **4.6×** |
| Whisper transcription | 22–28 s | 84.0 s (2:45) | 8–10× / **2.0×** |
| model load, cold | 2–120 s | — | (the 120 s was the first load, with the download) |

So the whole recommended pipeline — separate, then align — is **≈ 15 s per track on this strong
notebook GPU and ≈ 2 minutes on its CPU**, and slower again on an older machine. For the 589 tracks
in the library that have words but no timings that is about 2.5 hours with the GPU or a night
without one; for one track in the editor, fifteen seconds here and a couple of minutes on a machine
that has no GPU to lend it.

### Determinism

Two runs of the same track with the same settings, at temperature 0: **byte-identical**. The
alignment produced the same 51 segments with a maximum difference of 0.0 s, and the transcription
produced identical text and identical segments. Nothing here needs a seed to be reproducible.

### Versions, for anyone repeating this

`faster-whisper` 1.2.1 · `ctranslate2` 4.8.2 · `stable-ts` 2.19.1 · `whisperX` 3.8.6 · `demucs` 4.0.1
· `torch` 2.8.0+cu128 (after whisperX pinned it down from 2.14.0+cu130) · `torchaudio` 2.11.0 ·
`jiwer` 4.0.0 · Python 3.12 · driver 580.178.04 · models as named above, all public, **no Hugging
Face token and no diarisation** at any point.

## 5. Architecture: where would this belong, if anywhere?

Written after the measurements and conditional on them. The constraint it is written against is the
user's: **ytalbum must not acquire a practical baseline of "modern GPU plus gigabytes of CUDA, Torch
and model weights"**. Their older desktop may not be a sensible inference machine at all, and the
core product — fetch, name, tag, trim, lyrics from LRCLIB — has to stay exactly as usable with none
of this present. So the question is not "shall we add Whisper", it is "is there a seam here, and
what does each thing behind it cost".

### 5.1 Two capabilities, not one

They are different jobs with different markets, and conflating them is what makes people install a
3 GB model to do something a 361 MB one does better.

- **TRANSCRIBE** — audio in, words out (usually with word timings). This is what every speech-to-text
  vendor sells.
- **ALIGN** — audio *and the words* in, timings out. This is what ytalbum wants most of the time:
  LRCLIB already supplies the words for two tracks in three in this library (2,644 of 3,946), and
  for 589 of them it supplies words *without* timings — which is the align case exactly.

What the measured tools offer:

| tool | transcribe | align | weights |
|---|---|---|---|
| faster-whisper / stable-ts (`large-v3`) | yes | yes (Whisper token timings) | 3.09 GB |
| whisperX's aligner (wav2vec2 CTC via torchaudio) | no | **yes, and best in this spike** | 361 MB per language |
| Demucs `htdemucs` | neither — a pre-step | — | 81 MB |

What the market offers, checked on the vendors' own pages on 2026-09-27:

| provider | transcribe | align supplied text | price (batch) |
|---|---|---|---|
| [ElevenLabs Scribe v2](https://elevenlabs.io/pricing/api) | yes | **yes** — a [Forced Alignment API](https://elevenlabs.io/docs/overview/capabilities/forced-alignment), 29 languages including German, up to 10 h and 675,000 characters per request | $0.22 / audio hour, alignment at the same rate |
| [AssemblyAI](https://www.assemblyai.com/pricing) | yes (Universal-2, Universal-3.5 Pro) | not offered | $0.15 / h and $0.21 / h |
| [Deepgram](https://deepgram.com/pricing) | yes (Nova-3) | not offered | $0.0043 / min = $0.26 / h |
| [OpenAI](https://developers.openai.com/api/docs/pricing) | yes (Whisper, gpt-4o-transcribe, -mini) | not offered | $0.006 / min ($0.003 for mini) = $0.36 / h |

So the split is not academic: **the capability ytalbum needs most is the one only one mainstream
vendor sells**, while transcription is a commodity. A provider interface that models them as one
thing would either force every provider to fake alignment by transcribing, or lock the feature to
the local implementation.

### 5.2 The smallest interface that survives this

In the shape the codebase already uses — `LyricsAPI` is a `Protocol` with two methods and a fake in
the tests; the PO-token helper is a separate process started on demand and switched off by a config
key. Nothing below is implemented; it is the sketch the recommendation refers to.

```python
class Timing(Protocol):
    """Whoever can put words on a clock. `none` is a valid answer to all of it."""

    def capabilities(self) -> frozenset[str]:      # subset of {"transcribe", "align"}
        ...

    def align(self, audio: Path, lines: list[str], *, language: str | None) -> Timed:
        ...

    def transcribe(self, audio: Path, *, language: str | None) -> Timed:
        ...


@dataclass
class Timed:
    lines: list[TimedLine]        # start, end, text — in the FILE's clock (§9, slice 35)
    unplaced: list[int]           # indices it would not place; the safety valve, see 5.5
    provider: str                 # "local", "http://rechenknecht:8770", "elevenlabs"
    model: str                    # "wav2vec2 voxpopuli de + htdemucs"
    version: str                  # of the provider's code or the vendor's API
    parameters: dict[str, str]    # separation, device, compute type, temperature
```

Three things in there are load-bearing:

- **The file's clock.** The provider is handed the file on disk, which is the file the `.lrc`
  belongs to, so nothing has to know about trims. §9, slice 35's conversion stays where it is, in the page.
- **`unplaced`.** Every measured method fails on some tracks, and the failures are not subtle —
  they are half a song out. A provider that cannot say "I did not place these" cannot be used
  safely, so it is in the type rather than in a comment.
- **Provenance.** What wrote these numbers has to travel with them, because it ends up in the plan
  and on the page (5.5).

`capabilities()` is what lets the UI stay honest: a provider that only aligns never shows a
transcribe action, and the default provider — `none` — shows neither.

### 5.3 The four implementations, and what each costs

**none** (the default, and what an installation has until someone changes it). No dependency, no
model, no network. The app is exactly what it is today. This is the case that must keep working, and
the interface exists mainly to make it trivial: `capabilities()` returns an empty set and every
button that needs one is not rendered.

**local**, an optional extra (`pip install ytalbum[timing]`), imported only when configured, weights
fetched on first use. The measurements above are its cost — **measured on a high-end notebook with
an RTX 4060, which is the favourable case and not what most installations will have**. What takes
fifteen seconds here takes minutes on a machine without that GPU, and on an older desktop longer
still. There are two very different versions of it:

| | alignment only (the recommended shape) | everything (alignment + cross-check + transcription) |
|---|---|---|
| Python environment | torch (CPU wheel ~200 MB) + torchaudio + demucs ≈ **1 GB** | torch with CUDA + ctranslate2 + faster-whisper + stable-ts + whisperX ≈ **9.3 GB** |
| weights | wav2vec2 361 MB per language + Demucs 81 MB ≈ **0.5 GB** | + faster-whisper `large-v3` **3.09 GB** ≈ 3.7 GB |
| total on disk | **~1.5 GB** | **~13 GB** |
| GPU | optional | effectively required |

The first column is the interesting one: **the best-performing arm in this spike does not use
Whisper at all.** Alignment is wav2vec2 CTC over a separated stem, and both of those models are
small. The 3 GB model earns its place only for the cross-check (5.5) and for transcription, which is
the weaker half anyway.

One cost that does not show up in a size table: getting there took a CUDA version fight. The default
PyPI `torch` ships CUDA 13 libraries; `ctranslate2` 4.8 wants `libcublas.so.12`, and the first
alignment run died on it. An installation that offers local inference inherits that class of
problem, on every machine, for ever.

**self-hosted HTTP** — the same local implementation behind a thin server, on whichever machine in
the house has the hardware, reached over the LAN. Privacy identical to local: nothing leaves the
network. Dependencies inherited by the laptop: none — it holds a URL and a `httpx` call it already
has. The machine that is always on in this house has four cores and no GPU, and §4 says that is
enough: separation runs at 2.7× real time and CTC alignment at 8–11×, so a track costs it a couple
of minutes rather than fifteen seconds. For a button in the editor that is acceptable; for a
library-wide pass it is a night's work, which is what nights are for. It is also the shape that
makes a spare GPU machine useful without touching the laptop's install.

**commercial API** — no local footprint at all, a key in the config, and the audio leaves the
machine. That last point is the one to decide on principle rather than on price, because the price
is small:

| | one track from the editor (3.98 min) | the 589 tracks that have words but no timings (39 h) | the 1,302 tracks with no words (86 h) | the whole library (261.9 h) |
|---|---|---|---|---|
| ElevenLabs (align or transcribe) | $0.015 | **$8.60** | $19 | $58 |
| AssemblyAI Universal-2 (transcribe) | $0.010 | — | $13 | $39 |
| Deepgram Nova-3 (transcribe) | $0.017 | — | $22 | $68 |
| OpenAI Whisper (transcribe) | $0.024 | — | $31 | $94 |

(The library, measured read-only: 3,946 finished tracks, 261.9 hours, mean 3.98 minutes; 2,055 have
synced lyrics already, 589 have words without timings, 1,302 have neither.)

Latency is roughly a wash for one track — a few seconds either way, plus the upload — and the
commercial side wins outright on a bulk pass, because it is not queued behind one laptop GPU.

### 5.4 Is "entirely optional" practical here? Yes, and cheaply

- **Configuration.** `Config` is a dataclass over one TOML file, already carrying a three-valued
  `pot_mode` for exactly this kind of thing. A `[timing]` table with `provider = "none" | "local" |
  "http" | "elevenlabs"`, plus `endpoint` and `api_key`, fits without inventing a mechanism. The web
  UI's settings panel already renders a chosen subset of the config and `/api/state` already carries
  it to the page.
- **Hiding the actions.** The page renders from `/api/state`; the lyrics editor would ask for
  `state.timing.capabilities` and render "Align these words" only if `align` is in it. With the
  default provider the set is empty, nothing is rendered, and the page is what it is today — the
  same way the ⇉ channel-trim button is only rendered when a track has a channel.
- **Jobs.** Alignment changes a sidecar, so it is a write-lane job like `save_lyrics`: one at a time,
  cancellable at the next safe point, blocked while another job holds that album. Nothing new. A
  library-wide pass would be a write-lane job that reports per track, like `lyrics`.
- **The ownership contract (§9, slice 21), which is the part that needs care.** Alignment derives *stamps*
  for words that may be the user's own. The words must not change and their ownership must not
  change; only the timings are new, and they were made by a machine.
  The clean way out is the one P23 already built: **the align action writes into the editor's
  textarea, not to the disk.** The user sees the stamps appear in the text they are editing, can
  play them, nudge them, shift them, and presses Save — which is the existing `save_lyrics` path,
  with `write_sidecar` recording `lyrics_sha` and `lyrics_for_source`/`lyrics_for_length` exactly as
  it does today. No new write path, no new ownership rule, and the user's hand is on the save.
  What should still be recorded is *who timed it*: a `lyrics_timed_by` beside the existing
  `lyrics_for_*` fields, shown in the panel as "timed by <provider>" rather than as "yours", so that
  a later reader knows the words were the user's and the clock was a model's. And the `unplaced`
  lines are shown as lines the provider would not place, left without a stamp rather than guessed.

### 5.5 Recommendation

**A provider boundary is warranted, and it is worth defining before anything is built behind it.**
The measurements say so three times over:

1. The capability that matters (align) is small, cheap and mostly solved — 0.30 s median error, no
   penalty for growled vocals or for German — and it is *not* the capability that needs a 3 GB
   model or a GPU. Without a boundary, the obvious implementation is "add whisper", which is the
   expensive, worse one — and it is expensive in a way this machine hides, because every quick
   number above was taken on a strong notebook GPU that a typical installation will not have.
2. No single method is safe alone. The rule the evidence supports is **run two and compare**: where
   a Whisper-based aligner and a CTC aligner agree within a second they are right, including where
   the `.lrc` in the library is wrong; where they disagree, one of them is half a song out and the
   only correct action is to tell the user. That rule is a property of the *provider*, not of the
   app, and it is much easier to state in an interface than to bolt on later. A commercial provider
   would substitute its own confidence signal, which is exactly why the app should ask for
   `unplaced` and not for a model name.
3. The costs are not comparable between implementations — 13 GB and a GPU, 1.5 GB and patience, or
   $8.60 for every untimed track in the library — and a user on an older desktop should be able to
   pick the third without the first being installed. The measurements were taken on the best machine
   in this house; the design has to be right for the worst one.

**What to build first, if the user wants the feature at all:** the interface, the `none` default,
and **one** provider — `local`, in its *alignment-only* shape (wav2vec2 + Demucs, ~1.5 GB, no
Whisper), as an optional extra. It is measured, free, private, and — this is the part the CPU
numbers settled — it does **not** need a GPU: 2.7× real time for separation and 8–11× for the
alignment itself, about two minutes for a four-minute track on this notebook's processor, and more
on an older one. The GPU turns that into fifteen seconds; it does not turn the feature on. That is
the whole argument for this shape — **what works here in seconds must still work elsewhere in
minutes**, and a 442 MB CPU pipeline does, while a 13 GB CUDA one simply will not be installed. The HTTP provider is then perhaps fifty
lines around the same code and gives the household its "run it on the box with the hardware" option,
and a commercial provider is a `httpx` call whose main design question is whether the user wants
their audio to leave the house — a question for them, not for the code.

**The one honest tension in that recommendation**, and it should be decided rather than glossed:
the two-aligner cross-check is what makes the result *automatically* safe, and it costs the 3.09 GB
Whisper model, which is the thing the cheap configuration exists to avoid. Three ways out, in the
order I would try them:

1. **One aligner, and the user's ears.** The three tracks it gets wrong are not out by a second,
   they are out by half a song, and P23's editor makes that visible in one click — press ▶ on the
   first line and you know. The action fills the textarea and nothing is saved until the user saves
   it, so a wrong alignment costs a "Cancel". This keeps the install at 1.5 GB and is what I would
   ship first, with the panel saying plainly that the stamps are a machine's proposal.
2. **The second aligner as a second optional extra** for whoever wants the automatic check, off by
   default. The interface does not change: a provider that runs two models and compares them is
   still one provider.
3. **A cheaper second opinion than Whisper**, unmeasured and therefore only a suggestion: the same
   wav2vec2 alignment run against the raw mix as well as the stem, and the two compared. It would
   have caught every failure in this spike, because the failures are gross — but that is an
   observation about twenty tracks, not a measurement of the rule, and it would need its own.

**What not to build:** transcription as a headline feature. On this library it recovers three
quarters of a clean song's lines and half of a harsh one's, at seven times the cost of alignment,
and it always needs a human afterwards. It belongs — later — as "draft the words for a track that
has none", clearly labelled as a draft, behind the same boundary, and probably pointed at a
commercial provider rather than a local model.
