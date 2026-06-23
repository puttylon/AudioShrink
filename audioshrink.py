#!/usr/bin/env python3
"""AudioShrink – verkleinert Musiksammlungen nach Opus.

Version 0.9.2 (Re-Encode-Politik + Hybrid-Cleanup):
  - Spiegelt die Verzeichnisstruktur von SOURCE nach TARGET (ordnerweise).
  - FLAC/WAV/AIFF werden nach Opus transkodiert; die Bitrate wird pro Datei
    aus Samplerate, Genre und Quellbitrate ermittelt (ffprobe).
  - Album-Cover-Deduplizierung: gemeinsames eingebettetes Cover -> einmal
    cover.jpg/png, in den Opus-Dateien verworfen. Abschaltbar (--no-cover-dedup).
  - --cover-max-size verkleinert Cover (ImageMagick); --strip-covers entfernt
    Cover/Bilder vollständig.
  - Verlustbehaftete Quellen (MP3/AAC/...) werden standardmäßig nach Opus
    re-encodiert, wenn ihre Bitrate über der Schwelle liegt (--reencode-min-bitrate,
    Standard 192 kbps); darunter werden sie kopiert. Abschaltbar mit
    --no-reencode-lossy. Die Entscheidung hängt NUR an der Quellbitrate (kein Genre);
    die Zielbitrate/Tuning beim Encoden berücksichtigt Genre weiterhin
    (Speech -> 64 kbps, --speech). ffmpeg dekodiert, opusenc encodiert.
  - Bereits aktuelle Ziele werden übersprungen (mtime/Größe); --force erzwingt.
  - Verwaiste Ziele (ohne Quelle) werden entfernt; abschaltbar mit --no-cleanup.
    Aufräumen erfolgt hybrid: verwaiste Dateien sofort nach jedem Ordner,
    verwaiste Verzeichnisse + leere Ordner am Ende.
  - --dry-run zeigt alle Aktionen (inkl. Löschungen) nur an.
  - Konvertierungen laufen parallel (--jobs N, Standard 4); Logausgabe bleibt
    pro Album in Reihenfolge.
  - Pro-Datei-Fehler brechen den Lauf nicht ab.

Noch nicht enthalten (siehe ROADMAP.md): README/Doku, Release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "0.9.2"
DEFAULT_JOBS = 4
DEFAULT_REENCODE_MIN_BITRATE = 192   # kbps; verlustbehaftete Quellen DARÜBER werden re-encodiert

# --- Konfiguration -----------------------------------------------------------
LOSSLESS_FORMATS = {"flac", "wav", "aiff", "aif"}   # opusenc liest diese nativ
LOSSY_FORMATS = {"mp3", "opus", "ogg", "aac", "m4a", "wma"}
IMAGE_FORMATS = {"jpg", "jpeg", "png", "gif"}
IGNORE_DIRS = {"@eaDir"}                             # System-/Cache-Ordner
TARGET_EXT = "opus"
MTIME_TOLERANCE = 1                                  # s; gegen Dateisystem-Rundung
COVER_FILENAMES = {"cover.jpg", "cover.png", "folder.jpg", "folder.png"}
COVER_QUALITY = 85                                   # JPEG-Qualität beim Verkleinern

# Eine Ziel-.opus kann aus einer verlustfreien Quelle transkodiert ODER aus
# einer vorhandenen .opus-Quelle kopiert worden sein (mit --reencode-lossy auch
# aus anderen verlustbehafteten Quellen).
OPUS_SOURCE_CANDIDATES = LOSSLESS_FORMATS | {TARGET_EXT}

# Bitratenwahl
SPEECH_GENRES = {"hörbuch", "audiobook", "speech", "podcast", "spoken", "hörspiel"}
SPEECH_BITRATE = 64

DRY_VERB = {
    "transcode": "würde konvertieren",
    "transcode_lossy": "würde re-encodieren",
    "cover_resize": "würde Cover verkleinern",
    "copy": "würde kopieren",
}

log = logging.getLogger("audioshrink")


# --- Hilfsfunktionen ---------------------------------------------------------
def check_dependencies() -> bool:
    ok = True
    for tool in ("opusenc", "ffprobe"):
        if shutil.which(tool) is None:
            log.error("Pflichtabhängigkeit fehlt: %s", tool)
            ok = False
    return ok


def im_cmd():
    """Bevorzugt ImageMagick 7 (magick), sonst 6 (convert); None wenn keins da."""
    for candidate in ("magick", "convert"):
        if shutil.which(candidate):
            return candidate
    return None


def ext_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def is_lossless(path: Path) -> bool:
    return ext_of(path) in LOSSLESS_FORMATS


def walk_by_directory(source: Path, target: Path):
    """Liefert (Ordner, [Dateien]) je Quell-Ordner; ignoriert versteckte und
    ausgeschlossene Ordner sowie das (ggf. eingebettete) Zielverzeichnis."""
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
    """True, wenn das Ziel als aktuell gelten kann und übersprungen werden darf."""
    if not dst.exists():
        return False
    try:
        s = src.stat()
        d = dst.stat()
    except OSError:
        return False  # im Zweifel verarbeiten
    if s.st_mtime > d.st_mtime + MTIME_TOLERANCE:   # Quelle deutlich neuer
        return False
    if compare_size and s.st_size != d.st_size:     # reine Kopie: zusätzlich Größe
        return False
    return True


def safe_unlink(path: Path) -> None:
    """Entfernt eine (unvollständige) Zieldatei. Schluckt dabei jeden Fehler,
    damit die Aufräumlogik selbst niemals den Lauf abbricht."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Konnte unvollständige Datei nicht entfernen: %s (%s)", path, exc)


def copy_source_mtime(src: Path, dst: Path) -> None:
    """Überträgt die mtime der Quelle auf das Ziel, damit die Skip-Logik
    bei späteren Läufen konsistent greift (opusenc setzt sonst die Encode-Zeit)."""
    try:
        st = src.stat()
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError as exc:
        log.warning("mtime konnte nicht gesetzt werden: %s (%s)", dst, exc)


# --- Audioanalyse & Bitratenwahl --------------------------------------------
def analyze_audio(path: Path) -> dict:
    """Liest Samplerate, Quellbitrate und alle Tags via ffprobe. Bei Fehlern
    werden Defaults zurückgegeben, sodass die Konvertierung trotzdem läuft."""
    info = {"sample_rate": 0, "bitrate_kbps": 0, "genre": "", "tags": {}}
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "stream=sample_rate,bit_rate:stream_tags:format=bit_rate:format_tags",
        "-of", "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            log.warning("ffprobe-Analyse fehlgeschlagen: %s", path)
            return info
        data = json.loads(result.stdout or "{}")
    except (OSError, ValueError) as exc:
        log.warning("ffprobe-Analyse fehlgeschlagen: %s (%s)", path, exc)
        return info

    fmt = data.get("format", {})
    stream = (data.get("streams") or [{}])[0]

    sr = stream.get("sample_rate")
    if sr and sr != "N/A":
        info["sample_rate"] = int(sr)

    br = stream.get("bit_rate") or fmt.get("bit_rate")
    if br and br != "N/A":
        info["bitrate_kbps"] = int(br) // 1000

    tags = {}
    for source_tags in (fmt.get("tags"), stream.get("tags")):
        if source_tags:
            for k, v in source_tags.items():
                tags[k.lower()] = v
    info["tags"] = tags
    info["genre"] = tags.get("genre", "")

    return info


def is_speech(genre: str) -> bool:
    g = (genre or "").lower()
    return any(term in g for term in SPEECH_GENRES)


def determine_bitrate(info: dict) -> int:
    """Bitrate aus Samplerate (Basis), Genre und Quellbitrate (Deckel)."""
    sr = info.get("sample_rate", 0)
    if sr >= 96000:
        bitrate = 160
    elif sr >= 48000:
        bitrate = 128
    else:
        bitrate = 96  # u. a. 44,1-kHz-CD-Material

    if is_speech(info.get("genre", "")):
        bitrate = min(bitrate, SPEECH_BITRATE)

    src_br = info.get("bitrate_kbps", 0)
    if src_br > 0:                       # nie höher ansetzen als die Quelle
        bitrate = min(bitrate, src_br)

    return bitrate


def opus_tuning(info: dict) -> str:
    return "--speech" if is_speech(info.get("genre", "")) else "--music"


def should_reencode(info: dict, min_bitrate: int = DEFAULT_REENCODE_MIN_BITRATE) -> bool:
    """Ob eine verlustbehaftete Quelle re-encodiert wird – Entscheidung NUR anhand
    der Quellbitrate (kein Genre): über der Schwelle und tatsächlich schrumpfend."""
    src_br = info.get("bitrate_kbps", 0)
    return src_br > min_bitrate and src_br > determine_bitrate(info)


def build_metadata_opts(info: dict) -> list:
    """opusenc-Metadatenoptionen aus den Quell-Tags (für den Lossy-Re-Encode,
    da ffmpeg die Tags beim Dekodieren nach WAV verliert)."""
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


# --- Album-Cover-Deduplizierung ---------------------------------------------
def embedded_cover_bytes(flac_path: Path):
    """Exportiert das eingebettete Cover einer FLAC-Datei (oder None)."""
    cmd = ["metaflac", "--export-picture-to=-", str(flac_path)]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def plan_album_cover(files: list) -> dict:
    """Entscheidet, ob für dieses Album ein gemeinsames Cover dedupliziert wird.
    Rückgabe: {discard_embedded: bool, write_cover: bytes|None}."""
    plan = {"discard_embedded": False, "write_cover": None}

    flacs = [f for f in files if ext_of(f) == "flac"]
    if not flacs:
        return plan

    # (a) Liegt bereits eine separate Cover-Datei in der Quelle? Dann reicht es,
    #     das eingebettete Cover zu verwerfen – die Datei wird normal kopiert.
    if any(f.name.lower() in COVER_FILENAMES for f in files):
        plan["discard_embedded"] = True
        return plan

    # (b) Haben alle FLAC-Tracks dasselbe eingebettete Cover?
    first_bytes = None
    digests = set()
    for f in flacs:
        data = embedded_cover_bytes(f)
        if data is None:
            return plan  # mind. ein Track ohne Cover → nicht deduplizieren
        if first_bytes is None:
            first_bytes = data
        digests.add(hashlib.md5(data).digest())

    if len(digests) == 1:
        plan["discard_embedded"] = True
        plan["write_cover"] = first_bytes
    return plan


def write_cover_file(dest: Path, data: bytes, cover_max_size) -> bool:
    """Schreibt die Cover-Bytes nach dest, optional via ImageMagick verkleinert."""
    if cover_max_size and im_cmd():
        cmd = [
            im_cmd(), "-",
            "-resize", "%dx%d>" % (cover_max_size, cover_max_size),
            "-strip", "-quality", str(COVER_QUALITY),
            str(dest),
        ]
        try:
            res = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
        except OSError as exc:
            log.error("Album-Cover konnte nicht verkleinert werden: %s (%s)", dest, exc)
            return False
        if res.returncode != 0:
            log.error("Album-Cover konnte nicht verkleinert werden: %s", dest)
            return False
        return True
    dest.write_bytes(data)
    return True


def finalize_album_cover(src_dir: Path, source: Path, target: Path,
                         plan: dict, dry_run: bool, cover_max_size) -> int:
    """Legt das extrahierte Album-Cover als eine Datei ab. Gibt 1 zurück, wenn
    ein Cover geschrieben wurde (bzw. im Dry-Run gemeldet)."""
    data = plan.get("write_cover")
    if not data:
        return 0
    ext = "png" if data.startswith(b"\x89PNG") else "jpg"
    rel = src_dir.relative_to(source)
    relname = rel / ("cover." + ext)
    cover_dest = target / relname
    if dry_run:
        log.info("[DRY-RUN] würde Album-Cover anlegen: %s", relname)
        return 1
    try:
        cover_dest.parent.mkdir(parents=True, exist_ok=True)
        if not write_cover_file(cover_dest, data, cover_max_size):
            return 0
        log.info("Album-Cover angelegt: %s", relname)
        return 1
    except OSError as exc:
        log.error("Album-Cover konnte nicht angelegt werden: %s (%s)", cover_dest, exc)
        return 0


# --- Verarbeitung einzelner Dateien -----------------------------------------
def _opusenc_base(bitrate: int, tuning: str, discard_pictures: bool) -> list:
    cmd = [
        "opusenc",
        "--bitrate", str(bitrate),
        "--vbr",
        tuning,                 # --music oder --speech
        "--comp", "10",
        "--framesize", "20",
        "--quiet",
    ]
    if discard_pictures:
        cmd.append("--discard-pictures")
    return cmd


def transcode(src: Path, dst: Path, bitrate: int, tuning: str,
              discard_pictures: bool):
    """Verlustfreie Quelle (FLAC/WAV/AIFF) direkt mit opusenc nach Opus.
    Rückgabe: (ok, fehlermeldung|None)."""
    tmp_dst = dst.with_name(dst.name + ".tmp")
    cmd = _opusenc_base(bitrate, tuning, discard_pictures) + [str(src), str(tmp_dst)]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
    except OSError as exc:
        safe_unlink(tmp_dst)
        return False, "Konvertierung fehlgeschlagen: %s (%s)" % (src, exc)
    if result.returncode != 0:
        safe_unlink(tmp_dst)
        return False, "Konvertierung fehlgeschlagen: %s\n%s" % (src, result.stderr.strip())
    try:
        os.replace(tmp_dst, dst)  # atomarer Austausch
    except OSError as exc:
        safe_unlink(tmp_dst)
        return False, "Zieldatei konnte nicht umbenannt werden: %s (%s)" % (dst, exc)
    copy_source_mtime(src, dst)
    return True, None


def extract_cover_ffmpeg(src: Path):
    """Extrahiert ein eingebettetes Cover aus einer verlustbehafteten Quelle in
    eine temporäre JPEG-Datei (oder None). Pfad muss vom Aufrufer entfernt werden."""
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp = Path(tmp_name)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src),
           "-an", "-frames:v", "1", str(tmp)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        safe_unlink(tmp)
        return None
    try:
        if res.returncode != 0 or tmp.stat().st_size == 0:
            safe_unlink(tmp)
            return None
    except OSError:
        safe_unlink(tmp)
        return None
    return tmp


def transcode_lossy(src: Path, dst: Path, bitrate: int, tuning: str,
                    info: dict, discard_pictures: bool):
    """Verlustbehaftete Quelle re-encodieren: ffmpeg dekodiert nach WAV, opusenc
    encodiert. Metadaten und (sofern gewünscht) Cover werden neu gesetzt.
    Rückgabe: (ok, fehlermeldung|None)."""
    tmp_dst = dst.with_name(dst.name + ".tmp")
    pic_tmp = None
    pic_opts = []
    if not discard_pictures:
        pic_tmp = extract_cover_ffmpeg(src)
        if pic_tmp:
            pic_opts = ["--picture", "3||||" + str(pic_tmp)]

    opus_cmd = (_opusenc_base(bitrate, tuning, discard_pictures)
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
            err = "Re-Encode fehlgeschlagen: %s\n%s" % (src, res.stderr.strip())
    except OSError as exc:
        err = "Re-Encode fehlgeschlagen: %s (%s)" % (src, exc)
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
        return False, "Zieldatei konnte nicht umbenannt werden: %s (%s)" % (dst, exc)
    copy_source_mtime(src, dst)
    return True, None


def cover_resize_file(src: Path, dst: Path, cover_max_size: int):
    """Kopiert ein separates Cover-Bild verkleinert (ImageMagick).
    Rückgabe: (ok, fehlermeldung|None)."""
    cmd = [
        im_cmd(), str(src),
        "-resize", "%dx%d>" % (cover_max_size, cover_max_size),
        "-strip", "-quality", str(COVER_QUALITY),
        str(dst),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True)
    except OSError as exc:
        safe_unlink(dst)
        return False, "Cover-Verkleinerung fehlgeschlagen: %s (%s)" % (src, exc)
    if res.returncode != 0:
        safe_unlink(dst)
        return False, "Cover-Verkleinerung fehlgeschlagen: %s\n%s" % (src, res.stderr.strip())
    copy_source_mtime(src, dst)
    return True, None


def copy(src: Path, dst: Path):
    """Rückgabe: (ok, fehlermeldung|None)."""
    try:
        shutil.copy2(src, dst)  # erhält mtime bereits
        return True, None
    except OSError as exc:
        safe_unlink(dst)
        return False, "Kopie fehlgeschlagen: %s (%s)" % (src, exc)


# --- Hauptverarbeitung -------------------------------------------------------
def process_one(src: Path, source: Path, target: Path, plan: dict, *,
                force: bool, dry_run: bool, reencode_lossy: bool,
                reencode_min_bitrate: int, strip_covers: bool, cover_max_size):
    """Verarbeitet eine einzelne Datei (im Worker-Thread). Loggt nicht selbst,
    sondern liefert (status, [(level, message), ...]) zurück, damit die Ausgabe
    geordnet vom Hauptthread erfolgt. status: converted|copied|skipped|ignored|error."""
    try:
        rel = src.relative_to(source)
        ext = ext_of(src)
        name_lower = src.name.lower()

        # Bilder unter --strip-covers gar nicht übernehmen
        if strip_covers and ext in IMAGE_FORMATS:
            return "ignored", [("debug", "Cover entfernt (nicht kopiert): %s" % rel)]

        # Aktion + Ziel bestimmen
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
        if not force and is_up_to_date(src, dst, compare_size):
            return "skipped", [("debug", "übersprungen (aktuell): %s" % rel)]

        if dry_run:
            status = "converted" if action in ("transcode", "transcode_lossy") else "copied"
            return status, [("info", "[DRY-RUN] %s: %s" % (DRY_VERB[action], rel))]

        dst.parent.mkdir(parents=True, exist_ok=True)
        if action == "transcode":
            info = analyze_audio(src)
            bitrate = determine_bitrate(info)
            ok, err = transcode(src, dst, bitrate, opus_tuning(info), plan["discard_embedded"])
            if ok:
                return "converted", [("info", "konvertiert: %s [%d kbps]" % (rel, bitrate))]
        elif action == "transcode_lossy":
            bitrate = determine_bitrate(info)
            ok, err = transcode_lossy(src, dst, bitrate, opus_tuning(info), info,
                                      plan["discard_embedded"])
            if ok:
                return "converted", [("info", "re-encodiert: %s [%d kbps]" % (rel, bitrate))]
        elif action == "cover_resize":
            ok, err = cover_resize_file(src, dst, cover_max_size)
            if ok:
                return "copied", [("info", "Cover verkleinert: %s" % rel)]
        else:  # copy
            ok, err = copy(src, dst)
            if ok:
                return "copied", [("info", "kopiert: %s" % rel)]
        return "error", [("error", err or "Fehlgeschlagen: %s" % rel)]
    except Exception as exc:  # pro Datei isolieren – nie den Lauf abbrechen
        return "error", [("error", "Übersprungen (Fehler): %s (%s)" % (src, exc))]


def run(source: Path, target: Path, *, force: bool, dry_run: bool,
        dedup_covers: bool, reencode_lossy: bool, reencode_min_bitrate: int,
        strip_covers: bool, cover_max_size, jobs: int, cleanup: bool) -> int:
    counts = {"converted": 0, "copied": 0, "skipped": 0, "ignored": 0, "error": 0}
    covers = 0
    removed = 0

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for src_dir, files in walk_by_directory(source, target):
            # Album-Cover-Plan (seriell, vor den Track-Tasks)
            plan = {"discard_embedded": False, "write_cover": None}
            if strip_covers:
                plan = {"discard_embedded": True, "write_cover": None}
            elif dedup_covers:
                need = any(
                    ext_of(f) == "flac"
                    and (force or not is_up_to_date(
                        f, target / f.relative_to(source).with_suffix("." + TARGET_EXT), False))
                    for f in files
                )
                if need:
                    plan = plan_album_cover(files)

            # Tracks parallel verarbeiten ...
            futures = [
                pool.submit(process_one, src, source, target, plan,
                            force=force, dry_run=dry_run, reencode_lossy=reencode_lossy,
                            reencode_min_bitrate=reencode_min_bitrate,
                            strip_covers=strip_covers, cover_max_size=cover_max_size)
                for src in files
            ]
            # ... Ausgabe aber in Submission-Reihenfolge (lesbar)
            for fut in futures:
                status, messages = fut.result()
                for level, msg in messages:
                    getattr(log, level)(msg)
                counts[status] += 1

            covers += finalize_album_cover(src_dir, source, target, plan, dry_run, cover_max_size)

            # Hybrid-Cleanup, Teil 1: verwaiste Dateien dieses Ordners sofort
            if cleanup:
                removed += cleanup_dir_files(
                    source, target, src_dir, dry_run=dry_run,
                    dedup_covers=dedup_covers, reencode_lossy=reencode_lossy,
                    reencode_min_bitrate=reencode_min_bitrate)

    log.info(
        "Fertig | konvertiert=%d kopiert=%d übersprungen=%d Cover=%d Fehler=%d",
        counts["converted"], counts["copied"], counts["skipped"], covers, counts["error"],
    )

    # Hybrid-Cleanup, Teil 2: verwaiste Verzeichnisse + leere Ordner am Ende
    if cleanup:
        removed += cleanup_dirs(source, target, dry_run=dry_run)
        verb = "würden entfernt" if dry_run else "entfernt"
        log.info("Bereinigung abgeschlossen | %d Objekte %s", removed, verb)
    else:
        log.info("Aufräumen übersprungen (--no-cleanup)")

    return 2 if counts["error"] else 0


# --- Bereinigung -------------------------------------------------------------
def source_has_counterpart(target_file: Path, source: Path, target: Path,
                           reencode_lossy: bool,
                           reencode_min_bitrate: int = DEFAULT_REENCODE_MIN_BITRATE) -> bool:
    """True, wenn es zur Zieldatei eine passende Quelle gibt."""
    rel_parent = target_file.parent.relative_to(target)
    name = target_file.name
    ext = ext_of(target_file)
    if ext == TARGET_EXT:
        stem = name[: -(len(TARGET_EXT) + 1)]  # Endung per String entfernen (punktsicher)
        candidates = OPUS_SOURCE_CANDIDATES | (LOSSY_FORMATS if reencode_lossy else set())
        return any(
            (source / rel_parent / (stem + "." + e)).exists()
            for e in candidates
        )

    src_file = source / rel_parent / name
    if not src_file.exists():
        return False
    # Mit --reencode-lossy wird eine verlustbehaftete Quelle ggf. zu .opus – dann
    # ist die gleichnamige Nicht-Opus-Datei im Ziel überholt (Ziel sollte .opus sein).
    if (reencode_lossy and ext in LOSSY_FORMATS
            and should_reencode(analyze_audio(src_file), reencode_min_bitrate)):
        return False
    return True


def _remove(path: Path, dry_run: bool, recursive: bool = False) -> int:
    """Entfernt path. Gibt 1 zurück, wenn entfernt (bzw. im Dry-Run gemeldet)."""
    if dry_run:
        log.info("[DRY-RUN] würde entfernen: %s", path)
        return 1
    try:
        if recursive:
            shutil.rmtree(path)
        elif path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        log.info("entfernt: %s", path)
        return 1
    except OSError as exc:
        log.error("Konnte nicht entfernen: %s (%s)", path, exc)
        return 0


def cleanup_dir_files(source: Path, target: Path, src_dir: Path, *, dry_run: bool,
                      dedup_covers: bool, reencode_lossy: bool,
                      reencode_min_bitrate: int) -> int:
    """Entfernt verwaiste DATEIEN im zu src_dir gehörenden Zielordner (nicht
    rekursiv). Läuft direkt nach Fertigstellung des Ordners."""
    rel = src_dir.relative_to(source)
    target_dir = target / rel
    if not target_dir.is_dir():
        return 0
    removed = 0
    for entry in sorted(target_dir.iterdir()):
        name = entry.name
        if name.startswith(".") or not entry.is_file():
            continue
        # Generiertes Album-Cover behalten (Quell-Ordner existiert ja gerade)
        if dedup_covers and name.lower() in COVER_FILENAMES:
            continue
        if not source_has_counterpart(entry, source, target, reencode_lossy,
                                      reencode_min_bitrate):
            removed += _remove(entry, dry_run)
    return removed


def cleanup_dirs(source: Path, target: Path, *, dry_run: bool) -> int:
    """Abschluss-Durchlauf: verwaiste VERZEICHNISSE (gelöschte Alben) und
    anschließend leere Ordner entfernen."""
    removed = 0

    # 1) Verwaiste Verzeichnisse komplett entfernen
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
        dirs[:] = survivors  # verwaiste nicht betreten

    # 2) Leere Verzeichnisse entfernen (bottom-up)
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
        description="Verkleinert eine Musiksammlung nach Opus (gespiegelte Struktur).",
    )
    parser.add_argument("source", help="Quellverzeichnis")
    parser.add_argument("target", help="Zielverzeichnis")
    parser.add_argument(
        "--no-cleanup", dest="cleanup", action="store_false",
        help="Verwaiste Ziel-Dateien/-Ordner NICHT löschen",
    )
    parser.add_argument(
        "--no-cover-dedup", dest="dedup_covers", action="store_false",
        help="Album-Cover NICHT deduplizieren (eingebettete Cover bleiben erhalten)",
    )
    parser.add_argument(
        "--no-reencode-lossy", dest="reencode_lossy", action="store_false",
        help="Verlustbehaftete Quellen NICHT re-encodieren (immer kopieren)",
    )
    parser.add_argument(
        "--reencode-min-bitrate", type=int, default=DEFAULT_REENCODE_MIN_BITRATE,
        metavar="KBPS",
        help="Verlustbehaftete Quellen oberhalb dieser Bitrate re-encodieren "
             "(Standard: %d)" % DEFAULT_REENCODE_MIN_BITRATE,
    )
    parser.add_argument(
        "--cover-max-size", type=int, default=None, metavar="PX",
        help="Cover auf max. Kantenlänge PX verkleinern; benötigt ImageMagick",
    )
    parser.add_argument(
        "--strip-covers", action="store_true",
        help="Cover/Bilder vollständig entfernen (keine eingebetteten Cover, keine Bilddateien)",
    )
    parser.add_argument(
        "--jobs", type=int, default=DEFAULT_JOBS, metavar="N",
        help="Anzahl paralleler Konvertierungen (Standard: %d)" % DEFAULT_JOBS,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Alle Aktionen (inkl. Löschungen) nur anzeigen, nichts ausführen",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Alle Dateien neu verarbeiten (Aktualitätsprüfung ignorieren)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Ausführliche Protokollausgabe (zeigt u. a. übersprungene Dateien)",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
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
        log.error("Quellverzeichnis nicht gefunden: %s", source)
        return 1
    if source == target:
        log.error("Quelle und Ziel dürfen nicht identisch sein")
        return 1
    if args.reencode_lossy and shutil.which("ffmpeg") is None:
        log.warning("ffmpeg fehlt – verlustbehaftete Quellen werden kopiert statt re-encodiert")
        args.reencode_lossy = False
    if args.cover_max_size and im_cmd() is None:
        log.error("ImageMagick (magick/convert) wird für --cover-max-size benötigt")
        return 1
    if args.dedup_covers and not args.strip_covers and shutil.which("metaflac") is None:
        log.warning("metaflac fehlt – Cover-Deduplizierung wird übersprungen")
        args.dedup_covers = False
    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    jobs = max(1, args.jobs)
    log.info("Start | Quelle=%s Ziel=%s | Jobs=%d", source, target, jobs)
    return run(
        source, target,
        force=args.force, dry_run=args.dry_run, dedup_covers=args.dedup_covers,
        reencode_lossy=args.reencode_lossy, reencode_min_bitrate=args.reencode_min_bitrate,
        strip_covers=args.strip_covers, cover_max_size=args.cover_max_size,
        jobs=jobs, cleanup=args.cleanup,
    )


if __name__ == "__main__":
    sys.exit(main())
