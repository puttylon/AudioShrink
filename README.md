# AudioShrink

Verkleinert eine große, gemischte Musiksammlung in eine kompakte, einheitlich
formatierte **Opus**-Spiegelkopie – ideal für Smartphone, Tablet oder Player.

- Verlustfreie Quellen (FLAC, WAV, AIFF) → Opus transkodiert.
- Verlustbehaftete Quellen (MP3, AAC, …) → kopiert; nur hochbitratige werden
  re-encodiert (Schwelle einstellbar).
- Die Verzeichnisstruktur wird **exakt gespiegelt**; der Lauf ist **wiederholbar**
  (nur Neues/Geändertes wird verarbeitet).

Design und Hintergrund: siehe [CONCEPT.md](CONCEPT.md).

---

## Voraussetzungen

Externe Werkzeuge müssen im `PATH` liegen (Installation systemabhängig):

| Werkzeug | Pflicht? | Wofür |
|---|---|---|
| `opusenc` (opus-tools) | **ja** | Einziger Encoder (alles → Opus) |
| `ffprobe` (ffmpeg)     | **ja** | Audio-Analyse (Samplerate, Bitrate, Tags) |
| `ffmpeg`               | nur bei `--reencode-lossy` (Standard an) | Dekodiert Lossy-Quellen + extrahiert Cover. Fehlt es, wird Re-Encode mit Warnung deaktiviert. |
| `metaflac` (flac)      | optional | Cover-Extraktion aus FLAC für die Deduplizierung |
| ImageMagick (`magick`/`convert`) | nur bei `--cover-max-size` | Cover verkleinern |

Python **3.8+** (nur Standardbibliothek, keine pip-Pakete).

---

## Aufruf

```sh
audioshrink.py QUELLE ZIEL [Optionen]
```

Beispiel (entspricht den aktuellen Standardwerten):
```sh
./audioshrink.py /volume1/music/musik /volume1/music/musik_ogg
# = --jobs 2 --comp 6 --reencode-min-bitrate 320
```

### Optionen

| Option | Default | Bedeutung |
|---|---|---|
| `--jobs N` | `2` | Parallele Konvertierungen (Kerne für andere NAS-Aufgaben frei lassen). |
| `--comp 0..10` | `6` | opusenc-Komplexität (10 = beste/langsamste, kleiner = schneller). |
| `--reencode-min-bitrate KBPS` | `320` | Verlustbehaftete Quellen **über** dieser Bitrate werden re-encodiert, darunter kopiert. |
| `--no-reencode-lossy` | – | Verlustbehaftete Quellen **immer** kopieren (kein Re-Encode). |
| `--no-cover-dedup` | – | Album-Cover **nicht** deduplizieren (eingebettete Cover bleiben je Datei). |
| `--cover-max-size PX` | – | Cover auf max. Kantenlänge verkleinern (ImageMagick). |
| `--strip-covers` | – | Cover/Bilder vollständig entfernen. |
| `--no-cleanup` | – | Verwaiste Ziel-Dateien/-Ordner **nicht** löschen. |
| `--dry-run` | – | Alle Aktionen (inkl. Löschungen) nur anzeigen, nichts ausführen. |
| `--force` | – | Alles neu verarbeiten (Aktualitätsprüfung ignorieren). |
| `--debug` | – | Ausführliche Ausgabe (zeigt u. a. übersprungene Dateien). |
| `--version` | – | Version ausgeben. |

---

## Wie entschieden wird

**Pro Datei:**

| Quelle | Aktion |
|---|---|
| FLAC / WAV / AIFF | → Opus transkodieren |
| MP3 / AAC / M4A / WMA / OGG / Opus | Bitrate **> Schwelle** → Re-Encode nach Opus; sonst **kopieren** |
| Bilder (jpg/png/…) | kopieren; optional verkleinern (`--cover-max-size`) oder entfernen (`--strip-covers`) |
| alles andere | kopieren |

**Zielbitrate (beim Encoden):** aus der Samplerate – ≥ 96 kHz → 160, ≥ 48 kHz → 128,
sonst 96 kbps; bei Sprach-Genres (Hörbuch/Podcast/…) auf **64 kbps + `--speech`**.
Nie höher als die Quellbitrate.

> Wichtig: Die **Re-Encode-Entscheidung** hängt **nur an der Bitrate** (kein Genre).
> Ein 320er-Hörbuch wird also re-encodiert (zu 64 kbps Opus), ein 128er kopiert.

**Album-Cover-Deduplizierung (Standard an):** Haben alle transkodierten Tracks eines
Ordners dasselbe eingebettete Cover (oder liegt eine `cover.jpg`/`folder.jpg` vor),
wird **ein** `cover.jpg`/`.png` im Ordner abgelegt und das Bild aus den Opus-Dateien
entfernt. Unterschiedliche Cover bleiben je Track erhalten.

**Aufräumen (Spiegel, Standard an):** Was im Ziel keine Entsprechung in der Quelle
hat, wird entfernt – Audiodateien im falschen Format (z. B. ein `.opus`, das nun als
`.mp3` kopiert würde) sowie Dateien/Ordner ohne Quelle. Bereits vorhandene
Begleitdateien (`.lrc`, Cover …) bleiben, solange sie in der Quelle existieren.
Das Aufräumen erfolgt **hybrid**: verwaiste Dateien direkt nach jedem Ordner,
verwaiste Verzeichnisse + leere Ordner am Ende. Ein abgebrochener Lauf hinterlässt
fertige Alben konsistent.

**Inkrementell:** Aktuelle Ziele (gleiche mtime, bei Kopien zusätzlich Größe) werden
übersprungen. Der Lauf ist damit **fortsetzbar** – nach Abbruch einfach erneut starten.
`--force` erzwingt Neuverarbeitung.

---

## Exit-Codes

| Code | Bedeutung |
|---|---|
| 0 | Erfolgreich, keine Fehler |
| 1 | Start-/Konfigurationsfehler (Parameter, fehlende Abhängigkeit, Quelle) |
| 2 | Lauf beendet, aber einzelne Dateien fehlerhaft |

---

## Hilfsskript: fix_covers.py

Findet im Ziel Album-Ordner, die `.opus` enthalten, aber **gar kein** Cover haben
(weder `cover.jpg` noch eingebettet – Altbestand früherer Versionen), und erzeugt
sie gezielt mit `audioshrink --force` neu. Den Quellordner leitet es aus dem Zielpfad
ab (Endung `_ogg` der Wurzel entfernen, z. B. `…/musik_ogg` → `…/musik`).

```sh
./fix_covers.py /volume1/music/musik_ogg            # nur auflisten (sicher)
./fix_covers.py /volume1/music/musik_ogg --apply    # tatsächlich neu erzeugen
./fix_covers.py /volume1/music/musik_ogg --source-root /pfad/zur/quelle --apply
```

Beide Skripte müssen im selben Ordner liegen (`fix_covers` nutzt `audioshrink` als Modul).

---

## Hinweise

- **NAS:** getestet auf Synology DS718plus (Python 3.8). `--jobs 2` lässt Kerne frei;
  für einen ungestörten Lauf kann `--jobs 4` schneller sein.
- **Erst gefahrlos prüfen:** `--dry-run` zeigt vollständig, was kopiert/konvertiert
  **und gelöscht** würde, ohne etwas anzufassen.
- **Rechte:** Das Programm schreibt als der ausführende Benutzer; gehören vorhandene
  Zieldateien einem anderen Benutzer, scheitert das Überschreiben (wird je Datei als
  Fehler protokolliert, der Lauf läuft weiter).
