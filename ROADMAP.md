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
| 0.1 | Grundgerüst (Walking Skeleton) | ✅ fertig |
| 0.2 | Inkrementell & robust | ✅ fertig |
| 0.3 | Intelligente Bitrate | ✅ fertig |
| 0.4 | Spiegel-Bereinigung (Cleanup) | ✅ fertig |
| 0.5 | Album-Cover-Deduplizierung | ✅ fertig |
| 0.6 | Cover-Optimierung & -Entfernung | ✅ fertig (mit 0.7) |
| 0.7 | Lossy-Re-Encode (optional) | ✅ fertig (mit 0.6) |
| 0.8 | Parallelisierung & Performance | ✅ fertig (Standard: 4 Jobs) |
| 0.91 | Tests (stdlib unittest-Suite) | ✅ fertig |
| 0.92 | Re-Encode-Politik (Bitrate-Schwelle) + Hybrid-Cleanup | ✅ fertig |
| 0.95 | README/Doku & Härtung | offen |
| 1.0 | Release | offen |

(0.9x lässt Raum für unterwegs Aufgefallenes – z. B. eine „Ziel-Whitelist", die
Begleitdateien wie `.lrc`/`.jpg` im Ziel auch ohne Quelle verschont.)

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
**Neu:** Encoding über mehrere CPU-Kerne (Worker-Pool), `--jobs N` (**Standard 4**,
passend zur DS718plus), geordnete Logausgabe, Fortschrittsanzeige. **Messung**, ob die
Parallelisierung real Zeit spart.
**Ergebnis:** Große Sammlungen in vertretbarer Zeit.

## 0.91 — Tests (stdlib unittest-Suite)
**Fokus:** Logik absichern, ohne Zusatzpaket.
**Neu:** `test_audioshrink.py` mit `unittest` (kein pytest → läuft per
`python3 -m unittest` überall, auch auf der DS718plus). Deckt die reinen
Logikfunktionen ab: `determine_bitrate`, `is_speech`, `reencode_is_sensible`,
`build_metadata_opts`, `is_up_to_date`, `source_has_counterpart` (inkl.
Re-Encode-Fälle) und `plan_album_cover` (mit gemockten externen Tools).
**Ergebnis:** Regressionen werden früh erkannt.

## 0.92 — Re-Encode-Politik (Bitrate-Schwelle) + Hybrid-Cleanup
**Fokus:** Konsistente Behandlung verlustbehafteter Quellen, inkrementelles Aufräumen.
**Neu:**
- Re-Encode verlustbehafteter Quellen ist jetzt **Standard**; abschaltbar mit
  `--no-reencode-lossy`. Entscheidung **nur über die Quellbitrate** (kein Genre):
  re-encodieren, wenn Bitrate > Schwelle (`--reencode-min-bitrate`, Standard 192),
  sonst kopieren. Die **Zielbitrate/Tuning** bleibt genre-bewusst (Speech → 64 kbps,
  `--speech`). Dieselbe Entscheidung nutzen Lauf und Cleanup.
- Fehlt ffmpeg, wird Re-Encode mit Warnung deaktiviert (Quellen werden kopiert).
- **Hybrid-Cleanup:** verwaiste Dateien direkt nach jedem Ordner, verwaiste
  Verzeichnisse + leere Ordner am Ende. Abgebrochene Läufe lassen fertige Alben
  konsistent zurück.
**Ergebnis:** Kein Format-Churn mehr bei konsistenter Nutzung; Hörbücher (≤192)
werden schnell kopiert statt langsam re-encodiert.

## 0.95 — README/Doku & Härtung
**Fokus:** Bedienbarkeit & Sonderfälle.
**Neu:** README/Bedienungsanleitung, Sonderfälle (Sonderzeichen in Pfaden/Tags,
leere Ordner, konfigurierbare `IGNORE_DIRS`), optional `--ext opus/ogg`, ggf.
Ziel-Whitelist für Begleitdateien.
**Ergebnis:** Dokumentiert und robust.

## 1.0 — Release
**Fokus:** Stabil & verteilbar.
**Neu:** Stabile CLI, `--version`, Packaging (Single-File-Skript oder `pyproject`),
Abschluss-Review, Changelog.
**Ergebnis:** Produktiv einsetzbar.
