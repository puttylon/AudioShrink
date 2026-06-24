# Changelog

Notable changes to AudioShrink. Format inspired by "Keep a Changelog",
versioning roughly following SemVer.

## 1.0.0 — 2026-06-24

First stable release. Tested in production on a large collection
(Synology DS718plus, Python 3.8).

### Features
- Mirrors SOURCE → TARGET with identical structure; **incremental** (skips current)
  and **resumable** after interruption.
- FLAC/WAV/AIFF → **Opus** (opusenc as sole encoder).
- Lossy sources: copy, or **re-encode to Opus above a bitrate threshold**
  (default 320 kbps; `--no-reencode-lossy`). ffmpeg serves only as decoder;
  tags/covers are re-set.
- **Intelligent target bitrate**: sample-rate basis (160/128/96), speech genres
  → 64 kbps + `--speech`, capped to source bitrate.
- **Album cover deduplication**: shared cover → one `cover.jpg`/`.png` in folder,
  removed from Opus files (FLAC **and** re-encoded lossy); `--no-cover-dedup`.
- **Resize covers** (`--cover-max-size`, ImageMagick) and **remove**
  (`--strip-covers`).
- **Mirror cleanup**, threshold-aware and **hybrid** (orphaned files per folder
  immediately, directories/empty folders at end). Default on; `--no-cleanup`,
  `--dry-run`.
- **Parallel** via ThreadPool (`--jobs`, default 2).
- Robust: per-file errors do not break the run; atomic writes; Python 3.8
  standard library only, no pip packages.
- Helper script `fix_covers.py` for targeted regeneration of missing album covers.
- Test suite: `python3 -m unittest test_audioshrink`.

### Default Values
`--jobs 2` · `--comp 6` · `--reencode-min-bitrate 320` · Re-encode / Cover-dedup /
Cleanup each on.

## Development (0.x)

- **0.1** Skeleton: FLAC/WAV/AIFF → Opus, mirror structure.
- **0.2** Incremental processing (skip by mtime/size), `--force`, `--debug`.
- **0.3** Intelligent bitrate (ffprobe: sample-rate/genre/source bitrate).
- **0.4** Mirror cleanup (cleanup, `--no-cleanup`, `--dry-run`).
- **0.5** Album cover deduplication (FLAC) + version display. — 0.5.1: atomic writes.
- **0.6/0.7** Cover optimization (ImageMagick) + lossy re-encode (ffmpeg decoder → opusenc).
  — 0.8.1: cleanup removes obsolete non-Opus targets.
- **0.8** Parallelization (ThreadPool, `--jobs`).
- **0.9.1** Test suite (stdlib unittest).
- **0.9.2** Re-encode policy (bitrate threshold, genre-independent) + hybrid cleanup.
- **0.9.3** Robust cover extraction (copy image stream instead of re-encoding).
- **0.9.6** Cover deduplication also for re-encoded lossy albums.
- **0.9.7** Cleanup is threshold-aware (removes orphaned `.opus` with raised threshold).
- Defaults finally set to `--comp 6`, `--reencode-min-bitrate 320`, `--jobs 2`.
