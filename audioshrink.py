#!/usr/bin/env python3
"""AudioShrink – verkleinert Musiksammlungen nach Opus.

Version 0.1 (Grundgerüst):
  - Spiegelt die Verzeichnisstruktur von SOURCE nach TARGET.
  - FLAC/WAV/AIFF werden mit fester Bitrate (128 kbps) nach Opus transkodiert.
  - Alle anderen Dateien werden 1:1 kopiert.

Noch nicht enthalten (siehe ROADMAP.md): Skip-Logik, intelligente Bitrate,
Cleanup, Cover-Deduplizierung, Lossy-Re-Encode, Parallelisierung.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

__version__ = "0.1.0"

# --- Konfiguration -----------------------------------------------------------
LOSSLESS_FORMATS = {"flac", "wav", "aiff", "aif"}   # opusenc liest diese nativ
IGNORE_DIRS = {"@eaDir"}                             # System-/Cache-Ordner
TARGET_EXT = "opus"
BITRATE = 128                                        # feste Bitrate (0.1)

OPUSENC_OPTS = [
    "--bitrate", str(BITRATE),
    "--vbr", "--music",
    "--comp", "10",
    "--framesize", "20",
    "--quiet",
]

log = logging.getLogger("audioshrink")


# --- Hilfsfunktionen ---------------------------------------------------------
def check_dependencies() -> bool:
    if shutil.which("opusenc") is None:
        log.error("Pflichtabhängigkeit fehlt: opusenc")
        return False
    return True


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


def safe_unlink(path: Path) -> None:
    """Entfernt eine (unvollständige) Zieldatei. Schluckt dabei jeden Fehler,
    damit die Aufräumlogik selbst niemals den Lauf abbricht."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Konnte unvollständige Datei nicht entfernen: %s (%s)", path, exc)


def transcode(src: Path, dst: Path) -> bool:
    cmd = ["opusenc", *OPUSENC_OPTS, str(src), str(dst)]
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
    return True


def copy(src: Path, dst: Path) -> bool:
    try:
        shutil.copy2(src, dst)
        return True
    except OSError as exc:
        log.error("Kopie fehlgeschlagen: %s (%s)", src, exc)
        safe_unlink(dst)
        return False


# --- Hauptverarbeitung -------------------------------------------------------
def run(source: Path, target: Path) -> int:
    converted = copied = errors = 0

    for src in iter_source_files(source, target):
        try:
            rel = src.relative_to(source)
            if is_lossless(src):
                dst = target / rel.with_suffix("." + TARGET_EXT)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if transcode(src, dst):
                    converted += 1
                    log.info("konvertiert: %s", rel)
                else:
                    errors += 1
            else:
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if copy(src, dst):
                    copied += 1
                    log.info("kopiert: %s", rel)
                else:
                    errors += 1
        except OSError as exc:
            log.error("Übersprungen (Fehler): %s (%s)", src, exc)
            errors += 1

    log.info("Fertig | konvertiert=%d kopiert=%d Fehler=%d", converted, copied, errors)
    return 2 if errors else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="audioshrink",
        description="Verkleinert eine Musiksammlung nach Opus (gespiegelte Struktur).",
    )
    parser.add_argument("source", help="Quellverzeichnis")
    parser.add_argument("target", help="Zielverzeichnis")
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
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
    return run(source, target)


if __name__ == "__main__":
    sys.exit(main())
