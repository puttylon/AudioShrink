# AudioShrink

Compresses a large, mixed music collection into a compact, uniformly
formatted **Opus** mirror copy – ideal for smartphone, tablet, or player.

- Lossless sources (FLAC, WAV, AIFF) → transcoded to Opus.
- Lossy sources (MP3, AAC, …) → copied; only high-bitrate ones are
  re-encoded (threshold adjustable).
- The directory structure is **mirrored exactly**; the run is **repeatable**
  (only new/changed files are processed).

Design and background: see [CONCEPT.md](CONCEPT.md).

---

## Requirements

External tools must be in `PATH` (installation is system-dependent):

| Tool | Required? | Purpose |
|---|---|---|
| `opusenc` (opus-tools) | **yes** | Only encoder (everything → Opus) |
| `ffmpeg`               | only with `--reencode-lossy` (default on) | Decodes lossy sources + extracts covers. If missing, re-encode is disabled with a warning. |
| `metaflac` (flac)      | optional | Cover extraction from FLAC for deduplication |
| ImageMagick (`magick`/`convert`) | only with `--cover-max-size` | Resize covers |

**Python Requirements:**
Python **3.8+** and the `mutagen` library for fast metadata parsing.

## Installation (NAS / Homebrew)

Modern Python environments restrict global pip installations (PEP 668). The cleanest way is to install the external binaries via Homebrew, and use a virtual environment for the Python package:

```sh
# 1. Install required system dependencies via Homebrew
brew install opus-tools ffmpeg flac imagemagick

# 2. Create a virtual environment (e.g., in your home directory)
python3 -m venv ~/audioshrink-env

# 3. Install the required Python package inside the environment
~/audioshrink-env/bin/pip install mutagen

---

## Usage

```sh
~/audioshrink-env/bin/python audioshrink.py SOURCE TARGET [Options]
```

Example (corresponds to current defaults):
```sh
~/audioshrink-env/bin/python audioshrink.py /volume1/music/musik /volume1/music/musik_ogg
# = --jobs max_cores-1 --comp 6 --reencode-min-bitrate 320
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--jobs N` | `2` | Parallel conversions (leave cores for other NAS tasks). |
| `--comp 0..10` | `6` | opusenc complexity (10 = best/slowest, lower = faster). |
| `--reencode-min-bitrate KBPS` | `320` | Lossy sources **above** this bitrate are re-encoded; below are copied. |
| `--no-reencode-lossy` | – | Always copy lossy sources (no re-encode). |
| `--no-cover-dedup` | – | Do **not** deduplicate album covers (embedded covers remain per file). |
| `--cover-max-size PX` | – | Resize covers to max dimension (ImageMagick). |
| `--strip-covers` | – | Remove covers/images completely. |
| `--no-cleanup` | – | Do **not** delete orphaned target files/folders. |
| `--dry-run` | – | Show all actions (including deletions) without executing. |
| `--force` | – | Reprocess everything (ignore freshness check). |
| `--debug` | – | Verbose output (shows skipped files, etc.). |
| `--version` | – | Print version. |

---

## Decision Logic

**Per file:**

| Source | Action |
|---|---|
| FLAC / WAV / AIFF | → Transcode to Opus |
| MP3 / AAC / M4A / WMA / OGG / Opus | Bitrate **> threshold** → Re-encode to Opus; otherwise **copy** |
| Images (jpg/png/…) | Copy; optionally resize (`--cover-max-size`) or remove (`--strip-covers`) |
| Everything else | Copy |

**Target bitrate (when encoding):** derived from sample rate – ≥ 96 kHz → 160, ≥ 48 kHz → 128,
otherwise 96 kbps; for speech genres (audiobook/podcast/…) → **64 kbps + `--speech`**.
Never higher than the source bitrate.

> Important: The **re-encode decision** depends **only on bitrate** (not genre).
> A 320k audiobook will thus be re-encoded (to 64 kbps Opus), a 128k one copied.

**Album cover deduplication (default on):** If all transcoded tracks in a folder have the same 
embedded cover (or a `cover.jpg`/`folder.jpg` exists), a single `cover.jpg`/`.png` is placed 
in the folder and the image removed from the Opus files. Different covers remain per track.

**Cleanup (mirroring, default on):** Anything in the target without a source counterpart is removed 
– audio files in the wrong format (e.g. a `.opus` that would now be copied as `.mp3`) and files/folders 
with no source. Existing companion files (`.lrc`, covers, …) remain as long as they exist in the source. 
Cleanup is **hybrid**: orphaned files removed immediately after each folder, orphaned directories + 
empty folders at the end. An interrupted run leaves completed albums consistent.

**Incremental:** Current targets (same mtime, for copies also size) are skipped. Runs are thus 
**resumable** – after a break, just run again.
`--force` forces reprocessing.

---

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success, no errors |
| 1 | Startup/configuration error (parameters, missing dependency, source) |
| 2 | Run completed, but some files had errors |

---

## Helper Script: fix_covers.py

Finds album folders in the target that contain `.opus` files but **have no** cover at all
(neither `cover.jpg` nor embedded – legacy from earlier versions), and regenerates them 
with `audioshrink --force`. It derives the source folder from the target path (removes 
`_ogg` suffix from root, e.g. `…/musik_ogg` → `…/musik`).

```sh
./fix_covers.py /volume1/music/musik_ogg            # list only (safe)
./fix_covers.py /volume1/music/musik_ogg --apply    # actually regenerate
./fix_covers.py /volume1/music/musik_ogg --source-root /path/to/source --apply
```

Both scripts must be in the same directory (`fix_covers` uses `audioshrink` as a module).

---

## Notes

- **NAS:** tested on Synology DS718plus (Python 3.8). `--jobs 2` leaves cores free;
  for an uninterrupted run, `--jobs 4` can be faster.
- **Check safely first:** `--dry-run` shows completely what would be copied/converted
  **and deleted**, without touching anything.
- **Permissions:** The program runs as the executing user; if existing target files
  are owned by another user, overwriting will fail (logged per file, run continues).
