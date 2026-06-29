# AudioShrink – Roadmap 0.1 → 1.0

Incremental implementation of the program described in [CONCEPT.md](CONCEPT.md).
Core idea: **Each version is runnable and end-to-end usable** – functionality
is stacked on a working skeleton from the start.

## Commitments

- **Language:** Python 3
- **Minimum version:** **Python 3.8** (covers real NAS systems; no 3.9-only features like `str.removesuffix`)
- **Encoder:** exclusively `opusenc`; `ffmpeg` never for encoding (lossy decoder only)
- **0.1:** fixed bitrate 128 kbps (intelligent bitrate from 0.3 onward)
- **Parallelization:** from 0.8 onward – including measurement of real time savings
- **Dependencies:** Exclusively standard library up to v1.1. From v1.2 onward, `mutagen` is required to eliminate process-spawning bottlenecks for metadata reading.

## Status

| Version | Topic | Status |
|---------|-------|--------|
| 0.1 | Skeleton (Walking Skeleton) | ✅ done |
| 0.2 | Incremental & robust | ✅ done |
| 0.3 | Intelligent bitrate | ✅ done |
| 0.4 | Mirror cleanup (Cleanup) | ✅ done |
| 0.5 | Album cover deduplication | ✅ done |
| 0.6 | Cover optimization & removal | ✅ done (with 0.7) |
| 0.7 | Lossy re-encode (optional) | ✅ done (with 0.6) |
| 0.8 | Parallelization & performance | ✅ done (default: 4 jobs) |
| 0.91 | Tests (stdlib unittest suite) | ✅ done |
| 0.92 | Re-encode policy (bitrate threshold) + hybrid cleanup | ✅ done |
| 0.95 | README/docs & hardening | ✅ done |
| 1.0 | Release | ✅ done |

(The "target whitelist" considered in 0.9x was rejected: the strict mirror is
intended – companion files remain as long as they exist in the source; only
source-less targets are removed.)

---

## 0.1 — Skeleton (Walking Skeleton)
**Status:** ✅ done
**Focus:** Complete pass-through, minimal.
**New:** CLI (`SOURCE TARGET`), dependency check (opusenc), recursive traversal,
mirror structure, FLAC/WAV/AIFF → Opus (fixed bitrate 128, `--vbr --music`),
everything else copy, simple logging.
**Result:** A FLAC collection is actually compressed.
**Not yet:** skip logic, bitrate intelligence, cleanup, covers, ffprobe.

## 0.2 — Incremental & Robust
**Status:** ✅ done
**Focus:** Repeatable & fault-tolerant.
**New:** Freshness check (mtime/size) → skip, `--force`, `try/except` per file,
error counter, exit codes (0/1/2), `--debug`.
**Result:** Second run is fast; one broken track doesn't break everything.

## 0.3 — Intelligent Bitrate
**Status:** ✅ done
**Focus:** Quality/size per file.
**New:** `ffprobe` analysis (sample rate, bitrate, genre, codec), sample-rate rule
(160/128/96), source bitrate cap, `--speech`/`--music` by genre.
**Result:** Each file gets the right bitrate. **Deliberately no** track-length reduction.

## 0.4 — Mirror Cleanup (Cleanup)
**Status:** ✅ done
**Focus:** True mirror including deletions.
**New:** `cleanup_target` (orphaned files/folders), default **on**, `--no-cleanup`,
`--dry-run`, Opus → source extension mapping.
**Result:** Deleted sources also disappear in target; `--dry-run` shows it safely.

## 0.5 — Album Cover Deduplication
**Status:** ✅ done
**Focus:** Album-level processing.
**New:** Switch to **folder-by-folder** processing, `plan_album_cover`/
`finalize_album_cover`, hash comparison "all tracks same cover?", `metaflac` extraction,
`--discard-pictures`, one `cover.jpg` per album, `--no-cover-dedup`, cover protection in cleanup.
**Result:** Covers only once per album instead of in every file – noticeable space savings.

## 0.6 — Cover Optimization & Removal
**Status:** ✅ done
**Focus:** Control image size.
**New:** `--cover-max-size` (ImageMagick `magick`/`convert`, `-resize 'NxN>' -strip -quality`),
`COVER_QUALITY`, `--strip-covers`.
**Result:** Covers are resized or removed entirely.

## 0.7 — Lossy Re-Encode (Optional)
**Status:** ✅ done
**Focus:** The most complex path.
**New:** `--reencode-lossy`, `reencode_is_sensible`, ffmpeg-**decoder** → pipe → opusenc,
tag/cover rebuild via ffprobe → opusenc options.
**Result:** Large MP3/AAC can also be compressed – lossless metadata, ffmpeg as decoder only.

## 0.8 — Parallelization & Performance
**Status:** ✅ done
**Focus:** Suitable for 300–500 GB.
**New:** Encoding across multiple CPU cores (worker pool), `--jobs N` (**default 4**,
suitable for DS718plus), ordered log output, progress reporting. **Measurement** of
whether parallelization actually saves time.
**Result:** Large collections in reasonable time.

## 0.91 — Tests (Stdlib unittest Suite)
**Status:** ✅ done
**Focus:** Secure logic without extra packages.
**New:** `test_audioshrink.py` with `unittest` (no pytest → runs via
`python3 -m unittest` everywhere, even on DS718plus). Covers pure logic functions:
`determine_bitrate`, `is_speech`, `reencode_is_sensible`, `build_metadata_opts`,
`is_up_to_date`, `source_has_counterpart` (including re-encode cases), and
`plan_album_cover` (with mocked external tools).
**Result:** Regressions detected early.

## 0.92 — Re-Encode Policy (Bitrate Threshold) + Hybrid Cleanup
**Status:** ✅ done
**Focus:** Consistent treatment of lossy sources, incremental cleanup.
**New:**
- Re-encode of lossy sources now **default**; disable with `--no-reencode-lossy`.
  Decision **by source bitrate only** (not genre): re-encode if bitrate > threshold
  (`--reencode-min-bitrate`, default 192), otherwise copy. **Target bitrate/tuning**
  remains genre-aware (speech → 64 kbps, `--speech`). Same decision used in run and cleanup.
- If ffmpeg missing, re-encode disabled with warning (sources are copied).
- **Hybrid cleanup:** orphaned files per folder immediately, orphaned directories +
  empty folders at end. Interrupted runs leave completed albums consistent.
**Result:** No format churn with consistent use; audiobooks (≤192) copied fast instead of
slow re-encoding.

## 0.95 — README/Docs & Hardening
**Status:** ✅ done
**Focus:** Usability & documentation.
**Done:** README.md (user guide with all options/defaults), CONCEPT.md streamlined
to lean design/architecture doc (no version-synced pseudocode; code is source of truth).
Hardening mostly via real NAS bugfixes (0.9.x).
**Open/optional:** `--ext opus/ogg` (if needed).
**Result:** Documented and production-tested.

## 1.0 — Release 
**Status:** ✅ done
**Focus:** Stable & distributable.
**Done:** Stable CLI with `--version`, [CHANGELOG.md](CHANGELOG.md) from 0.x history,
final review (compiles cleanly, tests pass), git tag `v1.0.0`. Distribution:
`audioshrink.py` (+ optional `fix_covers.py`) into a PATH directory or copy to
`/volume1/public/` – no installation/dependencies beyond standard library.
**Result:** Production-ready.

## 1.1 — Quick Wins & Hardware Detection
**Status:** ✅ done
**Focus:** Fixes and dynamic resource allocation.
- Bitrate threshold (`--reencode-min-bitrate`) now applies inclusively (e.g., `>= 320 kbps`).
- `--jobs` defaults to all available CPU cores minus 1 (`os.cpu_count() - 1`) to maximize utilization without freezing the system.
**Result:** 320 kbps MP3s are reliably shrunk; out-of-the-box performance improvement on multi-core systems.

## 1.2 — Performance Leap
**Status:** ✅ done
**Focus:** Eliminating process startup overhead for large libraries.
**New:** Replaced `ffprobe` with the native Python library `mutagen` for reading metadata. Requires a one-time `pip install mutagen` (best done via a virtual environment).
**Result:** Massive time savings for libraries with >10,000 tracks by avoiding external process spawning per file.

## 1.3 — Incremental Update
**Status:** ✅ done
**Focus:** Incrementally update the library if paramaters (comp, bitrate) changed.
**New:** --max-time MIN limits the time the program runs. --update only reencodes files which do not match the given comp and bitrate paramaters
**Result:** Large libararies can incrementally updated.

## 1.3.x — Bugfixes & Testbasis
**Status:** ✅ done
**Focus:** Korrektheit und stabile Testbasis
- **Classical-Bitrate-Bug:** `min()` → `max()` in `determine_bitrate` für Klassik-Genres.
  `CLASSICAL_BITRATE = 112` war toter Code, da `min(96, 112) = 96` — Klassik bekam nie 112 kbps.
- **Truncated Test-File:** `test_audioshrink.py` endete mitten in einer Zeile (Syntax-Error).
  Datei kompletiert, veraltete samplerate-basierte Bitrate-Tests durch aktuelle Genre-Tests ersetzt,
  Schwellenwert-Tests für inklusive `>=`-Semantik korrigiert, `TestIsClassical` ergänzt.
- **`requirements.txt`** angelegt (`mutagen>=1.45`).
- **Doppelter Docstring** in `analyze_audio` entfernt.

---

## 1.4 — Sichtbarkeit & Qualität
**Status:** 🔲 planned
**Focus:** Auswertbarkeit im Log (primärer Betrieb via Cron/NAS, kein interaktiver Zuschauer).
- **Größenstatistik:** Gesamteinsparung (Quell- vs. Zielgröße in MB/GB, Prozent) im
  abschließenden Summary-Log — nützlich zum Nachlesen nach dem Cron-Lauf.
- **WebP-Cover-Fix:** `_write_tmp_image()` erkennt WebP-Magicbytes (`RIFF…WEBP`)
  und schreibt korrekte `.webp`-Extension statt `.jpg` (Known Issue aus CONCEPT.md §8).

## 1.5 — Robustheit
**Status:** 🔲 planned
**Focus:** Weniger externe Abhängigkeiten, saubererer State.
- **METADATA_CACHE als lokale Variable:** Globaler Modul-State verhindert korrekten
  Betrieb bei wiederholtem Aufruf im selben Prozess (z. B. in Tests oder als Modul
  importiert). Cache-Scope auf `run()`-Aufruf begrenzen.

## 1.6 — Usability (optional)
**Status:** 🔲 planned
**Focus:** Operativer Komfort für NAS-Betrieb.
- **`--stats`-Flag:** Übersicht der Quell-Formate (Anzahl FLAC/MP3/AAC/…) ohne
  Konvertierung — nützlich vor dem ersten Lauf.
- **`--log-file PATH`:** Ausgabe zusätzlich in Datei schreiben für
  unbeaufsichtigte Läufe.
