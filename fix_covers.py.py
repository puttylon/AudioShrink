#!/usr/bin/env python3
"""fix_covers – finds target album folders WITHOUT covers and has audioshrink
recreate them with --force.

"Without cover" = the folder contains .opus files, but NEITHER a
cover.jpg/folder.jpg file NOR an image embedded in the Opus files.
Such folders originate from earlier AudioShrink runs (before cover
deduplication for lossy formats or with an ffmpeg lacking an mjpeg encoder).

The corresponding source folder is derived from the target path by removing
the "_ogg" suffix in the root directory (…/musik_ogg -> …/musik).
The source can also be specified explicitly with --source-root.

Usage:
  python3 fix_covers.py TARGET_ROOT                  # only list (safe)
  python3 fix_covers.py TARGET_ROOT --apply          # actually recreate
  python3 fix_covers.py TARGET_ROOT --source-root /path/to/source --apply
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import audioshrink as a


def opus_has_embedded_cover(path: Path) -> bool:
    """True if the Opus file contains an image (video) stream."""
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
    """Yields target folders with .opus, but without cover (neither file nor embedded)."""
    for root, dirs, files in os.walk(target_root):
        dirs[:] = [d for d in sorted(dirs)
                   if d not in a.IGNORE_DIRS and not d.startswith(".")]
        root_path = Path(root)
        opus_files = sorted(f for f in files if a.ext_of(Path(f)) == a.TARGET_EXT)
        if not opus_files:
            continue
        if dir_has_cover_file(root_path):
            continue
        # Within an album, all tracks are treated equally -> check the first one
        if opus_has_embedded_cover(root_path / opus_files[0]):
            continue
        yield root_path


def default_source_root(target_root: Path) -> Path:
    name = target_root.name
    if name.endswith("_ogg"):
        return target_root.parent / name[:-4]
    return target_root  # no meaningful derivation possible


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="fix_covers",
        description="Finds target album folders without covers and recreates "
                    "them with audioshrink --force.")
    parser.add_argument("target_root", help="Target root directory (e.g., …/musik_ogg)")
    parser.add_argument("--source-root", default=None,
                        help="Source root (Default: target root without '_ogg' suffix)")
    parser.add_argument("--apply", action="store_true",
                        help="actually call audioshrink (without this: only list)")
    parser.add_argument("--audioshrink", default=None,
                        help="Path to audioshrink.py (Default: next to this script)")
    args = parser.parse_args(argv)

    target_root = Path(args.target_root).expanduser().resolve()
    if not target_root.is_dir():
        print("Target root not found:", target_root)
        return 1

    source_root = (Path(args.source_root).expanduser().resolve()
                   if args.source_root else default_source_root(target_root))
    if not source_root.is_dir():
        print("Source root not found:", source_root)
        print("→ specify explicitly with --source-root.")
        return 1

    audioshrink_py = (Path(args.audioshrink).expanduser().resolve()
                      if args.audioshrink
                      else Path(__file__).resolve().parent / "audioshrink.py")
    if args.apply and not audioshrink_py.is_file():
        print("audioshrink.py not found:", audioshrink_py)
        return 1

    print("Source:", source_root)
    print("Target:", target_root)
    print("Searching for album folders without cover...")

    affected = list(find_coverless_albums(target_root))
    if not affected:
        print("No coverless album folders found.")
        return 0

    todo = []
    print("%d album folders without cover:" % len(affected))
    for tdir in affected:
        rel = tdir.relative_to(target_root)
        sdir = source_root / rel
        if sdir.is_dir():
            todo.append((sdir, tdir))
            print("  [ok] %s" % rel)
        else:
            print("  [source missing] %s" % rel)

    if not args.apply:
        print("\nListing only (safe). With --apply, the [ok] folders will be "
              "recreated via 'audioshrink --force --no-cleanup'.")
        return 0

    print("\nRecreating %d folders..." % len(todo))
    errors = 0
    for sdir, tdir in todo:
        cmd = [sys.executable, str(audioshrink_py), str(sdir), str(tdir),
               "--force", "--no-cleanup"]
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            errors += 1
            print("  Note: audioshrink rc=%d at %s" % (rc, tdir))
    print("\nDone. Recreated: %d, with notes: %d" % (len(todo), errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())