#!/usr/bin/env python3
"""fix_covers – findet Ziel-Album-Ordner OHNE Cover und lässt audioshrink sie
mit --force neu erzeugen.

„Ohne Cover" = der Ordner enthält .opus-Dateien, aber WEDER eine
cover.jpg/folder.jpg-Datei NOCH ein in die Opus-Dateien eingebettetes Bild.
Solche Ordner stammen aus früheren AudioShrink-Läufen (vor der Cover-
Deduplizierung für Lossy bzw. mit einem ffmpeg ohne mjpeg-Encoder).

Der zugehörige Quellordner wird aus dem Zielpfad abgeleitet, indem im
Wurzelverzeichnis die Endung „_ogg" entfernt wird (…/musik_ogg -> …/musik).
Mit --source-root lässt sich die Quelle auch explizit angeben.

Aufruf:
  python3 fix_covers.py ZIEL_WURZEL                  # nur auflisten (sicher)
  python3 fix_covers.py ZIEL_WURZEL --apply          # tatsächlich neu erzeugen
  python3 fix_covers.py ZIEL_WURZEL --source-root /pfad/zur/quelle --apply
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import audioshrink as a


def opus_has_embedded_cover(path: Path) -> bool:
    """True, wenn die Opus-Datei einen Bild-(Video-)Stream enthält."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return False
    return "video" in (res.stdout or "")


def dir_has_cover_file(dir_path: Path) -> bool:
    return any((dir_path / name).exists() for name in a.COVER_FILENAMES)


def find_coverless_albums(target_root: Path):
    """Liefert Ziel-Ordner mit .opus, aber ohne Cover (weder Datei noch eingebettet)."""
    for root, dirs, files in os.walk(target_root):
        dirs[:] = [d for d in sorted(dirs)
                   if d not in a.IGNORE_DIRS and not d.startswith(".")]
        root_path = Path(root)
        opus_files = sorted(f for f in files if a.ext_of(Path(f)) == a.TARGET_EXT)
        if not opus_files:
            continue
        if dir_has_cover_file(root_path):
            continue
        # Innerhalb eines Albums werden alle Tracks gleich behandelt -> erstes prüfen
        if opus_has_embedded_cover(root_path / opus_files[0]):
            continue
        yield root_path


def default_source_root(target_root: Path) -> Path:
    name = target_root.name
    if name.endswith("_ogg"):
        return target_root.parent / name[:-4]
    return target_root  # keine sinnvolle Ableitung möglich


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="fix_covers",
        description="Findet Ziel-Album-Ordner ohne Cover und erzeugt sie mit "
                    "audioshrink --force neu.")
    parser.add_argument("target_root", help="Ziel-Wurzelverzeichnis (z. B. …/musik_ogg)")
    parser.add_argument("--source-root", default=None,
                        help="Quell-Wurzel (Default: Ziel-Wurzel ohne Endung '_ogg')")
    parser.add_argument("--apply", action="store_true",
                        help="audioshrink wirklich aufrufen (ohne: nur auflisten)")
    parser.add_argument("--audioshrink", default=None,
                        help="Pfad zu audioshrink.py (Default: neben diesem Skript)")
    args = parser.parse_args(argv)

    target_root = Path(args.target_root).expanduser().resolve()
    if not target_root.is_dir():
        print("Ziel-Wurzel nicht gefunden:", target_root)
        return 1

    source_root = (Path(args.source_root).expanduser().resolve()
                   if args.source_root else default_source_root(target_root))
    if not source_root.is_dir():
        print("Quell-Wurzel nicht gefunden:", source_root)
        print("→ mit --source-root explizit angeben.")
        return 1

    audioshrink_py = (Path(args.audioshrink).expanduser().resolve()
                      if args.audioshrink
                      else Path(__file__).resolve().parent / "audioshrink.py")
    if args.apply and not audioshrink_py.is_file():
        print("audioshrink.py nicht gefunden:", audioshrink_py)
        return 1

    print("Quelle:", source_root)
    print("Ziel:  ", target_root)
    print("Suche Album-Ordner ohne Cover ...")

    affected = list(find_coverless_albums(target_root))
    if not affected:
        print("Keine cover-losen Album-Ordner gefunden.")
        return 0

    todo = []
    print("%d Album-Ordner ohne Cover:" % len(affected))
    for tdir in affected:
        rel = tdir.relative_to(target_root)
        sdir = source_root / rel
        if sdir.is_dir():
            todo.append((sdir, tdir))
            print("  [ok] %s" % rel)
        else:
            print("  [Quelle fehlt] %s" % rel)

    if not args.apply:
        print("\nNur Auflistung (sicher). Mit --apply werden die [ok]-Ordner "
              "via 'audioshrink --force --no-cleanup' neu erzeugt.")
        return 0

    print("\nErzeuge %d Ordner neu ..." % len(todo))
    errors = 0
    for sdir, tdir in todo:
        cmd = [sys.executable, str(audioshrink_py), str(sdir), str(tdir),
               "--force", "--no-cleanup"]
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            errors += 1
            print("  Hinweis: audioshrink rc=%d bei %s" % (rc, tdir))
    print("\nFertig. Neu erzeugt: %d, mit Hinweisen: %d" % (len(todo), errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
