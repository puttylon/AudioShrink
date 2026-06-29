# AudioShrink – Roadmap

## Commitments

- **Sprache:** Python 3.8+ (NAS-Kompatibilität)
- **Encoder:** ausschließlich `opusenc`; `ffmpeg` nur als Decoder
- **Abhängigkeiten:** `mutagen` (pip), externe Tools im PATH

## Status

| Version | Thema | Status |
|---------|-------|--------|
| 0.1–1.0 | Kern: Transkodierung, Mirror, Cleanup, Cover, Parallelisierung, Tests, Release | ✅ |
| 1.1 | Inklusive Bitrate-Schwelle, dynamisches `--jobs` | ✅ |
| 1.2 | `mutagen` ersetzt `ffprobe` (Performance) | ✅ |
| 1.3 | `--max-time`, `--update` (inkrementelles Re-Encoding) | ✅ |
| 1.3.x | Classical-Bitrate-Bug, Testdatei repariert, `requirements.txt` | ✅ |

---

## 1.4 — Qualität
**Status:** 🔲 planned

- **Größenstatistik im Summary:** Gesamteinsparung (MB/GB, %) im abschließenden Log-Eintrag — nützlich zum Nachlesen nach dem Cron-Lauf.
- **METADATA_CACHE-Fix:** Cache-Scope von global auf `run()`-Aufruf begrenzen (Korrektheit bei wiederholtem Import im selben Prozess).

---

## Später — bei echtem Bedarf
Issues und Praxis entscheiden.
