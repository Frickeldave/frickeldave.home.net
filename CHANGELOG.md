# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier festgehalten.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [Unreleased]

### Added

- Grundgerüst der Home-Assistant-Custom-Integration `frickeldave_store`
  (Manifest, HACS-Metadaten, CI mit ruff, mypy, pytest, hassfest und HACS-Validation).
- Datenschicht mit HA-`Store` (Schlüssel `frickeldave_store.data`, Schema-Version 1),
  Bildablage unter `config/frickeldave_store/images/<artikelnummer>/`, atomaren
  Schreibvorgängen, Snapshots vor Migrationen und `async_migrate_entry`.
- Produktdomain: Anlegen mit eindeutiger Artikelnummer, Bearbeiten, ausschließlich
  Soft-Delete, unveränderliche Kategorie, Tag-Verwaltung, Bestandsbuchungen,
  kombinierbare Filter (Kategorie ODER, Tags UND, übrige UND) und Sortierung.
- Config Flow und Options Flow inklusive deutscher und englischer Übersetzungen.
- Berechtigungsschicht mit den Rollen `product`, `finance` und `app_admin`,
  Default Deny, Prüfung je HTTP-/WebSocket-Anfrage und sofortigem Rechteentzug.
- HTTP-API unter `/api/frickeldave_store` und WebSocket-Kommandos inklusive
  Panel-Registrierung (`panel_custom`, `frickeldave-store`).
- KI-Beschreibungsvorschläge über eine Anbieter-Abstraktion mit DeepSeek-Client,
  EXIF/GPS-Bereinigung und ausschließlich manueller Übernahme.
- Idempotenter Katalogexport im Schema der Website plus CLI.
- Dokumentation (`docs/`) und Testsuite (Domain-, API- und HA-Integrationstests).

Referenz: `Frickeldave/frickeldave.github.io#289`.
