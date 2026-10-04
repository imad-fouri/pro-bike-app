# 02 — Flutter Architecture

```text
mobile/lib/
├── core/ (networking/dio, storage/hive, routing/go_router,
│          localization, permissions, theme, errors)
├── features/ (auth, onboarding, home, rides, gps, routes, training,
│             coach, performance, recovery, bikes, friends, teams,
│             group_rides, chat, location_sharing, notifications, profile)
│   └── <feature>/ {presentation/, domain/, data/}
└── main.dart (flavors: dev/staging/prod)
```

- State: Riverpod (or Bloc — ADR-02, decide Phase 1, one only).
- No business logic in widgets. Use cases in domain, DTOs+repos in data.
- Networking: Dio + interceptors (refresh, retry, offline queue). Storage: Hive (ride cache, offline routes) + secure_storage (tokens).
- GPS: foreground+background service isolate, local buffering, batched upload.
- Flavors + `--dart-define` for API/WS URLs. Tablet/web reuse same features.
