# AudioShrink – Concept & Architecture

This document describes the **why** and **structure** of AudioShrink.
The **usage** is in [README.md](README.md), the **implementation details**
in [audioshrink.py](audioshrink.py) (the authoritative source of truth).

It is **not** maintained version-synchronously – it captures stable design
decisions, not version history (which belongs in the changelog).

---

## 1. Purpose

Create a compact, mobile, uniformly formatted **Opus** mirror copy from a large,
heterogeneous music collection – typically 10–20% of the original size,
without audible quality loss.

---

## 2. Design Principles

| Principle | Commitment |
|---|---|
| Portability | Runs on any Linux system, shell-independent. Minimum Python version **3.8**. Minimal dependencies (only `mutagen` via pip) to avoid process-spawning bottlenecks. |
| Encoder discipline | **opusenc is the only encoder.** ffmpeg is never used for encoding. |
| Mirror invariant | The relative target structure is always identical to the source. |
| Idempotence / Resumability | Repeated runs process only new/changed files; an interruption leaves completed albums consistent. |
| Robustness | Per-file errors do not break the run; writes are atomic (temp file + rename). |

---

## 3. Tool Usage (Binding Task Division)

| Task | Tool |
|---|---|
| Encoding **all** output to Opus | **opusenc** (only encoder) |
| Read native inputs (FLAC/WAV/AIFF) | opusenc directly (tags + covers automatically) |
| **Decode** lossy sources for re-encode | **ffmpeg** → PCM pipe to opusenc (decoder only); tags/covers re-set via ffprobe → opusenc |
| Audio analysis (sample rate, bitrate, tags) | **mutagen** (Native Python library, replaces external ffprobe to drastically improve speed on large libraries) |
| Extract covers (for dedup) | **metaflac** (FLAC) or **ffmpeg** (lossy, unchanged copy) |
| Resize covers (optional) | **ImageMagick** |

> opusenc reads only WAV/AIFF/FLAC/raw-PCM – lossy formats must thus
> be decoded before encoding (ffmpeg as decoder, **not** as encoder).

---

## 4. Architecture Overview

Processing occurs **folder-by-folder** (one "album") because cover deduplication
needs album context. Per folder:

1. **Determine cover plan** (only if work is needed): do all transcoded tracks
   share the same embedded cover, or is there a separate cover file?
2. **Process tracks in parallel** (ThreadPool – the actual work runs in
   opusenc/ffmpeg as external processes). Per file: analyze → decide
   (transcode / re-encode / copy / skip) → execute → log.
   Log output is collected in submission order (readable despite parallelism).
3. **Finalize album cover** (one `cover.jpg`/`.png`, if deduplicated).
4. **Hybrid cleanup, Part 1:** remove orphaned files in this folder immediately.

At the end: **Hybrid cleanup, Part 2:** remove orphaned directories + empty folders.

---

## 5. Core Decisions

- **Format choice per file:** FLAC/WAV/AIFF → Opus; lossy → Re-encode to Opus
  **only if bitrate > threshold**, otherwise copy; images → copy/resize/remove;
  other → copy.
- **Re-encode decision: bitrate only, no genre.** Threshold adjustable
  (default 320 kbps). Rationale: consistent and predictable; avoids slow,
  barely worthwhile re-encoding of already small (low-bitrate) files.
- **Target bitrate/tuning: genre-aware.** Sample-rate basis (160/128/96),
  speech genres → 64 kbps + `--speech`, capped to source bitrate.
  **No** reduction by track length (length is not a measure of bitrate need).
- **Cover deduplication** across **all transcoded tracks** (FLAC **and**
  re-encoded lossy): shared cover → one file in folder, image removed from Opus
  (`--discard-pictures`). Saves multiples of cover volume per album.
- **Cleanup = mirror, threshold-aware:** A target is valid if it is exactly
  what the source currently produces. In particular, a `.opus` from a lossy
  source is valid only if that source would actually be re-encoded – otherwise
  (copy case) it is orphaned. Thus with changed threshold, each file has
  exactly one correct target format.
- **No environment changes:** no setting owner/permissions – files get the
  default permissions of the executing user.

---

## 6. Default Values (At Release)

`--jobs 2` · `--comp 6` · `--reencode-min-bitrate 320` · Re-encode on ·
Cover-dedup on · Cleanup on. All overridable via CLI (see README).

---

## 7. Deliberate Non-Goals

- No track-length-dependent bitrate.
- No "target whitelist": files without source counterpart are deliberately removed
  (strict mirror). Companion files remain as long as they exist in the source;
  only source-less targets are removed.
- No ffmpeg as encoder; no pip dependencies.
---

## 8. Known Issues / ToDo

- **Image Extensions:** When falling back to `ffmpeg` to extract covers from lossy sources, the script currently checks only for PNG and GIF headers. Other web-native formats (like WebP) or BMP are saved with a `.jpg` extension. This is very rare in standard music collections but should be addressed in a future update to avoid confusing external parsers.
