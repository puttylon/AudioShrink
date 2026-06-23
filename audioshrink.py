#!/usr/bin/env python3
"""AudioShrink – verkleinert Musiksammlungen nach Opus.

Version 0.4 (Spiegel-Bereinigung):
  - Spiegelt die Verzeichnisstruktur von SOURCE nach TARGET.
  - FLAC/WAV/AIFF werden nach Opus transkodiert; die Bitrate wird pro Datei
    aus Samplerate, Genre und Quellbitrate ermittelt (ffprobe).
  - Alle anderen Dateien werden 1:1 kopiert.
  - Bereits aktuelle Ziele werden übersprungen (mtime/Größe); --force erzwingt.
  - Verwaiste Ziele (ohne Quelle) werden entfernt; abschaltbar mit --no-cleanup.
  - --dry-run zeigt alle Aktionen (inkl. Löschungen) nur an.
  - Pro-Datei-Fehler brechen den Lauf nicht ab.

Noch nicht enthalten (siehe ROADMAP.md): Cover-Deduplizierung,
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

__version__ = "0.4.0"

# --- Konfiguration -----------------------------------------------------------
LOSSLESS_FORMATS = {"flac", "wav", "aiff", "aif"}   # opusenc liest diese nativ
IGNORE_DIRS = {"@eaDir"}                             # System-/Cache-Ordner
TARGET_EXT = "opus"
MTIME_TOLERANCE = 1                                  # s; gegen Dateisystem-Rundung

# Eine Ziel-.opus kann aus einer verlustfreien Quelle transkodiert ODER aus
# einer vorhandenen .opus-Quelle kopiert worden sein.
OPUS_SOURCE_CANDIDATES = LOSSLESS_FORMATS | {TARGET_EXT}

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
def run(source: Path, target: Path, force: bool, dry_run: bool) -> int:
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

            if is_copy:
                if dry_run:
                    log.info("[DRY-RUN] würde kopieren: %s", rel)
                    copied += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                if copy(src, dst):
                    copied += 1
                    log.info("kopiert: %s", rel)
                else:
                    errors += 1
            else:
                info = analyze_audio(src)
                bitrate = determine_bitrate(info)
                tuning = opus_tuning(info)
                if dry_run:
                    log.info("[DRY-RUN] würde konvertieren: %s [%d kbps]", rel, bitrate)
                    converted += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
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


# --- Bereinigung -------------------------------------------------------------
def source_has_counterpart(target_file: Path, source: Path, target: Path) -> bool:
    """True, wenn es zur Zieldatei eine passende Quelle gibt."""
    rel_parent = target_file.parent.relative_to(target)
    name = target_file.name
    ext = target_file.suffix.lower().lstrip(".")
    if ext == TARGET_EXT:
        stem = name[: -(len(TARGET_EXT) + 1)]  # Endung per String entfernen (punktsicher)
        return any(
            (source / rel_parent / (stem + "." + e)).exists()
            for e in OPUS_SOURCE_CANDIDATES
        )
    return (source / rel_parent / name).exists()


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


def cleanup_target(source: Path, target: Path, dry_run: bool) -> None:
    log.info("Bereinigung (%s) ...", "DRY-RUN" if dry_run else "LÖSCHEN")
    removed = 0

    def keep_dirs(root_path: Path, dirs, drop_orphans: bool):
        """Filtert ignorierte/versteckte Ordner; optional auch verwaiste."""
        kept = []
        for d in sorted(dirs):
            if d in IGNORE_DIRS or d.startswith("."):
                continue
            rel = (root_path / d).relative_to(target)
            is_orphan = not (source / rel).is_dir()
            if drop_orphans and is_orphan:
                continue
            kept.append((d, is_orphan))
        return kept

    # 1) Verwaiste Verzeichnisse komplett entfernen
    for root, dirs, _files in os.walk(target, topdown=True):
        root_path = Path(root)
        survivors = []
        for d, is_orphan in keep_dirs(root_path, dirs, drop_orphans=False):
            if is_orphan:
                removed += _remove(root_path / d, dry_run, recursive=True)
            else:
                survivors.append(d)
        dirs[:] = survivors  # verwaiste nicht betreten

    # 2) Verwaiste Dateien entfernen (nur in noch gültige Ordner absteigen)
    for root, dirs, files in os.walk(target, topdown=True):
        root_path = Path(root)
        dirs[:] = [d for d, _ in keep_dirs(root_path, dirs, drop_orphans=True)]
        for name in sorted(files):
            if name.startswith("."):
                continue
            fpath = root_path / name
            if not source_has_counterpart(fpath, source, target):
                removed += _remove(fpath, dry_run)

    # 3) Leere Verzeichnisse entfernen (bottom-up)
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

    verb = "würden entfernt" if dry_run else "entfernt"
    log.info("Bereinigung abgeschlossen | %d Objekte %s", removed, verb)


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
    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    log.info("Start | Quelle=%s Ziel=%s", source, target)
    exit_code = run(source, target, force=args.force, dry_run=args.dry_run)

    if args.cleanup:
        cleanup_target(source, target, dry_run=args.dry_run)
    else:
        log.info("Aufräumen übersprungen (--no-cleanup)")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
