# AudioShrink – Sprachunabhängiges Programmkonzept (Pseudocode)

Verbindliche Beschreibung, was AudioShrink tun muss.
Grundlage ist der im README beschriebene Funktionsumfang.
**Zielsprache der Implementierung: Python 3.**

---

## 1. Zweck & Ziel

AudioShrink erzeugt aus einer großen, heterogenen Musiksammlung eine kompakte,
mobile, einheitlich formatierte Kopie für Smartphones, Tablets und portable Player.

- Verlustfreie Quellen (FLAC, WAV) werden in effiziente **Opus**-Dateien transkodiert.
- Verlustbehaftete Quellen (MP3, AAC, Opus …) werden **behalten** (kopiert) –
  Re-Encoding nur optional und nur, wenn sinnvoll.
- Die Verzeichnisstruktur wird **exakt gespiegelt**.

**Mengengerüst:**
- Ergebnis ist typ. 10–20 % der Ausgangsgröße ohne hörbare Qualitätsverluste.
- Beispiel: 500 GB FLAC → 60–80 GB Opus (96–128 kbps).
- Beispiel: 300 GB gemischt → 40–50 GB Opus.

---

## 2. Designprinzipien

| Prinzip | Festlegung |
|---|---|
| Portabilität | Läuft auf beliebigen Linux-Distributionen (glibc/musl), unabhängig von der Shell. Nur Python-3-**Standardbibliothek**, keine pip-Pakete nötig. |
| Externe Werkzeuge | `ffprobe`, `opusenc`, `ffmpeg`, `metaflac` werden über `subprocess` aufgerufen. |
| Pfade | Über plattformneutrale Pfad-API (`pathlib`-Äquivalent), kein String-Hacking. |
| Robustheit | Jede Datei einzeln in `try/except`; ein Fehler bricht den Gesamtlauf nicht ab. |
| Idempotenz | Wiederholter Lauf verarbeitet nur Neues/Geändertes (inkrementell). |
| Spiegel-Invariante | Relative Zielstruktur ist stets identisch zur Quelle. |

---

## 3. Programmaufruf (CLI)

```
audioshrink SOURCE_DIR TARGET_DIR [Optionen]
```

| Option                  | Bedeutung                                                                 |
|-------------------------|--------------------------------------------------------------------------|
| `--no-cleanup`          | Verwaiste Ziel-Dateien/-Ordner werden **nicht** gelöscht.                |
| `--dry-run`             | Zeigt alle Aktionen (inkl. Löschungen) nur an, ohne sie auszuführen.      |
| `--force`               | Erzwingt Neuverarbeitung aller Dateien (ignoriert Aktualitätsprüfung).   |
| `--reencode-lossy`      | Auch verlustbehaftete Quellen nach Opus re-encodieren (sonst kopieren).  |
| `--cover-max-size PX`   | Eingebettete & separate Cover auf max. Kantenlänge PX verkleinern.       |
| `--strip-covers`        | Cover/Bilder vollständig entfernen statt mitnehmen.                       |
| `--ext {opus,ogg}`      | Dateiendung der Zieldateien (Default: `opus`).                           |
| `--debug`               | Ausführliche Protokollausgabe.                                           |

Die Bitrate ist **kein** Parameter – sie wird stets automatisch ermittelt (Abschnitt 11).

Fehlende Pflichtparameter / unbekannte Option / nicht existierendes SOURCE_DIR
→ Fehlermeldung + Exit-Code 1.

---

## 4. Konfiguration & Defaults

```
CLEANUP        = true        // verwaiste Ziele löschen; abschaltbar via --no-cleanup
DRY_RUN        = false
FORCE          = false
REENCODE_LOSSY = false       // verlustbehaftete Quellen standardmäßig nur kopieren
TARGET_EXT     = "opus"
COVER_MAX_SIZE = 600         // px; eingebettete Cover über dieser Größe werden verkleinert
STRIP_COVERS   = false
DEBUG          = false

LOSSLESS_FORMATS = ["flac", "wav", "aiff", "aif"]
LOSSY_FORMATS    = ["mp3", "opus", "ogg", "aac", "m4a", "wma"]
IMAGE_FORMATS    = ["jpg", "jpeg", "png"]

IGNORE_DIRS   = ["@eaDir"]    // konfigurierbare Ausschlussliste für System-/Cache-Ordner
IGNORE_HIDDEN = true          // Dateien/Ordner, deren Name mit "." beginnt
```

---

## 5. Abhängigkeiten prüfen

```
FUNCTION check_dependencies():
    required = ["ffprobe", "opusenc"]
    optional = ["ffmpeg",  "metaflac"]   // ffmpeg: Re-Encode & Cover-Resize; metaflac: FLAC-Tags

    FOR tool IN required:
        IF NOT in_path(tool):
            log(ERROR, "Pflichtabhängigkeit fehlt: " + tool); EXIT 1

    FOR tool IN optional:
        IF NOT in_path(tool):
            log(WARN, "Optionale Abhängigkeit fehlt, Funktion eingeschränkt: " + tool)
```
Die Werkzeuge müssen im `PATH` verfügbar sein; ihre Installation ist systemabhängig.

---

## 6. Protokollierung (Log pro Schritt)

```
FUNCTION log(level, message):           // INFO | WARN | ERROR | DEBUG
    IF level == DEBUG AND NOT DEBUG: RETURN
    write("[" + now() + "] [" + level + "] " + message)   // stdout; Fehler auch stderr
```
Für jede Datei wird mindestens eine Zeile geschrieben: Aktion + Ergebnis.

---

## 7. Hauptprogramm

```
FUNCTION main(args):
    parse_arguments(args)                 // SOURCE_DIR, TARGET_DIR, Flags
    check_dependencies()

    IF NOT is_directory(SOURCE_DIR):
        log(ERROR, "Quellverzeichnis nicht gefunden: " + SOURCE_DIR); EXIT 1
    ensure_directory(TARGET_DIR)

    log(INFO, "Start | Quelle=" + SOURCE_DIR + " Ziel=" + TARGET_DIR)

    stats = new Stats()

    FOR each source_file IN walk_source(SOURCE_DIR):     // siehe 8
        TRY:
            process_file(source_file, stats)             // siehe 9 (Entscheidungsmaschine)
        CATCH error:
            log(ERROR, "Unerwarteter Fehler bei " + source_file + ": " + error)
            stats.errors += 1

    IF CLEANUP:
        cleanup_target()                                  // siehe 16
    ELSE:
        log(INFO, "Aufräumen übersprungen (--no-cleanup)")

    log(INFO, "Fertig | konvertiert=" + stats.converted
            + " kopiert=" + stats.copied
            + " übersprungen=" + stats.skipped
            + " Fehler=" + stats.errors)
    EXIT (stats.errors > 0 ? 2 : 0)
```

---

## 8. Quell-Traversierung

```
FUNCTION walk_source(root):
    FOR each entry IN recursive_walk(root):
        IF any(part of entry.path IN IGNORE_DIRS):           SKIP
        IF IGNORE_HIDDEN AND any(part starts with "."):      SKIP
        IF entry is regular file:                            YIELD entry
```

---

## 9. Entscheidungsmaschine pro Datei (Kern)

Ablauf je Datei: *analysieren → Samplerate/Bitrate/Codec/Tags lesen →
Entscheidung (behalten / neu encodieren / überspringen) → Encoding → Tags übernehmen → Log.*

```
FUNCTION process_file(source_file, stats):
    relative_path = source_file RELATIVE TO SOURCE_DIR
    ext           = lowercase(extension(source_file))
    file_class    = classify(ext)        // LOSSLESS | LOSSY | IMAGE | OTHER

    // ---- Zielpfad bestimmen ----
    IF file_class == LOSSLESS OR (file_class == LOSSY AND REENCODE_LOSSY):
        target_file = TARGET_DIR / replace_extension(relative_path, TARGET_EXT)
        action_kind = TRANSCODE
    ELSE:
        target_file = TARGET_DIR / relative_path
        action_kind = COPY

    ensure_directory(parent(target_file))

    // ---- Entscheidung: ÜBERSPRINGEN? ----
    IF NOT FORCE AND is_up_to_date(source_file, target_file, action_kind):
        log(DEBUG, "Überspringe (aktuell): " + relative_path)
        stats.skipped += 1
        RETURN

    // ---- Analyse (nur für Audio) ----
    IF file_class IN [LOSSLESS, LOSSY]:
        info = analyze_audio(source_file)            // siehe 10

        // Keine Re-Encodes, wenn nicht sinnvoll
        IF action_kind == TRANSCODE AND file_class == LOSSY:
            IF NOT reencode_is_sensible(info):
                action_kind = COPY
                target_file = TARGET_DIR / relative_path
                ensure_directory(parent(target_file))

    // ---- Ausführen ----
    IF action_kind == TRANSCODE:
        bitrate = determine_bitrate(info)            // siehe 11
        ok = transcode_to_opus(source_file, target_file, bitrate, info)   // siehe 12
        IF ok: stats.converted += 1
    ELSE:
        ok = copy_file(source_file, target_file, file_class)              // siehe 13
        IF ok: stats.copied += 1

    IF NOT ok: stats.errors += 1
```

```
FUNCTION classify(ext):
    IF ext IN LOSSLESS_FORMATS: RETURN LOSSLESS
    IF ext IN LOSSY_FORMATS:    RETURN LOSSY
    IF ext IN IMAGE_FORMATS:    RETURN IMAGE
    RETURN OTHER
```

```
FUNCTION reencode_is_sensible(info):
    // Re-Encode lohnt nur, wenn die Quelle deutlich über der Zielbitrate liegt.
    target = determine_bitrate(info)
    RETURN info.bitrate_kbps > target * 1.15      // sonst: keine Qualität "gewinnen", nur verlieren
```

---

## 10. Audioanalyse

```
FUNCTION analyze_audio(file):
    // via ffprobe (JSON-Ausgabe)
    RETURN {
        codec:        string,    // flac, mp3, opus, aac, pcm_s16le (wav) ...
        sample_rate:  integer,   // Hz
        bitrate_kbps: integer,   // gemeldete Bitrate der Quelle
        duration_s:   float,
        channels:     integer,
        tags:         dict,      // title, artist, album, genre, ...
        has_cover:    boolean
    }
```
Optional: FLAC-Tags ergänzend über `metaflac` lesen, falls vorhanden.

---

## 11. Intelligente Bitratenwahl (Samplerate, Genre, Dateigröße, Tracklänge)

```
FUNCTION determine_bitrate(info):
    // (a) Basis aus Samplerate (verbindliche Regel)
    IF info.sample_rate >= 96000: base = 160
    ELSE IF info.sample_rate >= 48000: base = 128
    ELSE: base = 96

    bitrate = base

    // (b) Genre-Verfeinerung: Sprachinhalte brauchen weniger
    genre = lowercase(info.tags.genre)
    IF genre CONTAINS any ["hörbuch","audiobook","speech","podcast","spoken","hörspiel"]:
        bitrate = min(bitrate, 64)

    // Kommentar: Gerade Klassik würde ich NICHT absenken. Wieso absenken nur weil das Stück lang ist? Verwerfen.
    // (c) Tracklänge: sehr lange Stücke (Mixes/Klassik) moderat absenken
    IF info.duration_s > 1800:                      // > 30 min
        bitrate = max(round(bitrate * 0.9), 96)

    // (d) Dateigröße/Quellbitrate als Deckel: nie höher als die Quelle ansetzen
    IF info.bitrate_kbps > 0:
        bitrate = min(bitrate, info.bitrate_kbps)

    RETURN bitrate
```
> Basisregel (a) ist verbindlich. (b)–(d) sind die zusätzlichen Faktoren;
> ihre Schwellen sind dokumentierte Defaults und anpassbar.

---

## 12. Transkodierung nach Opus (inkl. Tag- & Cover-Übernahme)

```
FUNCTION transcode_to_opus(source, target, bitrate, info):
    log(INFO, "Konvertiere: " + name(source) + " [" + bitrate + " kbps]")

    cover_args = build_cover_args(info)             // siehe 14

    IF codec_of(source) IN [flac, wav, aiff]:
        // opusenc übernimmt Tags & eingebettetes Cover automatisch
        ok = run("opusenc --bitrate {bitrate} --vbr --music --framesize 20 --quiet "
                 + cover_args + " {source} {target}")
    ELSE:
        // Kommentar:  kann das nicht auch opusenc durchgeführt werden? ffmpeg verliert die Metadaten; ich will ffmpeg so wenig wie möglich im einsatz haben!
        // optionaler Lossy-Re-Encode-Pfad: ffmpeg + libopus, Metadaten mappen
        ok = run("ffmpeg -i {source} -c:a libopus -b:a {bitrate}k -vbr on "
                 + "-map_metadata 0 " + cover_args + " -y {target}")

    IF ok:
        set_mtime(target, mtime(source))            // Aktualitätsprüfung bleibt konsistent
        log(INFO, "Erfolg: " + name(target))
        RETURN true
    ELSE:
        log(ERROR, "Konvertierung fehlgeschlagen: " + source)
        delete_if_exists(target)                    // unvollständige Datei entfernen
        RETURN false
```

---

## 13. Kopieren (behalten – ohne Qualitätsverlust)

```
FUNCTION copy_file(source, target, file_class):
    IF file_class == IMAGE AND (STRIP_COVERS OR COVER_MAX_SIZE set):
        RETURN process_standalone_cover(source, target)   // siehe 14

    log(INFO, "Kopiere: " + name(source))
    ok = copy_with_metadata(source, target)
    IF ok:
        set_mtime(target, mtime(source))
        log(INFO, "Erfolg: " + name(target))
        RETURN true
    ELSE:
        log(ERROR, "Kopie fehlgeschlagen: " + source)
        delete_if_exists(target)
        RETURN false
```

---

## 14. Cover-Optimierung (verkleinern oder entfernen)

```
FUNCTION build_cover_args(info):
    IF STRIP_COVERS:                 RETURN flag_to_drop_embedded_cover()   // kein Cover einbetten
    IF COVER_MAX_SIZE set:           RETURN flag_to_downscale_cover(COVER_MAX_SIZE)
    RETURN default_keep_cover()

FUNCTION process_standalone_cover(source, target):     // cover.jpg / folder.png etc.
    IF STRIP_COVERS:
        log(INFO, "Cover entfernt (nicht kopiert): " + name(source)); RETURN true
    IF COVER_MAX_SIZE set:
        // Verkleinern via ffmpeg (kein zusätzliches Python-Bildpaket nötig)
        ok = run("ffmpeg -i {source} -vf scale='min({MAX},iw)':-1 -y {target}")
        RETURN ok
    RETURN copy_with_metadata(source, target)
```
> Bildverkleinerung nutzt **ffmpeg** statt eines Python-Bildpakets, um die
> Abhängigkeiten minimal zu halten (Portabilitätsziel).
// Kommentar: eine sehr effektive Cover-Optimierung kann darin bestehen ein einzelnes cover.jpg oder folder.jpg im Albumpfad zu haben, sofern die einzelnen Tracks nicht unterschiedliche haben. Man kann also (wenn alle dasselbe cover in sich haben) ein cover als cover.jpg speichern. die opusenc option  --discard-pictures verhindert das kopieren der bilddaten! Anschließend könnte man die Bilddaten noch verkleinern falls sinnvoll.  

---

## 15. Aktualitätsprüfung (Inkrementell / Überspringen)

```
FUNCTION is_up_to_date(source, target, action_kind):
    IF NOT exists(target): RETURN false
    IF mtime(source) > mtime(target): RETURN false
    IF action_kind == COPY AND size(source) != size(target): RETURN false
    RETURN true
```

---

## 16. Aufräumen / Spiegel bereinigen (Standard an; `--no-cleanup` aus)

```
FUNCTION cleanup_target():
    mode = DRY_RUN ? "DRY-RUN" : "LÖSCHEN"
    log(INFO, "Bereinigung (" + mode + ") ...")

    // 1) Verzeichnisse ohne Quell-Entsprechung
    FOR dir IN dirs_under(TARGET_DIR):
        IF dir in IGNORE_DIRS or under ignored: CONTINUE
        rel = dir RELATIVE TO TARGET_DIR
        IF NOT is_directory(SOURCE_DIR / rel):
            remove_dir_recursive_or_log(dir)

    // 2) Dateien ohne Quell-Entsprechung
    FOR file IN files_under(TARGET_DIR):
        IF under ignored: CONTINUE
        rel = file RELATIVE TO TARGET_DIR
        ext = lowercase(extension(file))

        IF ext == TARGET_EXT:
            // Opus-Ziel kann aus einer verlustfreien ODER (bei --reencode) verlustbeh. Quelle stammen
            base = rel WITHOUT extension
            exists_in_source = any(
                exists(SOURCE_DIR / (base + "." + e))
                FOR e IN LOSSLESS_FORMATS + (REENCODE_LOSSY ? LOSSY_FORMATS : []) + [TARGET_EXT]
            )
        ELSE:
            exists_in_source = exists(SOURCE_DIR / rel)

        IF NOT exists_in_source:
            remove_file_or_log(file)            // "keine Quelle"

    // 3) Leere Verzeichnisse entfernen
    FOR dir IN empty_dirs_under(TARGET_DIR):
        IF dir in IGNORE_DIRS or under ignored: CONTINUE
        remove_empty_dir_or_log(dir)

    log(INFO, "Bereinigung abgeschlossen")
```
`remove_*_or_log`: führt bei `DRY_RUN=false` die Löschung aus, sonst nur Log-Eintrag.

---

## 17. Unterstützte Formate & Behandlung

| Format            | Klasse   | Standardaktion                         |
|-------------------|----------|----------------------------------------|
| FLAC, WAV, AIFF   | LOSSLESS | → Opus transkodieren                   |
| MP3, AAC, M4A, WMA, OGG, Opus | LOSSY | Behalten (kopieren); Re-Encode nur mit `--reencode-lossy` und wenn sinnvoll |
| JPG, PNG          | IMAGE    | Kopieren; optional verkleinern/entfernen |
| Alles andere      | OTHER    | Kopieren (versteckte/ignorierte ausgenommen) |

---

## 18. Verzeichnisstruktur-Invariante

```
SOURCE/Künstler/Album/01 - Track.flac   →   TARGET/Künstler/Album/01 - Track.opus   (transkodiert)
SOURCE/Künstler/Album/cover.jpg         →   TARGET/Künstler/Album/cover.jpg         (kopiert/optim.)
SOURCE/Künstler/Album/02 - Live.mp3     →   TARGET/Künstler/Album/02 - Live.mp3     (behalten)
```

---

## 19. Exit-Codes

| Code | Bedeutung                                                |
|------|---------------------------------------------------------|
| 0    | Erfolgreich, keine Fehler                               |
| 1    | Start-/Konfigurationsfehler (Parameter, Abhängigkeit, Quelle) |
| 2    | Lauf beendet, aber einzelne Dateien fehlerhaft          |
```
