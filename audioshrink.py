#!/usr/bin/env python3
"""AudioShrink – compresses music collections to Opus.

Version 1.3.2:
  - Optimized multithreading with album lookahead
  - Minor optimization

Version 1.3.1:
  - Fixed bugs that occur when writing destination files fail

Version 1.3.0:
  - Mirrors directory structure from SOURCE to TARGET (folder by folder).
  - FLAC/WAV/AIFF are transcoded to Opus; bitrate is determined per file
    from sample rate, genre, and source bitrate (via ffprobe).
  - Album cover deduplication: shared embedded cover → once as cover.jpg/png,
    removed from Opus files. Disable with --no-cover-dedup.
  - --cover-max-size resizes covers (ImageMagick); --strip-covers removes
    covers/images completely.
  - Lossy sources (MP3/AAC/...) are re-encoded to Opus by default if their
    bitrate exceeds a threshold (--reencode-min-bitrate, default 320 kbps);
    below that they are copied. Disable with --no-reencode-lossy. Decision
    depends ONLY on source bitrate (not genre); target bitrate/tuning still
    considers genre (speech → 64 kbps, --speech). ffmpeg decodes, opusenc encodes.
  - Current targets are skipped (mtime/size); --force forces reprocessing.
  - Orphaned targets (without source) are removed; disable with --no-cleanup.
    Cleanup is hybrid: orphaned files immediately after each folder,
    orphaned directories + empty folders at end.
  - --dry-run shows all actions (including deletions) without executing.
  - Conversions run in parallel (--jobs N, default 2); log output remains
    per-album in order.
  - Per-file errors do not break the run.
  - --max-time MIN to stop after approximately MIN minutes, but 
    only after the current album has finished
  - --update re-encode targets if target bitrate or comp differ from current
    settings. Works great together with --max-time to incrementally update
    the audio libary to newer parameters (bitrate, --comp)

Not yet included (see ROADMAP.md): additional features.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import logging
import mutagen
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "1.3.2"
# determine cores, subtract 1, but is at least 1 job. (os.cpu_count() maybe is None)
DEFAULT_JOBS = max(1, (os.cpu_count() or 2) - 1)
DEFAULT_COMP = 6    # opusenc complexity 0..10 (10=best/slowest); lower=faster
DEFAULT_REENCODE_MIN_BITRATE = 320   # kbps; lossy sources ABOVE this are re-encoded

# --- Configuration -----------------------------------------------------------
LOSSLESS_FORMATS = {"flac", "wav", "aiff", "aif"}   # opusenc reads these natively
LOSSY_FORMATS = {"mp3", "opus", "ogg", "aac", "m4a", "wma"}
IMAGE_FORMATS = {"jpg", "jpeg", "png", "gif"}
IGNORE_DIRS = {"@eaDir"}                             # System/cache folders
TARGET_EXT = "opus"
MTIME_TOLERANCE = 1                                  # s; against filesystem rounding
COVER_FILENAMES = {"cover.jpg", "cover.png", "folder.jpg", "folder.png"}
COVER_QUALITY = 85                                   # JPEG quality when resizing

# A target .opus can be transcoded from a lossless source OR copied from
# an existing .opus source (or with --reencode-lossy also from other lossy sources).
OPUS_SOURCE_CANDIDATES = LOSSLESS_FORMATS | {TARGET_EXT}

# Bitrate selection
SPEECH_GENRES = {"hörbuch", "audiobook", "speech", "podcast", "spoken", "hörspiel"}
SPEECH_BITRATE = 64

DRY_VERB = {
    "transcode": "would convert",
    "transcode_lossy": "would re-encode",
    "cover_resize": "would resize cover",
    "copy": "would copy",
}

log = logging.getLogger("audioshrink")


# --- Helper Functions --------------------------------------------------------
def check_dependencies() -> bool:
    ok = True
    for tool in ["opusenc"]:
        if shutil.which(tool) is None:
            log.error("Required dependency missing: %s", tool)
            ok = False
    return ok    


@functools.lru_cache(maxsize=None)
def im_cmd():
    """Prefer ImageMagick 7 (magick), else 6 (convert); None if neither."""
    for candidate in ("magick", "convert"):
        if shutil.which(candidate):
            return candidate
    return None


def ext_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def is_lossless(path: Path) -> bool:
    return ext_of(path) in LOSSLESS_FORMATS


def walk_by_directory(source: Path, target: Path):
    """Yields (folder, [files]) per source folder; ignores hidden and
    excluded folders, and the (potentially embedded) target directory."""
    for root, dirs, files in os.walk(source):
        root_path = Path(root)
        dirs[:] = sorted(
            d for d in dirs
            if d not in IGNORE_DIRS
            and not d.startswith(".")
            and (root_path / d).resolve() != target
        )
        kept = [root_path / n for n in sorted(files) if not n.startswith(".")]
        if kept:
            yield root_path, kept


def is_up_to_date(src: Path, dst: Path, compare_size: bool) -> bool:
    """True if target can be considered current and skipped."""
    if not dst.exists():
        return False
    try:
        s = src.stat()
        d = dst.stat()
    except OSError:
        return False  # When in doubt, process
    if s.st_mtime > d.st_mtime + MTIME_TOLERANCE:   # Source significantly newer
        return False
    if compare_size and s.st_size != d.st_size:     # Pure copy: also check size
        return False
    return True


def safe_unlink(path: Path) -> None:
    """Remove an (incomplete) target file. Swallows all errors
    so cleanup logic itself never breaks the run."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove incomplete file: %s (%s)", path, exc)


def copy_source_mtime(src: Path, dst: Path) -> None:
    """Copy source mtime to target, so skip logic works consistently
    in later runs (opusenc otherwise sets encode time)."""
    try:
        st = src.stat()
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError as exc:
        log.warning("Could not set mtime: %s (%s)", dst, exc)


# --- Audio Analysis & Bitrate Selection -----------------------------------
# Create a simple cache dictionary in memory
METADATA_CACHE = {}
def analyze_audio(path: Path) -> dict:
    """Read tags/info with caching to avoid redundant disk access."""
    # Check if we already processed this file in this run
    if path in METADATA_CACHE:
        return METADATA_CACHE[path]

    """Read sample rate, source bitrate, and tags via mutagen directly in Python.
    On error, return defaults so conversion still proceeds."""
    info = {"sample_rate": 0, "bitrate_kbps": 0, "genre": "", "tags": {}}
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return info

        # Audio-Eigenschaften (Abtastrate und Bitrate)
        if hasattr(audio, 'info'):
            if getattr(audio.info, 'sample_rate', None):
                info["sample_rate"] = int(audio.info.sample_rate)
            if getattr(audio.info, 'bitrate', None) and audio.info.bitrate > 0:
                info["bitrate_kbps"] = int(audio.info.bitrate) // 1000

        # Metadaten (Tags) auslesen und vereinheitlichen
        tags = {}
        if getattr(audio, 'tags', None):
            for key, val in audio.tags.items():
                # mutagen liefert Werte oft als Listen (z.B. ['Rock'])
                if isinstance(val, list) and len(val) > 0:
                    str_val = str(val[0])
                else:
                    str_val = str(val)
                
                clean_key = key.lower()
                
                # Fallback für M4A/AAC, falls easy=True Apple-Tags nicht umwandelt
                if clean_key == '©gen': clean_key = 'genre'
                elif clean_key == '©nam': clean_key = 'title'
                elif clean_key == '©art': clean_key = 'artist'
                elif clean_key == '©alb': clean_key = 'album'
                elif clean_key == '©day': clean_key = 'date'
                elif clean_key == 'trkn': clean_key = 'tracknumber'
                
                tags[clean_key] = str_val

        info["tags"] = tags
        info["genre"] = tags.get("genre", "")

    except Exception as exc:
        log.warning("mutagen analysis failed: %s (%s)", path, exc)

    # Save the result in the cache before returning it
    METADATA_CACHE[path] = info
    return info

def is_speech(genre: str) -> bool:
    g = (genre or "").lower()
    return any(term in g for term in SPEECH_GENRES)


def determine_bitrate(info: dict) -> int:
    """Bitrate from sample rate (base), genre, and source bitrate (cap)."""
    sr = info.get("sample_rate", 0)
    if sr >= 96000:
        bitrate = 160
    elif sr >= 48000:
        bitrate = 128
    else:
        bitrate = 96  # e.g. 44.1 kHz CD material

    if is_speech(info.get("genre", "")):
        bitrate = min(bitrate, SPEECH_BITRATE)

    src_br = info.get("bitrate_kbps", 0)
    if src_br > 0:                       # never higher than source
        bitrate = min(bitrate, src_br)

    return bitrate


def opus_tuning(info: dict) -> str:
    return "--speech" if is_speech(info.get("genre", "")) else "--music"


def should_reencode(info: dict, min_bitrate: int = DEFAULT_REENCODE_MIN_BITRATE) -> bool:
    """Whether a lossy source should be re-encoded – decision based ONLY on
    source bitrate (not genre): equal or above threshold and actually shrinking."""
    src_br = info.get("bitrate_kbps", 0)
    return src_br >= min_bitrate and src_br > determine_bitrate(info)


def build_metadata_opts(info: dict) -> list:
    """opusenc metadata options from source tags (for lossy re-encode,
    since ffmpeg loses tags when decoding to WAV)."""
    tags = info.get("tags", {})
    opts = []
    used = set()
    for key, opt in (("title", "--title"), ("artist", "--artist"),
                     ("album", "--album"), ("genre", "--genre"), ("date", "--date")):
        val = tags.get(key)
        if val:
            opts += [opt, str(val)]
            used.add(key)
    track = tags.get("tracknumber") or tags.get("track")
    if track:
        opts += ["--tracknumber", str(track)]
        used.update({"tracknumber", "track"})
    for k, v in tags.items():
        if k in used or not v:
            continue
        opts += ["--comment", "%s=%s" % (k, v)]
    return opts


# --- Album Cover Deduplication -------------------------------------------
def embedded_cover_bytes(flac_path: Path):
    """Export embedded cover from FLAC file (or None)."""
    cmd = ["metaflac", "--export-picture-to=-", str(flac_path)]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _ffmpeg_cover_bytes(path: Path):
    """Extract embedded cover via ffmpeg UNCHANGED → bytes (or None)."""
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-an",
             "-map", "0:v:0", "-c", "copy", "-f", "image2pipe", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    return res.stdout if (res.returncode == 0 and res.stdout) else None


def embedded_cover_data(path: Path):
    """Embedded cover as bytes: FLAC via metaflac, else via ffmpeg."""
    if ext_of(path) == "flac":
        return embedded_cover_bytes(path)
    return _ffmpeg_cover_bytes(path)


def _will_transcode(f: Path, reencode_lossy: bool, reencode_min_bitrate: int) -> bool:
    """Will this file be transcoded to Opus? (lossless always; lossy only if
    re-encoded – decision by bitrate)."""
    if is_lossless(f):
        return True
    if ext_of(f) in LOSSY_FORMATS and reencode_lossy:
        return should_reencode(analyze_audio(f), reencode_min_bitrate)
    return False


def plan_album_cover(files: list, reencode_lossy: bool,
                     reencode_min_bitrate: int) -> dict:
    """Decide whether to deduplicate a shared cover for this album.
    Consider all TRANSCODED tracks (FLAC + re-encoded lossy like MP3).
    Return: {discard_embedded: bool, write_cover: bytes|None}."""
    plan = {"discard_embedded": False, "write_cover": None}

    transcoded = [f for f in files
                  if _will_transcode(f, reencode_lossy, reencode_min_bitrate)]
    if not transcoded:
        return plan

    # (a) Is there already a separate cover file in the source? Then it suffices
    #     to discard the embedded cover – the file is copied normally.
    if any(f.name.lower() in COVER_FILENAMES for f in files):
        plan["discard_embedded"] = True
        return plan

    # (b) Do all transcoded tracks have the same embedded cover?
    first_bytes = None
    digests = set()
    for f in transcoded:
        data = embedded_cover_data(f)
        if data is None:
            return plan  # At least one track without cover → don't deduplicate
        if first_bytes is None:
            first_bytes = data
        digests.add(hashlib.md5(data).digest())

    if len(digests) == 1:
        plan["discard_embedded"] = True
        plan["write_cover"] = first_bytes
    return plan


def write_cover_file(dest: Path, data: bytes, cover_max_size) -> bool:
    """Write cover bytes to dest atomically, optionally resized via ImageMagick."""
    # Generate a unique temporary target
    temp_dest = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
    
    try:
        if cover_max_size and im_cmd():
            cmd = [
                im_cmd(), "-",
                "-resize", "%dx%d>" % (cover_max_size, cover_max_size),
                "-strip", "-quality", str(COVER_QUALITY),
                str(temp_dest),
            ]
            res = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
            if res.returncode != 0:
                log.error("Album cover could not be resized: %s", dest)
                safe_unlink(temp_dest)
                return False
        else:
            # Write bytes directly to the temp file
            temp_dest.write_bytes(data)
            
        # Atomically replace the destination only on success
        temp_dest.replace(dest)
        return True
        
    except OSError as exc:
        log.error("Album cover operation failed: %s (%s)", dest, exc)
        safe_unlink(temp_dest)
        return False

def finalize_album_cover(src_dir: Path, source: Path, target: Path,
                         plan: dict, dry_run: bool, cover_max_size) -> int:
    """Place extracted album cover as a single file. Return 1 if a cover
    was written (or reported in dry-run)."""
    data = plan.get("write_cover")
    if not data:
        return 0
    ext = "png" if data.startswith(b"\x89PNG") else "jpg"
    rel = src_dir.relative_to(source)
    relname = rel / ("cover." + ext)
    cover_dest = target / relname
    if dry_run:
        log.info("[DRY-RUN] would create album cover: %s", relname)
        return 1
    try:
        cover_dest.parent.mkdir(parents=True, exist_ok=True)
        if not write_cover_file(cover_dest, data, cover_max_size):
            return 0
        log.info("Album cover created: %s", relname)
        return 1
    except OSError as exc:
        log.error("Album cover could not be created: %s (%s)", cover_dest, exc)
        return 0


# --- Process Individual Files ------------------------------------------------
def _opusenc_base(bitrate: int, tuning: str, discard_pictures: bool, comp: int) -> list:
    cmd = [
        "opusenc",
        "--bitrate", str(bitrate),
        "--vbr",
        tuning,                 # --music or --speech
        "--comp", str(comp),
        "--framesize", "20",
        "--quiet",
    ]
    if discard_pictures:
        cmd.append("--discard-pictures")
    return cmd


def transcode(src: Path, dst: Path, bitrate: int, tuning: str,
              discard_pictures: bool, comp: int):
    """Transcode lossless source (FLAC/WAV/AIFF) directly to Opus with opusenc.
    Return: (ok, error_message|None)."""
    tmp_dst = dst.with_name(dst.name + ".tmp")
    cmd = _opusenc_base(bitrate, tuning, discard_pictures, comp) + [str(src), str(tmp_dst)]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
    except OSError as exc:
        safe_unlink(tmp_dst)
        return False, "Conversion failed: %s (%s)" % (src, exc)
    if result.returncode != 0:
        safe_unlink(tmp_dst)
        return False, "Conversion failed: %s\n%s" % (src, result.stderr.strip())
    try:
        os.replace(tmp_dst, dst)  # atomic swap
    except OSError as exc:
        safe_unlink(tmp_dst)
        return False, "Target file could not be renamed: %s (%s)" % (dst, exc)
    copy_source_mtime(src, dst)
    return True, None


def _write_tmp_image(data: bytes):
    """Write image bytes to temp file with appropriate extension."""
    if data.startswith(b"\x89PNG"):
        ext = "png"
    elif data[:3] == b"GIF":
        ext = "gif"
    else:
        ext = "jpg"
    fd, name = tempfile.mkstemp(suffix="." + ext)
    os.write(fd, data)
    os.close(fd)
    return Path(name)


def extract_cover_ffmpeg(src: Path):
    """Extract embedded cover from a lossy source to a temp file (or None).
    Caller must remove the path.

    Prefer copying image stream UNCHANGED (no encoder needed, no quality loss).
    Only if that fails, re-encode to JPEG."""
    # 1) Copy image stream 1:1 (robust against minimal ffmpeg builds)
    data = _ffmpeg_cover_bytes(src)
    if data:
        return _write_tmp_image(data)

    # 2) Fallback: re-encode to JPEG (requires mjpeg encoder)
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src),
             "-an", "-frames:v", "1", str(tmp)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and tmp.stat().st_size > 0:
            return tmp
    except OSError:
        pass
    safe_unlink(tmp)
    return None


def transcode_lossy(src: Path, dst: Path, bitrate: int, tuning: str,
                    info: dict, discard_pictures: bool, comp: int):
    """Re-encode lossy source: ffmpeg decodes to WAV (pipe), opusenc encodes.
    Metadata and (if desired) covers are re-set.
    Return: (ok, error_message|None)."""
    tmp_dst = dst.with_name(dst.name + ".tmp")
    pic_tmp = None
    pic_opts = []
    if not discard_pictures:
        pic_tmp = extract_cover_ffmpeg(src)
        if pic_tmp:
            pic_opts = ["--picture", "3||||" + str(pic_tmp)]

    opus_cmd = (_opusenc_base(bitrate, tuning, discard_pictures, comp)
                + ["--ignorelength"] + build_metadata_opts(info) + pic_opts
                + ["-", str(tmp_dst)])
    ff_cmd = ["ffmpeg", "-v", "error", "-i", str(src), "-f", "wav", "-"]

    ok = False
    err = None
    try:
        ff = subprocess.Popen(ff_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            res = subprocess.run(opus_cmd, stdin=ff.stdout, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True)
        finally:
            ff.stdout.close()
            ff.wait()
        ok = res.returncode == 0 and ff.returncode == 0
        if not ok:
            err = "Re-encode failed: %s\n%s" % (src, res.stderr.strip())
    except OSError as exc:
        err = "Re-encode failed: %s (%s)" % (src, exc)
    finally:
        if pic_tmp:
            safe_unlink(pic_tmp)

    if not ok:
        safe_unlink(tmp_dst)
        return False, err
    try:
        os.replace(tmp_dst, dst)
    except OSError as exc:
        safe_unlink(tmp_dst)
        return False, "Target file could not be renamed: %s (%s)" % (dst, exc)
    copy_source_mtime(src, dst)
    return True, None

def cover_resize_file(src: Path, dst: Path, cover_max_size: int):
    """Copy a separate cover image resized (ImageMagick).
    Return: (ok, error_message|None)."""
    
    # Generate a unique temporary target in the same directory
    temp_dst = dst.with_name(f"{dst.name}.{uuid.uuid4().hex}.tmp")
    
    cmd = [
        im_cmd(), str(src),
        "-resize", "%dx%d>" % (cover_max_size, cover_max_size),
        "-strip", "-quality", str(COVER_QUALITY),
        str(temp_dst),  # Direct ImageMagick output to the temporary file
    ]
    
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True)
    except OSError as exc:
        # Clean up only the temporary file
        safe_unlink(temp_dst)
        return False, "Cover resize failed: %s (%s)" % (src, exc)
        
    if res.returncode != 0:
        # Clean up only the temporary file on ImageMagick error
        safe_unlink(temp_dst)
        return False, "Cover resize failed: %s\n%s" % (src, res.stderr.strip())
        
    # Apply mtime to the temp file BEFORE replacing the destination
    copy_source_mtime(src, temp_dst)
    
    # Atomically replace the original target
    temp_dst.replace(dst)
    
    return True, None

def copy(src: Path, dst: Path):
    """Return: (ok, error_message|None)."""
    temp_dst = dst.with_name(f"{dst.name}.{uuid.uuid4().hex}.tmp")
    
    try:
        shutil.copy2(src, temp_dst)
        temp_dst.replace(dst)
        return True, None
        
    except OSError as exc:
        safe_unlink(temp_dst)
        #return False, f"Copy failed: {src} ({exc})"
        return False, "Copy failed: %s (%s)" % (src, exc)

def needs_update(dst: Path, target_bitrate: int, target_comp: int) -> tuple[bool, str]:
    """Checks the target file to see if the bitrate or complexity has changed.
    Returns (True/False, reason)."""
    try:
        # Intentionally omitting easy=True to access the raw ENCODER_OPTIONS
        audio = mutagen.File(dst)
        if not audio or getattr(audio, 'tags', None) is None:
            return True, "No tags found"

        opts_list = audio.tags.get("ENCODER_OPTIONS") or audio.tags.get("encoder_options")
        if not opts_list:
            return True, "No ENCODER_OPTIONS found"

        opts_str = str(opts_list[0])

        m_comp = re.search(r'--comp\s+(\d+)', opts_str)
        m_bitrate = re.search(r'--bitrate\s+(\d+)', opts_str)

        dst_comp = int(m_comp.group(1)) if m_comp else -1
        dst_bitrate = int(m_bitrate.group(1)) if m_bitrate else -1

        if dst_comp != target_comp or dst_bitrate != target_bitrate:
            return True, "comp %d->%d, br %d->%d" % (dst_comp, target_comp, dst_bitrate, target_bitrate)

    except Exception:
        return True, "Failed to read metadata"

    return False, ""

# --- Main Processing --------------------------------------------------------
def process_one(src: Path, source: Path, target: Path, plan: dict, *,
                force: bool, update: bool, dry_run: bool, reencode_lossy: bool,
                reencode_min_bitrate: int, comp: int,
                strip_covers: bool, cover_max_size):
    """Process a single file (in worker thread). Does not log directly,
    but returns (status, [(level, message), ...]) so output is ordered
    from main thread. status: converted|copied|skipped|ignored|error."""
    try:
        rel = src.relative_to(source)
        ext = ext_of(src)
        name_lower = src.name.lower()

        # Don't include images when --strip-covers
        if strip_covers and ext in IMAGE_FORMATS:
            return "ignored", [("debug", "Cover removed (not copied): %s" % rel)]

        # Determine action + target
        info = None
        if is_lossless(src):
            action = "transcode"
            dst = target / rel.with_suffix("." + TARGET_EXT)
        elif ext in LOSSY_FORMATS and reencode_lossy:
            info = analyze_audio(src)
            if should_reencode(info, reencode_min_bitrate):
                action = "transcode_lossy"
                dst = target / rel.with_suffix("." + TARGET_EXT)
            else:
                action = "copy"
                dst = target / rel
        elif cover_max_size and ext in IMAGE_FORMATS and name_lower in COVER_FILENAMES:
            action = "cover_resize"
            dst = target / rel
        else:
            action = "copy"
            dst = target / rel

        compare_size = action == "copy"
        is_current = not force and is_up_to_date(src, dst, compare_size)

        # NEW in 1.3.0: Update-Check
        update_reason = ""
        if is_current and update and action in ("transcode", "transcode_lossy"):
            if info is None:
                info = analyze_audio(src)
            target_bitrate = determine_bitrate(info)
            do_update, update_reason = needs_update(dst, target_bitrate, comp)
            if do_update:
                is_current = False

        if is_current:
            return "skipped", [("debug", "Skipped (current): %s" % rel)]                    

        if dry_run:
            status = "converted" if action in ("transcode", "transcode_lossy") else "copied"
            return status, [("info", "[DRY-RUN] %s: %s" % (DRY_VERB[action], rel))]

        dst.parent.mkdir(parents=True, exist_ok=True)
        if action == "transcode":
            info = analyze_audio(src)
            bitrate = determine_bitrate(info)
            ok, err = transcode(src, dst, bitrate, opus_tuning(info),
                                plan["discard_embedded"], comp)
            if ok:
                prefix = "Updated (%s):" % update_reason if update_reason else "Converted:"
                return "converted", [("info", "%s %s [%d kbps]" % (prefix, rel, bitrate))]
        elif action == "transcode_lossy":
            bitrate = determine_bitrate(info)
            ok, err = transcode_lossy(src, dst, bitrate, opus_tuning(info), info,
                                      plan["discard_embedded"], comp)
            if ok:
                prefix = "Updated (%s):" % update_reason if update_reason else "Re-encoded:"
                return "converted", [("info", "%s %s [%d kbps]" % (prefix, rel, bitrate))]
        elif action == "cover_resize":
            ok, err = cover_resize_file(src, dst, cover_max_size)
            if ok:
                return "copied", [("info", "Cover resized: %s" % rel)]
        else:  # copy
            ok, err = copy(src, dst)
            if ok:
                return "copied", [("info", "Copied: %s" % rel)]
        return "error", [("error", err or "Failed: %s" % rel)]
    except Exception as exc:  # Isolate per file – never break the run
        return "error", [("error", "Skipped (error): %s (%s)" % (src, exc))]


def _album_needs_work(files: list, source: Path, target: Path) -> bool:
    """True if at least one audio file still needs processing – via stat only
    (no ffprobe), just to avoid expensive cover planning."""
    for f in files:
        rel = f.relative_to(source)
        if is_lossless(f):
            if not is_up_to_date(f, target / rel.with_suffix("." + TARGET_EXT), False):
                return True
        elif ext_of(f) in LOSSY_FORMATS:
            # Lossy can be .opus (re-encoded) OR a copy
            opus_dst = target / rel.with_suffix("." + TARGET_EXT)
            copy_dst = target / rel
            if not (is_up_to_date(f, opus_dst, False) or is_up_to_date(f, copy_dst, True)):
                return True
    return False


def run(source: Path, target: Path, *, force: bool, update: bool, dry_run: bool,
        dedup_covers: bool, reencode_lossy: bool, reencode_min_bitrate: int,
        comp: int, strip_covers: bool, cover_max_size,
        jobs: int, cleanup: bool, max_time_minutes: int | None = None) -> int:
    counts = {"converted": 0, "copied": 0, "skipped": 0, "ignored": 0, "error": 0}
    covers = 0
    removed = 0
    start_time = time.monotonic()
    stop_requested = False

    # Alle Alben sammeln
    albums = list(walk_by_directory(source, target))
    if not albums:
        return 0

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        album_iter = iter(albums)

        # function to plan album and pool the tracks
        def launch_album(src_dir, files):
            if strip_covers:
                plan = {"discard_embedded": True, "write_cover": None}
            elif dedup_covers and (force or _album_needs_work(files, source, target)):
                plan = plan_album_cover(files, reencode_lossy, reencode_min_bitrate)
            else:
                plan = {"discard_embedded": False, "write_cover": None}

            futs = [
                pool.submit(process_one, src, source, target, plan,
                            force=force, update=update, dry_run=dry_run,
                            reencode_lossy=reencode_lossy,
                            reencode_min_bitrate=reencode_min_bitrate, comp=comp,
                            strip_covers=strip_covers, cover_max_size=cover_max_size)
                for src in files
            ]
            return src_dir, plan, futs

        in_flight = []

        # 2 albums to launch (lookahead), to avoid thread-starvation.
        # while running album 1, main-thread is preparing album 2.
        try:
            in_flight.append(launch_album(*next(album_iter)))
            in_flight.append(launch_album(*next(album_iter)))
        except StopIteration:
            pass

        while in_flight:
            src_dir, plan, track_futs = in_flight.pop(0)

            # wait for curren albums tracks
            for fut in track_futs:
                status, messages = fut.result()
                for level, msg in messages:
                    getattr(log, level)(msg)
                counts[status] += 1

            # finalize cover und cleanup for this album
            covers += finalize_album_cover(src_dir, source, target, plan, dry_run, cover_max_size)

            if cleanup:
                removed += cleanup_dir_files(
                    source, target, src_dir, dry_run=dry_run,
                    dedup_covers=dedup_covers, reencode_lossy=reencode_lossy,
                    reencode_min_bitrate=reencode_min_bitrate)

            # check max-time-limit (check only after album is completed)
            if max_time_minutes is not None and not stop_requested:
                elapsed = (time.monotonic() - start_time) / 60
                if elapsed >= max_time_minutes:
                    log.info(
                        "Maximum runtime reached (%.1f min >= %d min). "
                        "Stopping after current queued albums finish.",
                        elapsed, max_time_minutes
                    )
                    stop_requested = True

            # launch next album, if we should not stop
            if not stop_requested:
                try:
                    in_flight.append(launch_album(*next(album_iter)))
                except StopIteration:
                    pass

    if stop_requested:
        log.info("Run stopped due to --max-time limit")

    log.info(
        "Done | converted=%d copied=%d skipped=%d covers=%d errors=%d",
        counts["converted"], counts["copied"], counts["skipped"], covers, counts["error"],
    )

    if cleanup:
        removed += cleanup_dirs(source, target, dry_run=dry_run)
        verb = "would be removed" if dry_run else "removed"
        log.info("Cleanup completed | %d items %s", removed, verb)
    else:
        log.info("Cleanup skipped (--no-cleanup)")

    return 2 if counts["error"] else 0



# --- Cleanup -----------------------------------------------------------------
def source_has_counterpart(target_file: Path, source: Path, target: Path,
                           reencode_lossy: bool,
                           reencode_min_bitrate: int = DEFAULT_REENCODE_MIN_BITRATE) -> bool:
    """True if a matching source exists for the target file."""
    rel_parent = target_file.parent.relative_to(target)
    name = target_file.name
    ext = ext_of(target_file)
    if ext == TARGET_EXT:
        stem = name[: -(len(TARGET_EXT) + 1)]  # Remove extension by string (dot-safe)
        # Lossless source OR existing .opus source → .opus target always valid.
        for e in OPUS_SOURCE_CANDIDATES:
            if (source / rel_parent / (stem + "." + e)).exists():
                return True
        # Lossy source → valid only if it would actually be re-encoded
        # (bitrate > threshold); otherwise target is a copy, not .opus.
        if reencode_lossy:
            for e in LOSSY_FORMATS:
                cand = source / rel_parent / (stem + "." + e)
                if cand.exists() and should_reencode(analyze_audio(cand), reencode_min_bitrate):
                    return True
        return False

    src_file = source / rel_parent / name
    if not src_file.exists():
        return False
    # With --reencode-lossy, a lossy source may become .opus – then
    # the same-named non-Opus file in target is superseded (should be .opus).
    if (reencode_lossy and ext in LOSSY_FORMATS
            and should_reencode(analyze_audio(src_file), reencode_min_bitrate)):
        return False
    return True


def _remove(path: Path, dry_run: bool, recursive: bool = False) -> int:
    """Remove path. Return 1 if removed (or reported in dry-run)."""
    if dry_run:
        log.info("[DRY-RUN] would remove: %s", path)
        return 1
    try:
        if recursive:
            shutil.rmtree(path)
        elif path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        log.info("Removed: %s", path)
        return 1
    except OSError as exc:
        log.error("Could not remove: %s (%s)", path, exc)
        return 0


def cleanup_dir_files(source: Path, target: Path, src_dir: Path, *, dry_run: bool,
                      dedup_covers: bool, reencode_lossy: bool,
                      reencode_min_bitrate: int) -> int:
    """Remove orphaned FILES in target folder corresponding to src_dir (non-recursive).
    Runs immediately after folder completion."""
    rel = src_dir.relative_to(source)
    target_dir = target / rel
    if not target_dir.is_dir():
        return 0
    removed = 0
    for entry in sorted(target_dir.iterdir()):
        name = entry.name
        if name.startswith(".") or not entry.is_file():
            continue
        # Keep generated album cover (source folder exists right now)
        if dedup_covers and name.lower() in COVER_FILENAMES:
            continue
        if not source_has_counterpart(entry, source, target, reencode_lossy,
                                      reencode_min_bitrate):
            removed += _remove(entry, dry_run)
    return removed


def cleanup_dirs(source: Path, target: Path, *, dry_run: bool) -> int:
    """Final pass: remove orphaned DIRECTORIES (deleted albums) and
    then empty folders."""
    removed = 0

    # 1) Remove orphaned directories completely
    for root, dirs, _files in os.walk(target, topdown=True):
        root_path = Path(root)
        survivors = []
        for d in sorted(dirs):
            if d in IGNORE_DIRS or d.startswith("."):
                continue
            rel = (root_path / d).relative_to(target)
            if (source / rel).is_dir():
                survivors.append(d)
            else:
                removed += _remove(root_path / d, dry_run, recursive=True)
        dirs[:] = survivors  # don't traverse orphaned

    # 2) Remove empty directories (bottom-up)
    for root, _dirs, _files in os.walk(target, topdown=False):
        root_path = Path(root)
        if root_path == target:
            continue
        if any(p in IGNORE_DIRS for p in root_path.relative_to(target).parts):
            continue
        try:
            if not any(root_path.iterdir()):
                removed += _remove(root_path, dry_run)
        except OSError:
            pass

    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="audioshrink",
        description="Compresses a music collection to Opus (mirrored structure).",
    )
    parser.add_argument("source", help="Source directory")
    parser.add_argument("target", help="Target directory")
    parser.add_argument(
        "--no-cleanup", dest="cleanup", action="store_false",
        help="Do NOT delete orphaned target files/folders",
    )
    parser.add_argument(
        "--no-cover-dedup", dest="dedup_covers", action="store_false",
        help="Do NOT deduplicate album covers (embedded covers remain per file)",
    )
    parser.add_argument(
        "--no-reencode-lossy", dest="reencode_lossy", action="store_false",
        help="Do NOT re-encode lossy sources (always copy)",
    )
    parser.add_argument(
        "--reencode-min-bitrate", type=int, default=DEFAULT_REENCODE_MIN_BITRATE,
        metavar="KBPS",
        help="Re-encode lossy sources above this bitrate "
             "(default: %d)" % DEFAULT_REENCODE_MIN_BITRATE,
    )
    parser.add_argument(
        "--comp", type=int, default=DEFAULT_COMP, metavar="0..10",
        help="opusenc complexity 0..10 (10=best/slowest, lower=faster; "
             "default: %d)" % DEFAULT_COMP,
    )
    parser.add_argument(
        "--cover-max-size", type=int, default=None, metavar="PX",
        help="Resize covers to max dimension PX; requires ImageMagick",
    )
    parser.add_argument(
        "--strip-covers", action="store_true",
        help="Remove covers/images completely (no embedded covers, no image files)",
    )
    parser.add_argument(
        "--jobs", type=int, default=DEFAULT_JOBS, metavar="N",
        help="Number of parallel conversions (default: %d)" % DEFAULT_JOBS,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show all actions (including deletions) without executing",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess all files (ignore freshness check)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Verbose log output (shows skipped files, etc.)",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=None,
        metavar="MIN",
        help="Stop after approximately MIN minutes, but only after the current album has finished",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Re-encode targets if target bitrate or comp differ from current settings",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.info("AudioShrink %s (Python %s)", __version__, platform.python_version())

    source = Path(args.source).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()

    if not check_dependencies():
        return 1
    if not source.is_dir():
        log.error("Source directory not found: %s", source)
        return 1
    if source == target:
        log.error("Source and target must not be identical")
        return 1
    if args.reencode_lossy and shutil.which("ffmpeg") is None:
        log.warning("ffmpeg missing – lossy sources will be copied instead of re-encoded")
        args.reencode_lossy = False
    if args.cover_max_size and im_cmd() is None:
        log.error("ImageMagick (magick/convert) required for --cover-max-size")
        return 1
    if args.dedup_covers and not args.strip_covers and shutil.which("metaflac") is None:
        log.warning("metaflac missing – cover deduplication will be skipped")
        args.dedup_covers = False
    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    jobs = max(1, args.jobs)
    comp = min(10, max(0, args.comp))

    log.info(
            "Start processing. Parameters:\n"
            "  > Paths:  source='%s', target='%s'\n"
            "  > System: jobs=%d, max_time=%s, cleanup=%s, force=%s, dry_run=%s\n"
            "  > Audio:  comp=%d, update=%s, reencode_lossy=%s, min_bitrate=%s\n"
            "  > Cover:  dedup=%s, strip=%s, max_size=%s",
            source, target, jobs, args.max_time, args.cleanup, args.force, args.dry_run,
            comp, args.update, args.reencode_lossy, args.reencode_min_bitrate,
            args.dedup_covers, args.strip_covers, args.cover_max_size
        )

    return run(
        source, target,
        force=args.force, update=args.update, dry_run=args.dry_run, dedup_covers=args.dedup_covers,
        reencode_lossy=args.reencode_lossy, reencode_min_bitrate=args.reencode_min_bitrate,
        comp=comp, strip_covers=args.strip_covers, cover_max_size=args.cover_max_size,
        jobs=jobs, cleanup=args.cleanup, max_time_minutes=args.max_time,
    )


if __name__ == "__main__":
    sys.exit(main())
