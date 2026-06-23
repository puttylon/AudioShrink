#!/usr/bin/env python3
"""AudioShrink – verkleinert Musiksammlungen nach Opus.

Version 0.3 (intelligente Bitrate):
  - Spiegelt die Verzeichnisstruktur von SOURCE nach TARGET.
  - FLAC/WAV/AIFF werden nach Opus transkodiert; die Bitrate wird pro Datei
    aus Samplerate, Genre und Quellbitrate ermittelt (ffprobe).
  - Alle anderen Dateien werden 1:1 kopiert.
  - Bereits aktuelle Ziele werden übersprungen (mtime/Größe); --force erzwingt.
  - Pro-Datei-Fehler brechen den Lauf nicht ab.

Noch nicht enthalten (siehe ROADMAP.md): Cleanup, Cover-Deduplizierung,
Lossy-Re-Encode, Parallelisierung.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

__version__ = "0.3.0"

# --- Konfiguration -----------------------------------------------------------
LOSSLESS_FORMATS = {"flac", "wav", "aiff", "aif"}   # opusenc liest diese nativ
IGNORE_DIRS = {"@eaDir"}                             # System-/Cache-Ordner
TARGET_EXT = "opus"
MTIME_TOLERANCE = 1                                  # s; gegen Dateisystem-Rundung

# Bitratenwahl
SPEECH_GENRES = {"hörbuch", "audiobook", "speech", "podcast", "spoken", "hörspiel"}
SPEECH_BITRATE = 64

log = logging.getLogger("audioshrink")


# --- Hilfsfunktionen ---------------------------------------------------------
def check_dependencies() -> bool:
    ok = True
    for tool in ("opusenc", "ffprobe"):
        if shutil.which(tool) is None:
            log.error("Pflichtabhängigkeit fehlt: %s", tool)
            ok = False
    return ok


def is_lossless(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in LOSSLESS_FORMATS


def iter_source_files(source: Path, target: Path):
    """Liefert alle zu verarbeitenden Dateien; ignoriert versteckte und
    ausgeschlossene Ordner sowie das (ggf. eingebettete) Zielverzeichnis."""
    for root, dirs, files in os.walk(source):
        root_path = Path(root)
        dirs[:] = sorted(
            d for d in dirs
            if d not in IGNORE_DIRS
            and not d.startswith(".")
            and (root_path / d).resolve() != target
        )
        for name in sorted(files):
            if not name.startswith("."):
                yield root_path / name


def is_up_to_date(src: Path, dst: Path, is_copy: bool) -> bool:
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
    if is_copy and s.st_size != d.st_size:          # Kopie: zusätzlich Größe
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
def _tag_get(tags: dict, key: str):
    if not tags:
        return None
    return {k.lower(): v for k, v in tags.items()}.get(key.lower())


def analyze_audio(path: Path) -> dict:
    """Liest Samplerate, Quellbitrate und Genre via ffprobe. Bei Fehlern werden
    Defaults zurückgegeben, sodass die Konvertierung trotzdem laufen kann."""
    info = {"sample_rate": 0, "bitrate_kbps": 0, "genre": ""}
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "stream=sample_rate,bit_rate:stream_tags=genre:format=bit_rate:format_tags=genre",
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

    genre = _tag_get(stream.get("tags"), "genre") or _tag_get(fmt.get("tags"), "genre")
    if genre:
        info["genre"] = genre

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


# --- Verarbeitung einzelner Dateien -----------------------------------------
def transcode(src: Path, dst: Path, bitrate: int, tuning: str) -> bool:
    cmd = [
        "opusenc",
        "--bitrate", str(bitrate),
        "--vbr",
        tuning,                 # --music oder --speech
        "--comp", "10",
        "--framesize", "20",
        "--quiet",
        str(src), str(dst),
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
    except OSError as exc:
        log.error("Konvertierung fehlgeschlagen: %s (%s)", src, exc)
        safe_unlink(dst)
        return False
    if result.returncode != 0:
        log.error("Konvertierung fehlgeschlagen: %s\n%s", src, result.stderr.strip())
        safe_unlink(dst)
        return False
    copy_source_mtime(src, dst)
    return True


def copy(src: Path, dst: Path) -> bool:
    try:
        shutil.copy2(src, dst)  # erhält mtime bereits
        return True
    except OSError as exc:
        log.error("Kopie fehlgeschlagen: %s (%s)", src, exc)
        safe_unlink(dst)
        return False


# --- Hauptverarbeitung -------------------------------------------------------
def run(source: Path, target: Path, force: bool) -> int:
    converted = copied = skipped = errors = 0

    for src in iter_source_files(source, target):
        try:
            rel = src.relative_to(source)
            is_copy = not is_lossless(src)
            dst = (target / rel) if is_copy else (target / rel.with_suffix("." + TARGET_EXT))

            if not force and is_up_to_date(src, dst, is_copy):
                skipped += 1
                log.debug("übersprungen (aktuell): %s", rel)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_copy:
                if copy(src, dst):
                    copied += 1
                    log.info("kopiert: %s", rel)
                else:
                    errors += 1
            else:
                info = analyze_audio(src)
                bitrate = determine_bitrate(info)
                tuning = opus_tuning(info)
                if transcode(src, dst, bitrate, tuning):
                    converted += 1
                    log.info("konvertiert: %s [%d kbps]", rel, bitrate)
                else:
                    errors += 1
        except OSError as exc:
            log.error("Übersprungen (Fehler): %s (%s)", src, exc)
            errors += 1

    log.info(
        "Fertig | konvertiert=%d kopiert=%d übersprungen=%d Fehler=%d",
        converted, copied, skipped, errors,
    )
    return 2 if errors else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="audioshrink",
        description="Verkleinert eine Musiksammlung nach Opus (gespiegelte Struktur).",
    )
    parser.add_argument("source", help="Quellverzeichnis")
    parser.add_argument("target", help="Zielverzeichnis")
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
    target.mkdir(parents=True, exist_ok=True)

    log.info("Start | Quelle=%s Ziel=%s", source, target)
    return run(source, target, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
