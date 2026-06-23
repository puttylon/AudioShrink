# AudioShrink – Roadmap 0.1 → 1.0

Inkrementelle Umsetzung des in [CONCEPT.md](CONCEPT.md) beschriebenen Programms.
Leitidee: **Jede Version ist lauffähig und end-to-end nutzbar** – Funktionalität
wird auf einem von Anfang an funktionierenden Skelett gestapelt.

## Festlegungen

- **Sprache:** Python 3
- **Mindestversion:** **Python 3.8** (deckt reale NAS-Systeme ab; keine 3.9-only-Features wie `str.removesuffix`)
- **Encoder:** ausschließlich `opusenc`; `ffmpeg` nie zum Encodieren (nur Lossy-Dekoder)
- **0.1:** feste Bitrate 128 kbps (intelligente Bitrate erst ab 0.3)
- **Parallelisierung:** erst in 0.8 – inkl. Messung, ob sie real etwas bringt

## Status

| Version | Thema | Status |
|---------|-------|--------|
| 0.1 | Grundgerüst (Walking Skeleton) | 🔨 in Arbeit |
| 0.2 | Inkrementell & robust | offen |
| 0.3 | Intelligente Bitrate | offen |
| 0.4 | Spiegel-Bereinigung (Cleanup) | offen |
| 0.5 | Album-Cover-Deduplizierung | offen |
| 0.6 | Cover-Optimierung & -Entfernung | offen |
| 0.7 | Lossy-Re-Encode (optional) | offen |
| 0.8 | Parallelisierung & Performance | offen |
| 0.9 | Härtung, Tests & Doku | offen |
| 1.0 | Release | offen |

---

## 0.1 — Grundgerüst (Walking Skeleton)
**Fokus:** Kompletter Durchstich, minimal.
**Neu:** CLI (`SOURCE TARGET`), Abhängigkeitsprüfung (opusenc), rekursive Traversierung,
Struktur spiegeln, FLAC/WAV/AIFF → Opus (feste Bitrate 128, `--vbr --music`), alles
andere kopieren, einfaches Logging.
**Ergebnis:** Eine FLAC-Sammlung wird real geschrumpft.
**Noch nicht:** Skip-Logik, Bitrate-Intelligenz, Cleanup, Cover, ffprobe.

## 0.2 — Inkrementell & robust
**Fokus:** Wiederholbar & fehlertolerant.
**Neu:** Aktualitätsprüfung (mtime/Größe) → Überspringen, `--force`, `try/except` pro
Datei, Fehlerzähler, Exit-Codes (0/1/2), `--debug`.
**Ergebnis:** Zweiter Lauf ist schnell; ein kaputter Track bricht nicht alles ab.

## 0.3 — Intelligente Bitrate
**Fokus:** Qualität/Größe pro Datei.
**Neu:** `ffprobe`-Analyse (Samplerate, Bitrate, Genre, Codec), Samplerate-Regel
(160/128/96), Quellbitrate-Deckel, `--speech`/`--music` nach Genre.
**Ergebnis:** Jede Datei bekommt die passende Bitrate. **Bewusst keine** Tracklängen-Absenkung.

## 0.4 — Spiegel-Bereinigung (Cleanup)
**Fokus:** Echter Spiegel inkl. Löschungen.
**Neu:** `cleanup_target` (verwaiste Dateien/Ordner), Standard **an**, `--no-cleanup`,
`--dry-run`, Rückabbildung Opus→Quellendungen.
**Ergebnis:** Gelöschte Quellen verschwinden auch im Ziel; `--dry-run` zeigt es gefahrlos.

## 0.5 — Album-Cover-Deduplizierung
**Fokus:** Die Albenverarbeitung.
**Neu:** Umstellung auf **ordnerweise** Verarbeitung, `plan_album_cover`/
`finalize_album_cover`, Hash-Vergleich „alle Tracks gleiches Cover?", `metaflac`-Extraktion,
`--discard-pictures`, ein `cover.jpg` pro Album, `--no-cover-dedup`, Cover-Schutz im Cleanup.
**Ergebnis:** Cover nur noch einmal pro Album statt in jeder Datei – spürbarer Platzgewinn.

## 0.6 — Cover-Optimierung & -Entfernung
**Fokus:** Bildgröße steuern.
**Neu:** `--cover-max-size` (ImageMagick `magick`/`convert`, `-resize 'NxN>' -strip -quality`),
`COVER_QUALITY`, `--strip-covers`.
**Ergebnis:** Cover werden verkleinert oder ganz entfernt.

## 0.7 — Lossy-Re-Encode (optional)
**Fokus:** Der komplexeste Pfad.
**Neu:** `--reencode-lossy`, `reencode_is_sensible`, ffmpeg-**Dekoder** → Pipe → opusenc,
Tag-/Cover-Neuaufbau via ffprobe → opusenc-Optionen.
**Ergebnis:** Auch große MP3/AAC können geschrumpft werden – verlustfreie Metadaten,
ffmpeg nur als Dekoder.

## 0.8 — Parallelisierung & Performance
**Fokus:** Tauglich für 300–500 GB.
**Neu:** Encoding über mehrere CPU-Kerne (Worker-Pool), `--jobs N`, geordnete Logausgabe,
Fortschrittsanzeige. **Messung**, ob die Parallelisierung real Zeit spart.
**Ergebnis:** Große Sammlungen in vertretbarer Zeit.

## 0.9 — Härtung, Tests & Doku
**Fokus:** Verlässlichkeit.
**Neu:** Test-Suite (pytest, kleine Fixtures), Sonderfälle (Sonderzeichen in Pfaden/Tags,
leere Ordner, konfigurierbare `IGNORE_DIRS`), `--ext opus/ogg`, README/Hilfetext.
**Ergebnis:** Abgesichert und dokumentiert.

## 1.0 — Release
**Fokus:** Stabil & verteilbar.
**Neu:** Stabile CLI, `--version`, Packaging (Single-File-Skript oder `pyproject`),
Abschluss-Review, Changelog.
**Ergebnis:** Produktiv einsetzbar.
