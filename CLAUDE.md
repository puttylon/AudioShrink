# CLAUDE.md – Richtlinien für persönliche Python-Projekte

Dieses Dokument dient als Leitfaden für die KI-Unterstützung in diesem Repository. Da es sich um ein reines Soloprojekt handelt, stehen Pragmatismus, Lesbarkeit und schnelle Iteration im Vordergrund. Kein Over-Engineering.

## 1. Kernphilosophie
- **Pragmatismus vor Perfektion:** Schreibe sauberen, verständlichen Code, aber vermeide unnötige Abstraktionsschichten oder komplexe Design-Patterns.
- **YAGNI (You Aren't Gonna Need It):** Implementiere nur das, was akut angefordert ist. Keine Vorbereitungen für hypothetische zukünftige Features.
- **Direkte Lösungen:** Löse Probleme kompakt und effizient, nutze bevorzugt die Python-Standardbibliothek, es sei denn, externe Pakete bringen massiven Mehrwert.

## 2. Entwicklung & Testing
- **Testpflicht vor Commit:** Bevor du (die KI) Code-Änderungen als abgeschlossen meldest oder committest, musst du selbstständig die Tests lokal ausführen.
- **Sinnvolles Testen:** Fokussiere Unit-Tests auf Kernlogik und fehleranfällige Funktionen. Triviale Funktionen benötigen keine erzwungene Testabdeckung.
- **Wichtige Befehle:**
  - Virtuelle Umgebung: Standard `venv` (`source .venv/bin/activate` bzw. `.venv\Scripts\activate`)
  - Abhängigkeiten: `pip install -r requirements.txt`
  - Tests ausführen: `pytest`
  - Code prüfen/formatieren: `ruff check --fix` und `ruff format`
  - Dokumentation nachziehen — Reihenfolge: **Roadmap → Code → Tests → Dokumentation → Commit**
    - Implementiertes Feature in ROADMAP.md als erledigt markieren
    - README.md auf aktuelle Flags, Befehle und Abhängigkeiten aktualisieren
	-- help auf dann aktuelle Funktionen und Änderung anpassen

## 3. Code-Stil & Qualität
- **Stil:** Halte dich an PEP 8 (Standard-Formatierung via Ruff). Nutze sprechende Variablen- und Funktionsnamen in `snake_case`.
- **Typisierung:** Verwende Type-Hints, um die Lesbarkeit und Wartbarkeit zu verbessern, aber erzwinge sie nicht dogmatisch bei trivialen Skripten.
- **Dokumentation:** Schreibe Docstrings und Kommentare nur für komplexe, nicht selbsterklärende Logik. Keine Zeit mit dem Dokumentieren von Offensichtlichkeiten verschwenden.
- **Fehlerbehandlung:** Fehler abfangen und sinnvoll loggen oder ins Terminal ausgeben. Keine leeren `except: pass`-Blöcke, es sei denn, es ist explizit begründet.