# 00 — Product Spec (CycleCoach)

## Vision
Worldwide GPS-first cycling platform for 14 disciplines (Road, Gravel, MTB, CX, Endurance, TT, Track, BMX, Touring, Bikepacking, E-Bike, Commuting, Indoor, Custom). Extensible `cycling_disciplines` table — new category = new row, no core rewrite.

## Training methods (configurable, not hard-coded)
`training_methodologies` table + `training_zones` per methodology: Endurance, Z1/Z2, Tempo, Sweet Spot, Threshold, VO2max, Anaerobic, Sprint, Climbing, Cadence, Long-distance, Recovery, Race/Taper/Base/Build, Event-specific (Road/Gravel/MTB/Bikepacking). Engine reads methodology config; code never branches on literal method names.

## Core domains
auth/users, bikes, rides+GPS, routes+GPX, training, performance/recovery, AI coach, friends, teams+chat, group-rides+live-location, route-sync, sensors/BLE, offline-sync, challenges, subscriptions, admin.

## Non-negotiables
- Privacy-first location: `Friend + explicit grant + active session = visibility`. Revocable, expiring.
- AI honesty: never invent HR/power/FTP/VO2max. Raw vs derived vs estimate always labelled.
- Offline-first rides: loss of network must never destroy a ride.
- i18n from day 1: en, fr, ar (RTL), es, it, de, pt. No user strings in business logic.
