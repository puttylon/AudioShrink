# Changelog

Nennenswerte Änderungen an AudioShrink. Format angelehnt an „Keep a Changelog",
Versionierung sinngemäß nach SemVer.

## 1.0.0 — 2026-06-24

Erste stabile Version. Im produktiven Einsatz auf einer großen Sammlung
(Synology DS718plus, Python 3.8) erprobt.

### Funktionsumfang
- Spiegelt QUELLE → ZIEL mit identischer Struktur; **inkrementell** (überspringt
  Aktuelles) und **fortsetzbar** nach Abbruch.
- FLAC/WAV/AIFF → **Opus** (opusenc als einziger Encoder).
- Verlustbehaftete Quellen: kopieren, oder **Re-Encode** nach Opus **oberhalb einer
  Bitrate-Schwelle** (Standard 320 kbps; `--no-reencode-lossy`). ffmpeg dient dabei
  nur als Dekoder; Tags/Cover werden neu gesetzt.
- **Intelligente Zielbitrate**: Samplerate-Basis (160/128/96), Sprach-Genres
  → 64 kbps + `--speech`, Deckelung auf die Quellbitrate.
- **Album-Cover-Deduplizierung**: gemeinsames Cover → eine `cover.jpg`/`.png` im
  Ordner, aus den Opus-Dateien entfernt (FLAC **und** re-encodete Lossy);
  `--no-cover-dedup`.
- **Cover verkleinern** (`--cover-max-size`, ImageMagick) und **entfernen**
  (`--strip-covers`).
- **Spiegel-Aufräumen**, schwellenbewusst und **hybrid** (verwaiste Dateien je Ordner
  sofort, Verzeichnisse/Leerordner am Ende). Standard an; `--no-cleanup`, `--dry-run`.
- **Parallel** über ThreadPool (`--jobs`, Standard 2).
- Robust: Pro-Datei-Fehler brechen den Lauf nicht ab; atomares Schreiben; nur
  Python-3.8-Standardbibliothek, keine pip-Pakete.
- Hilfsskript `fix_covers.py` zum gezielten Nachrüsten fehlender Album-Cover.
- Test-Suite: `python3 -m unittest test_audioshrink`.

### Standardwerte
`--jobs 2` · `--comp 6` · `--reencode-min-bitrate 320` · Re-Encode / Cover-Dedup /
Cleanup jeweils an.

## Entwicklung (0.x)

- **0.1** Grundgerüst: FLAC/WAV/AIFF → Opus, Struktur spiegeln.
- **0.2** Inkrementelle Verarbeitung (Skip nach mtime/Größe), `--force`, `--debug`.
- **0.3** Intelligente Bitrate (ffprobe: Samplerate/Genre/Quellbitrate).
- **0.4** Spiegel-Bereinigung (Cleanup, `--no-cleanup`, `--dry-run`).
- **0.5** Album-Cover-Deduplizierung (FLAC) + Versionsanzeige. — 0.5.1: atomares Schreiben.
- **0.6/0.7** Cover-Optimierung (ImageMagick) + Lossy-Re-Encode (ffmpeg-Dekoder → opusenc).
  — 0.8.1: Cleanup entfernt überholte Nicht-Opus-Ziele.
- **0.8** Parallelisierung (ThreadPool, `--jobs`).
- **0.9.1** Test-Suite (stdlib unittest).
- **0.9.2** Re-Encode-Politik (Bitrate-Schwelle, genre-unabhängig) + Hybrid-Cleanup.
- **0.9.3** Robuste Cover-Extraktion (Bildstream kopieren statt re-encodieren).
- **0.9.6** Cover-Deduplizierung auch für re-encodete Lossy-Alben.
- **0.9.7** Cleanup schwellenbewusst (entfernt verwaiste `.opus` bei angehobener Schwelle).
- Defaults final auf `--comp 6`, `--reencode-min-bitrate 320`, `--jobs 2` gesetzt.
