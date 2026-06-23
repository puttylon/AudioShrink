# AudioShrink – Sprachunabhängiges Programmkonzept (Pseudocode)

Verbindliche Beschreibung, was AudioShrink tun muss.
Grundlage ist der im README beschriebene Funktionsumfang.
**Zielsprache der Implementierung: Python 3.**

---

## 1. Zweck & Ziel

AudioShrink erzeugt aus einer großen, heterogenen Musiksammlung eine kompakte,
mobile, einheitlich formatierte Kopie für Smartphones, Tablets und portable Player.

- Verlustfreie Quellen (FLAC, WAV, AIFF) werden in effiziente **Opus**-Dateien transkodiert.
- Verlustbehaftete Quellen (MP3, AAC, Opus …) werden **behalten** (kopiert);
  Re-Encoding ist optional und nur, wenn sinnvoll.
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
| Werkzeug-Disziplin | **opusenc ist der einzige Encoder.** ffmpeg wird so wenig wie möglich und **nie zum Encodieren** eingesetzt (siehe Abschnitt 3). |
| Pfade | Über plattformneutrale Pfad-API (`pathlib`-Äquivalent), kein String-Hacking. |
| Robustheit | Jede Datei einzeln in `try/except`; ein Fehler bricht den Gesamtlauf nicht ab. |
| Idempotenz | Wiederholter Lauf verarbeitet nur Neues/Geändertes (inkrementell). |
| Spiegel-Invariante | Relative Zielstruktur ist stets identisch zur Quelle. |

---

## 3. Werkzeug-Einsatz (verbindliche Aufgabenteilung)

| Aufgabe | Werkzeug | Hinweis |
|---|---|---|
| Encoding **aller** Ausgaben nach Opus | **opusenc** | Einziger Encoder. |
| Native Eingaben (FLAC, WAV, AIFF) lesen | **opusenc** direkt | Liest Tags **und** eingebettetes Cover automatisch – kein ffmpeg, kein manuelles Tag-Mapping. |
| Verlustbehaftete Eingaben (MP3, AAC, …) für Re-Encode dekodieren | **ffmpeg** → PCM, via Pipe an opusenc | ffmpeg wird **nur als Dekoder** benutzt; Tags/Cover werden danach über opusenc-Optionen neu gesetzt. |
| Audio-Analyse (Samplerate, Bitrate, Codec, Tags, Dauer) | **ffprobe** | Nur lesend. |
| Cover aus FLAC extrahieren (für Deduplizierung) | **metaflac** `--export-picture-to` | Kein ffmpeg. |
| Cover-Bild verkleinern/optimieren (optional) | **ImageMagick** (`magick`, Fallback `convert`) | Nur bei `--cover-max-size`; ffmpeg dafür **nicht** nötig. |

> opusenc-Eingabeformate sind ausschließlich WAV, AIFF, FLAC, Ogg/FLAC und raw PCM.
> Verlustbehaftete Formate müssen deshalb vor dem Encoden dekodiert werden (ffmpeg-Dekoder).

---

## 4. Programmaufruf (CLI)

```
audioshrink SOURCE_DIR TARGET_DIR [Optionen]
```

| Option                  | Bedeutung                                                                 |
|-------------------------|--------------------------------------------------------------------------|
| `--no-cleanup`          | Verwaiste Ziel-Dateien/-Ordner werden **nicht** gelöscht.                |
| `--dry-run`             | Zeigt alle Aktionen (inkl. Löschungen) nur an, ohne sie auszuführen.      |
| `--force`               | Erzwingt Neuverarbeitung aller Dateien (ignoriert Aktualitätsprüfung).   |
| `--reencode-lossy`      | Verlustbehaftete Quellen ebenfalls nach Opus re-encodieren (sonst kopieren). Benötigt ffmpeg (Dekoder). |
| `--no-cover-dedup`      | Album-Cover **nicht** deduplizieren (eingebettete Cover bleiben in jeder Datei). |
| `--cover-max-size PX`   | Cover (separat & extrahiert) auf max. Kantenlänge PX verkleinern. Benötigt ImageMagick. |
| `--strip-covers`        | Cover/Bilder vollständig entfernen statt mitnehmen.                       |
| `--ext {opus,ogg}`      | Dateiendung der Zieldateien (Default: `opus`).                           |
| `--debug`               | Ausführliche Protokollausgabe.                                           |

Die Bitrate ist **kein** Parameter – sie wird stets automatisch ermittelt (Abschnitt 13).

Fehlende Pflichtparameter / unbekannte Option / nicht existierendes SOURCE_DIR
→ Fehlermeldung + Exit-Code 1.

---

## 5. Konfiguration & Defaults

```
CLEANUP        = true        // verwaiste Ziele löschen; abschaltbar via --no-cleanup
DRY_RUN        = false
FORCE          = false
REENCODE_LOSSY = false       // verlustbehaftete Quellen standardmäßig nur kopieren
DEDUP_COVERS   = true        // identische Album-Cover zu einer cover.jpg zusammenfassen
COVER_MAX_SIZE = none        // px; wenn gesetzt, werden Cover verkleinert (opt-in)
COVER_QUALITY  = 85          // JPEG-Qualität beim Verkleinern (ImageMagick -quality)
STRIP_COVERS   = false
TARGET_EXT     = "opus"
DEBUG          = false

LOSSLESS_FORMATS = ["flac", "wav", "aiff", "aif"]   // opusenc liest diese nativ
LOSSY_FORMATS    = ["mp3", "opus", "ogg", "aac", "m4a", "wma"]
IMAGE_FORMATS    = ["jpg", "jpeg", "png", "gif"]
COVER_FILENAMES  = ["cover.jpg", "folder.jpg", "cover.png", "folder.png"]

SPEECH_GENRES    = ["hörbuch","audiobook","speech","podcast","spoken","hörspiel"]

IGNORE_DIRS   = ["@eaDir"]    // konfigurierbare Ausschlussliste für System-/Cache-Ordner
IGNORE_HIDDEN = true          // Dateien/Ordner, deren Name mit "." beginnt
```

---

## 6. Abhängigkeiten prüfen

```
FUNCTION check_dependencies():
    required = ["opusenc", "ffprobe"]               // Encoder + Analyse
    optional = ["ffmpeg", "metaflac", "magick"]     // siehe unten (magick: oder convert)

    FOR tool IN required:
        IF NOT in_path(tool):
            log(ERROR, "Pflichtabhängigkeit fehlt: " + tool); EXIT 1

    // ffmpeg nur als Lossy-Dekoder nötig
    IF REENCODE_LOSSY AND NOT in_path("ffmpeg"):
        log(ERROR, "ffmpeg wird für --reencode-lossy benötigt (Dekoder)"); EXIT 1

    // ImageMagick nur zum Verkleinern nötig
    IF COVER_MAX_SIZE set AND im_cmd() == none:      // bevorzugt "magick", sonst "convert"
        log(ERROR, "ImageMagick (magick/convert) wird für --cover-max-size benötigt"); EXIT 1

    IF DEDUP_COVERS AND NOT in_path("metaflac"):
        log(WARN, "metaflac fehlt – Cover-Deduplizierung wird übersprungen")
        DEDUP_COVERS = false

FUNCTION im_cmd():
    IF in_path("magick"):  RETURN "magick"           // ImageMagick 7 (bevorzugt)
    IF in_path("convert"): RETURN "convert"           // ImageMagick 6 (Fallback)
    RETURN none
```
Die Werkzeuge müssen im `PATH` verfügbar sein; ihre Installation ist systemabhängig.

---

## 7. Protokollierung (Log pro Schritt)

```
FUNCTION log(level, message):           // INFO | WARN | ERROR | DEBUG
    IF level == DEBUG AND NOT DEBUG: RETURN
    write("[" + now() + "] [" + level + "] " + message)   // stdout; Fehler auch stderr
```
Für jede Datei wird mindestens eine Zeile geschrieben: Aktion + Ergebnis.

---

## 8. Hauptprogramm (Verarbeitung pro Album-Ordner)

Die Cover-Deduplizierung (Abschnitt 9) erfordert eine ordnerweise Betrachtung.
Deshalb ist die Verarbeitungseinheit der **Quell-Ordner** (ein „Album").

```
FUNCTION main(args):
    parse_arguments(args)                 // SOURCE_DIR, TARGET_DIR, Flags
    check_dependencies()

    IF NOT is_directory(SOURCE_DIR):
        log(ERROR, "Quellverzeichnis nicht gefunden: " + SOURCE_DIR); EXIT 1
    ensure_directory(TARGET_DIR)

    log(INFO, "Start | Quelle=" + SOURCE_DIR + " Ziel=" + TARGET_DIR)
    stats = new Stats()

    FOR each (dir, files) IN walk_by_directory(SOURCE_DIR):     // siehe 10
        cover_plan = plan_album_cover(dir, files)              // siehe 9
        FOR each source_file IN files:
            TRY:
                process_file(source_file, cover_plan, stats)  // siehe 11
            CATCH error:
                log(ERROR, "Unerwarteter Fehler bei " + source_file + ": " + error)
                stats.errors += 1
        finalize_album_cover(dir, cover_plan, stats)          // siehe 17

    IF CLEANUP: cleanup_target()                               // siehe 19
    ELSE:       log(INFO, "Aufräumen übersprungen (--no-cleanup)")

    log(INFO, "Fertig | konvertiert=" + stats.converted
            + " kopiert=" + stats.copied
            + " übersprungen=" + stats.skipped
            + " Cover dedupliziert=" + stats.covers_deduped
            + " Fehler=" + stats.errors)
    EXIT (stats.errors > 0 ? 2 : 0)
```

---

## 9. Album-Cover-Planung (Deduplizierung)

Ziel: Statt dasselbe Cover in jede Track-Datei einzubetten, wird **ein** `cover.jpg`
pro Album-Ordner abgelegt und das eingebettete Bild beim Encoden via
opusenc `--discard-pictures` verworfen. Das spart bei z. B. 12 Tracks das
(11-fache) Cover-Volumen.

```
FUNCTION plan_album_cover(dir, files):
    audio = [f IN files WHERE class(f) IN (LOSSLESS, LOSSY)]

    IF STRIP_COVERS:
        RETURN { mode: STRIP }                 // alle Bilder verwerfen, kein cover.jpg

    IF NOT DEDUP_COVERS OR audio is empty:
        RETURN { mode: KEEP_EMBEDDED }         // jede Datei behält ihr Cover

    // (a) Existiert in der Quelle bereits eine separate Cover-Datei?
    existing = first file IN files WHERE name(f) IN COVER_FILENAMES
    IF existing:
        RETURN { mode: DEDUP, source_image: existing, discard_embedded: true }

    // (b) Sonst: haben ALLE Tracks dasselbe eingebettete Cover?
    hashes = [embedded_cover_hash(f) FOR f IN audio]      // metaflac/ffprobe; none = kein Cover
    IF all hashes are equal AND hashes[0] != none:
        RETURN { mode: DEDUP, extract_from: audio[0], discard_embedded: true }

    // (c) Unterschiedliche oder fehlende Cover → nichts deduplizieren
    RETURN { mode: KEEP_EMBEDDED }
```

`cover_args_for(plan)` für opusenc:
```
IF plan.mode IN (STRIP, DEDUP): RETURN "--discard-pictures"   // kein Bild einbetten
ELSE:                           RETURN ""                     // opusenc übernimmt Cover automatisch
```

---

## 10. Quell-Traversierung (ordnerweise und sortiert)

```
FUNCTION walk_by_directory(root):
    FOR each directory dir IN recursive_sortet_dirs(root):
        IF any path part of dir IN IGNORE_DIRS:           SKIP
        IF IGNORE_HIDDEN AND any part starts with ".":    SKIP
        files = [f IN dir
                   WHERE f is regular file
                   AND NOT (IGNORE_HIDDEN AND name(f) starts with ".")]
        IF files not empty: YIELD (dir, sorted(files))
```

---

## 11. Entscheidungsmaschine pro Datei (Kern)

Ablauf je Datei: *analysieren → Samplerate/Bitrate/Codec/Tags lesen →
Entscheidung (behalten / neu encodieren / überspringen) → Encoding → Tags übernehmen → Log.*

```
FUNCTION process_file(source_file, cover_plan, stats):
    relative_path = source_file RELATIVE TO SOURCE_DIR
    ext           = lowercase(extension(source_file))
    file_class    = classify(ext)        // LOSSLESS | LOSSY | IMAGE | OTHER

    // Separate Cover-Datei wird von finalize_album_cover behandelt
    IF file_class == IMAGE AND name(source_file) IN COVER_FILENAMES:
        RETURN

    // ---- Zielpfad & Aktion bestimmen ----
    IF file_class == LOSSLESS OR (file_class == LOSSY AND REENCODE_LOSSY):
        target_file = TARGET_DIR / replace_extension(relative_path, TARGET_EXT)
        action_kind = TRANSCODE
    ELSE:
        target_file = TARGET_DIR / relative_path
        action_kind = COPY

    ensure_directory(parent(target_file))

    // ---- Überspringen, wenn aktuell ----
    IF NOT FORCE AND is_up_to_date(source_file, target_file, action_kind):
        log(DEBUG, "Überspringe (aktuell): " + relative_path)
        stats.skipped += 1; RETURN

    // ---- Audio analysieren ----
    IF file_class IN (LOSSLESS, LOSSY):
        info = analyze_audio(source_file)            // siehe 12

        // Keine Re-Encodes, wenn nicht sinnvoll → zurück auf KOPIEREN
        IF action_kind == TRANSCODE AND file_class == LOSSY AND NOT reencode_is_sensible(info):
            action_kind = COPY
            target_file = TARGET_DIR / relative_path
            ensure_directory(parent(target_file))

    // ---- Ausführen ----
    IF action_kind == TRANSCODE:
        bitrate = determine_bitrate(info)            // siehe 13
        ok = transcode_to_opus(source_file, target_file, bitrate, info, cover_plan)  // siehe 15
        IF ok: stats.converted += 1
    ELSE:
        ok = copy_file(source_file, target_file, file_class)                         // siehe 16
        IF ok: stats.copied += 1

    IF NOT ok: stats.errors += 1
```

```
FUNCTION classify(ext):
    IF ext IN LOSSLESS_FORMATS: RETURN LOSSLESS
    IF ext IN LOSSY_FORMATS:    RETURN LOSSY
    IF ext IN IMAGE_FORMATS:    RETURN IMAGE
    RETURN OTHER

FUNCTION reencode_is_sensible(info):
    // Re-Encode lohnt nur, wenn die Quelle deutlich über der Zielbitrate liegt.
    RETURN info.bitrate_kbps > determine_bitrate(info) * 1.15
```

---

## 12. Audioanalyse

```
FUNCTION analyze_audio(file):
    // via ffprobe (JSON-Ausgabe)
    RETURN {
        codec:        string,    // flac, mp3, opus, aac, pcm_s16le (wav) ...
        sample_rate:  integer,   // Hz
        bitrate_kbps: integer,   // gemeldete Bitrate der Quelle
        duration_s:   float,
        channels:     integer,
        tags:         dict,      // title, artist, album, tracknumber, genre, date, ...
        has_cover:    boolean
    }
```

---

## 13. Intelligente Bitratenwahl (Samplerate, Genre, Quellbitrate)

```
FUNCTION determine_bitrate(info):
    // (a) Basis aus Samplerate (verbindliche Regel)
    IF info.sample_rate >= 96000: base = 160
    ELSE IF info.sample_rate >= 48000: base = 128
    ELSE: base = 96
    bitrate = base

    // (b) Genre: reine Sprachinhalte brauchen weniger
    IF lowercase(info.tags.genre) CONTAINS any SPEECH_GENRES:
        bitrate = min(bitrate, 64)

    // (c) Quellbitrate als Deckel: nie höher ansetzen als die Quelle
    IF info.bitrate_kbps > 0:
        bitrate = min(bitrate, info.bitrate_kbps)

    RETURN bitrate
```
> **Bewusst KEINE Absenkung nach Tracklänge.** Die Stücklänge ist kein Maß für den
> Bitratenbedarf – lange Klassik-/Live-Stücke brauchen eher mehr Detailtreue, nicht weniger.

---

## 14. opusenc-Encoding-Parameter

Verbindliche Encoder-Einstellungen für jeden Opus-Output:

| Parameter        | Wert                | Begründung                                              |
|------------------|---------------------|--------------------------------------------------------|
| `--bitrate N`    | aus Abschnitt 13    | Zielbitrate pro Datei.                                  |
| `--vbr`          | (Default)           | Variable Bitrate = beste Qualität/Größe.               |
| `--music` / `--speech` | nach Genre    | `--speech` bei Sprach-Genres, sonst `--music`.         |
| `--comp 10`      | 10                  | Höchste Encoder-Komplexität = beste Qualität.          |
| `--framesize 20` | 20 ms               | Bewährter Standard für Musik.                          |
| `--quiet`        | –                   | Keine Encoder-Konsolenausgabe (eigenes Logging).       |
| `--discard-pictures` | nur bei STRIP/DEDUP | Kein eingebettetes Cover (siehe Abschnitt 9/17).    |

```
FUNCTION opus_tuning(info):
    IF lowercase(info.tags.genre) CONTAINS any SPEECH_GENRES: RETURN "--speech"
    RETURN "--music"
```

---

## 15. Transkodierung nach Opus

```
FUNCTION transcode_to_opus(source, target, bitrate, info, cover_plan):
    log(INFO, "Konvertiere: " + name(source) + " [" + bitrate + " kbps]")
    tuning     = opus_tuning(info)
    cover_flag = cover_args_for(cover_plan)         // "" oder "--discard-pictures"
    base_opts  = "--bitrate {bitrate} --vbr {tuning} --comp 10 --framesize 20 --quiet {cover_flag}"

    IF class(source) == LOSSLESS:
        // opusenc liest FLAC/WAV/AIFF direkt; Tags + (sofern nicht verworfen) Cover automatisch
        ok = run("opusenc {base_opts} {source} {target}")

    ELSE:   // LOSSY-Re-Encode: ffmpeg DEKODIERT nur, opusenc ENCODIERT
        meta_opts = build_metadata_opts(info)       // --title/--artist/--album/... aus ffprobe
        pic_opts  = build_picture_opts(source, cover_plan)   // --picture nur bei KEEP_EMBEDDED
        ok = run_piped(
                decode = "ffmpeg -i {source} -f wav -",      // PCM nach stdout
                encode = "opusenc {base_opts} {meta_opts} {pic_opts} - {target}")

    IF ok:
        set_mtime(target, mtime(source))            // Aktualitätsprüfung bleibt konsistent
        log(INFO, "Erfolg: " + name(target)); RETURN true
    ELSE:
        log(ERROR, "Konvertierung fehlgeschlagen: " + source)
        delete_if_exists(target); RETURN false      // unvollständige Datei entfernen
```

```
FUNCTION build_metadata_opts(info):
    // Tags, die ffmpeg beim Dekodieren nicht überträgt, werden hier explizit gesetzt
    opts = []
    IF info.tags.title:        opts += ["--title",       info.tags.title]
    IF info.tags.artist:       opts += ["--artist",      info.tags.artist]
    IF info.tags.album:        opts += ["--album",       info.tags.album]
    IF info.tags.tracknumber:  opts += ["--tracknumber", info.tags.tracknumber]
    IF info.tags.genre:        opts += ["--genre",       info.tags.genre]
    IF info.tags.date:         opts += ["--date",        info.tags.date]
    FOR each (k, v) IN remaining_tags(info.tags):
        opts += ["--comment", k + "=" + v]
    RETURN opts

FUNCTION build_picture_opts(source, cover_plan):
    IF cover_plan.mode != KEEP_EMBEDDED: RETURN []     // Cover wird verworfen oder als cover.jpg abgelegt
    img = extract_embedded_cover(source) → tmpfile     // ffmpeg, da Quelle nicht-FLAC
    IF img: RETURN ["--picture", "3||||" + img]        // TYP 3 = Front Cover
    RETURN []
```

---

## 16. Kopieren (behalten – ohne Qualitätsverlust)

```
FUNCTION copy_file(source, target, file_class):
    log(INFO, "Kopiere: " + name(source))
    ok = copy_with_metadata(source, target)
    IF ok:
        set_mtime(target, mtime(source))
        log(INFO, "Erfolg: " + name(target)); RETURN true
    ELSE:
        log(ERROR, "Kopie fehlgeschlagen: " + source)
        delete_if_exists(target); RETURN false
```

---

## 17. Cover-Optimierung & Deduplizierung (Album-Abschluss)

Wird einmal pro Album-Ordner nach der Track-Verarbeitung aufgerufen.

```
FUNCTION finalize_album_cover(dir, cover_plan, stats):
    IF cover_plan.mode != DEDUP: RETURN              // STRIP/KEEP_EMBEDDED: nichts zu tun

    rel        = dir RELATIVE TO SOURCE_DIR
    cover_dest = TARGET_DIR / rel / "cover.jpg"

    IF cover_plan.source_image:                      // separate Cover-Datei lag bereits vor
        src_img = cover_plan.source_image
    ELSE:                                            // Cover aus FLAC extrahieren (metaflac, kein ffmpeg)
        src_img = metaflac_export_picture(cover_plan.extract_from) → tmpfile

    IF NOT src_img: RETURN

    IF COVER_MAX_SIZE set:
        // ImageMagick: nur verkleinern ('>'-Flag), Metadaten strippen, JPEG-Qualität setzen
        run("{im_cmd()} {src_img} -resize '{COVER_MAX_SIZE}x{COVER_MAX_SIZE}>' "
            + "-strip -quality {COVER_QUALITY} {cover_dest}")
    ELSE:
        copy_with_metadata(src_img, cover_dest)

    stats.covers_deduped += 1
    log(INFO, "Album-Cover abgelegt: " + cover_dest)
```

> Eingebettetes Cover wurde bereits beim Encoden via `--discard-pictures` verworfen;
> hier entsteht stattdessen genau **eine** Bilddatei pro Album.

---

## 18. Aktualitätsprüfung (Inkrementell / Überspringen)

```
FUNCTION is_up_to_date(source, target, action_kind):
    IF NOT exists(target): RETURN false
    IF mtime(source) > mtime(target): RETURN false
    IF action_kind == COPY AND size(source) != size(target): RETURN false
    RETURN true
```

---

## 19. Aufräumen / Spiegel bereinigen (Standard an; `--no-cleanup` aus)

```
FUNCTION cleanup_target():
    mode = DRY_RUN ? "DRY-RUN" : "LÖSCHEN"
    log(INFO, "Bereinigung (" + mode + ") ...")

    // 1) Verzeichnisse ohne Quell-Entsprechung
    FOR dir IN dirs_under(TARGET_DIR):
        IF dir under IGNORE_DIRS: CONTINUE
        rel = dir RELATIVE TO TARGET_DIR
        IF NOT is_directory(SOURCE_DIR / rel):
            remove_dir_recursive_or_log(dir)

    // 2) Dateien ohne Quell-Entsprechung
    FOR file IN files_under(TARGET_DIR):
        IF file under IGNORE_DIRS: CONTINUE
        rel = file RELATIVE TO TARGET_DIR
        ext = lowercase(extension(file))

        // Generiertes Album-Cover behalten, solange der Quell-Ordner existiert
        IF name(file) IN COVER_FILENAMES AND is_directory(SOURCE_DIR / parent(rel)):
            CONTINUE

        IF ext == TARGET_EXT:
            // Opus-Ziel kann aus verlustfreier ODER (bei --reencode) verlustbeh. Quelle stammen
            base = rel WITHOUT extension
            candidates = LOSSLESS_FORMATS + [TARGET_EXT] + (REENCODE_LOSSY ? LOSSY_FORMATS : [])
            exists_in_source = any(exists(SOURCE_DIR / (base + "." + e)) FOR e IN candidates)
        ELSE:
            exists_in_source = exists(SOURCE_DIR / rel)

        IF NOT exists_in_source:
            remove_file_or_log(file)            // "keine Quelle"

    // 3) Leere Verzeichnisse entfernen
    FOR dir IN empty_dirs_under(TARGET_DIR):
        IF dir under IGNORE_DIRS: CONTINUE
        remove_empty_dir_or_log(dir)

    log(INFO, "Bereinigung abgeschlossen")
```
`remove_*_or_log`: führt bei `DRY_RUN=false` die Löschung aus, sonst nur Log-Eintrag.

---

## 20. Unterstützte Formate & Behandlung

| Format            | Klasse   | Standardaktion                         |
|-------------------|----------|----------------------------------------|
| FLAC, WAV, AIFF   | LOSSLESS | → Opus transkodieren (opusenc direkt)  |
| MP3, AAC, M4A, WMA, OGG, Opus | LOSSY | Behalten (kopieren); mit `--reencode-lossy` und wenn sinnvoll → Opus (ffmpeg dekodiert, opusenc encodiert) |
| JPG, PNG, GIF     | IMAGE    | Separate Cover via Deduplizierung; sonst kopieren/verkleinern/entfernen |
| Alles andere      | OTHER    | Kopieren (versteckte/ignorierte ausgenommen) |

---

## 21. Verzeichnisstruktur-Invariante

```
SOURCE/Künstler/Album/01 - Track.flac   →   TARGET/Künstler/Album/01 - Track.opus   (transkodiert, Cover verworfen)
SOURCE/Künstler/Album/02 - Track.flac   →   TARGET/Künstler/Album/02 - Track.opus   (transkodiert, Cover verworfen)
(eingebettete, identische Cover)        →   TARGET/Künstler/Album/cover.jpg         (einmalig, dedupliziert)
SOURCE/Künstler/Album/03 - Live.mp3     →   TARGET/Künstler/Album/03 - Live.mp3     (behalten)
```

---

## 22. Exit-Codes

| Code | Bedeutung                                                |
|------|---------------------------------------------------------|
| 0    | Erfolgreich, keine Fehler                               |
| 1    | Start-/Konfigurationsfehler (Parameter, Abhängigkeit, Quelle) |
| 2    | Lauf beendet, aber einzelne Dateien fehlerhaft          |
```
