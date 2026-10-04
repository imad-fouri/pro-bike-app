# 09 — Testing & CI/CD

Backend: pytest unit/integration/api/db/authZ/WS/privacy/route-sync. Flutter: unit/widget/integration. E2E critical flows: ride record→sync, route publish→sync, location grant→expiry, missed-workout adaptation.

Gates per phase: Tests PASS, `ruff+mypy` / `dart analyze` PASS, format PASS, build PASS, `alembic upgrade head` PASS, OpenAPI snapshot PASS, `pip-audit`/`osv` PASS.

CI (GitHub Actions): `backend-ci` (lint/type/test/migrate/contract), `flutter-ci` (analyze/test/build apk), `security` (secrets scan, audit). No phase marked complete on failing gate.
