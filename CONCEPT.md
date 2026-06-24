# AudioShrink – Konzept & Architektur

Dieses Dokument beschreibt das **Warum** und den **Aufbau** von AudioShrink.
Die **Bedienung** steht im [README.md](README.md), das **Implementierungsdetail**
in [audioshrink.py](audioshrink.py) (die maßgebliche Quelle der Wahrheit).

Es wird **nicht** versionssynchron gepflegt – es hält die stabilen Design-
Entscheidungen fest, nicht die Versionshistorie (die kommt in einen Changelog).

---

## 1. Zweck

Aus einer großen, heterogenen Musiksammlung eine kompakte, mobile, einheitlich
formatierte **Opus**-Spiegelkopie erzeugen – typisch 10–20 % der Ausgangsgröße,
ohne hörbare Qualitätsverluste.

---

## 2. Designprinzipien

| Prinzip | Festlegung |
|---|---|
| Portabilität | Läuft auf beliebigen Linux-Systemen, unabhängig von der Shell. Nur Python-3-**Standardbibliothek** (Mindestversion **3.8**), keine pip-Pakete. |
| Encoder-Disziplin | **opusenc ist der einzige Encoder.** ffmpeg wird nie zum Encodieren benutzt. |
| Spiegel-Invariante | Die relative Zielstruktur ist stets identisch zur Quelle. |
| Idempotenz / Fortsetzbarkeit | Wiederholte Läufe verarbeiten nur Neues/Geändertes; ein Abbruch hinterlässt fertige Alben konsistent. |
| Robustheit | Pro-Datei-Fehler brechen den Lauf nicht ab; Schreiben erfolgt atomar (Temp-Datei + Umbenennen). |

---

## 3. Werkzeug-Einsatz (verbindliche Aufgabenteilung)

| Aufgabe | Werkzeug |
|---|---|
| Encoding **aller** Ausgaben nach Opus | **opusenc** (einziger Encoder) |
| Native Eingaben (FLAC/WAV/AIFF) lesen | opusenc direkt (Tags + Cover automatisch) |
| Lossy-Quellen für Re-Encode **dekodieren** | **ffmpeg** → PCM-Pipe an opusenc (nur Dekoder); Tags/Cover via ffprobe→opusenc neu gesetzt |
| Audio-Analyse (Samplerate, Bitrate, Tags) | **ffprobe** (nur lesend) |
| Cover extrahieren (für Dedup) | **metaflac** (FLAC) bzw. **ffmpeg** (Lossy, unverändertes Kopieren) |
| Cover verkleinern (optional) | **ImageMagick** |

> opusenc liest nur WAV/AIFF/FLAC/raw-PCM – verlustbehaftete Formate müssen daher
> vor dem Encoden dekodiert werden (ffmpeg als Dekoder, **nicht** als Encoder).

---

## 4. Architektur-Überblick

Verarbeitung erfolgt **ordnerweise** (ein „Album"), weil die Cover-Deduplizierung
einen Album-Kontext braucht. Je Ordner:

1. **Cover-Plan** bestimmen (nur wenn etwas zu tun ist): teilen sich alle
   transkodierten Tracks dasselbe eingebettete Cover bzw. liegt eine separate
   Cover-Datei vor?
2. **Tracks parallel** verarbeiten (ThreadPool – die eigentliche Arbeit läuft in
   opusenc/ffmpeg als externe Prozesse). Pro Datei: analysieren → entscheiden
   (transkodieren / re-encodieren / kopieren / überspringen) → ausführen → loggen.
   Logausgabe wird in Submission-Reihenfolge eingesammelt (lesbar trotz Parallelität).
3. **Album-Cover** abschließend ablegen (eine `cover.jpg`/`.png`, falls dedupliziert).
4. **Hybrid-Cleanup, Teil 1:** verwaiste Dateien dieses Ordners sofort entfernen.

Am Ende: **Hybrid-Cleanup, Teil 2:** verwaiste Verzeichnisse + leere Ordner.

---

## 5. Kern-Entscheidungen

- **Formatwahl je Datei:** FLAC/WAV/AIFF → Opus; verlustbehaftet → Re-Encode nach
  Opus **nur wenn Bitrate > Schwelle**, sonst Kopie; Bilder → kopieren/verkleinern/
  entfernen; sonstige → kopieren.
- **Re-Encode-Entscheidung: nur Bitrate, kein Genre.** Schwelle einstellbar
  (Standard 320 kbps). Begründung: konsistent und vorhersehbar; vermeidet das
  langsame, kaum lohnende Re-Encoden ohnehin schon kleiner (niedrigbitratiger) Dateien.
- **Zielbitrate/Tuning: genre-bewusst.** Samplerate-Basis (160/128/96), Sprach-Genres
  → 64 kbps + `--speech`, Deckelung auf die Quellbitrate. **Keine** Absenkung nach
  Tracklänge (Länge ist kein Maß für den Bitratenbedarf).
- **Cover-Deduplizierung** über **alle transkodierten Tracks** (FLAC **und**
  re-encodete Lossy): gemeinsames Cover → eine Datei im Ordner, Bild aus den Opus
  verworfen (`--discard-pictures`). Spart das Vielfache des Cover-Volumens je Album.
- **Aufräumen = Spiegel, schwellenbewusst:** Ein Ziel ist gültig, wenn es exakt das
  ist, was die Quelle aktuell produziert. Insbesondere ist ein `.opus` aus einer
  Lossy-Quelle nur gültig, wenn diese auch tatsächlich re-encodiert würde – sonst
  (Kopie-Fall) ist es verwaist. So bleibt bei geänderter Schwelle je Datei genau
  ein korrektes Zielformat übrig.
- **Keine Umgebungs-Eingriffe:** kein Setzen von Besitzer/Rechten – Dateien erhalten
  die Standardrechte des ausführenden Benutzers.

---

## 6. Standardwerte (Stand der Auslieferung)

`--jobs 2` · `--comp 6` · `--reencode-min-bitrate 320` · Re-Encode an ·
Cover-Dedup an · Cleanup an. Alle per CLI überschreibbar (siehe README).

---

## 7. Bewusste Nicht-Ziele

- Keine Tracklängen-abhängige Bitrate.
- Keine „Ziel-Whitelist": Dateien ohne Quell-Entsprechung werden bewusst entfernt
  (strikter Spiegel). Begleitdateien bleiben, solange sie in der Quelle liegen.
- Kein ffmpeg als Encoder; keine pip-Abhängigkeiten.
