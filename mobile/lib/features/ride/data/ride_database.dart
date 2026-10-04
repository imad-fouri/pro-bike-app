import 'package:drift/drift.dart';
import 'package:drift_flutter/drift_flutter.dart';

import 'ride_tables.dart';

part 'ride_database.g.dart';

/// Drift database (ADR-07). `driftDatabase` picks the right executor per
/// platform (native file on mobile/desktop, sqlite3.wasm on web).
/// Tests use [RideDatabase.forTesting] with an in-memory database.
@DriftDatabase(tables: [LocalRides, LocalPoints, CachedRoutes])
class RideDatabase extends _$RideDatabase {
  RideDatabase(super.e);

  factory RideDatabase.open() =>
      RideDatabase(driftDatabase(name: 'cyclecoach_rides'));

  factory RideDatabase.forTesting(QueryExecutor e) => RideDatabase(e);

  @override
  int get schemaVersion => 3;

  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (m) => m.createAll(),
    onUpgrade: (m, from, to) async {
      if (from < 2) await m.createTable(cachedRoutes);
      if (from < 3) await m.addColumn(localRides, localRides.routeId);
    },
  );

  /// Crash/restart recovery probe: any ride left recording, paused, or stuck
  /// mid-finalize. `finalizing` and `pending_sync` are durable finish intents:
  /// the rider asked to finish and the drain did not complete, so these rows
  /// must surface exactly like an interrupted recording.
  Future<LocalRide?> unfinishedRide() {
    return (select(localRides)
          ..where(
            (r) => r.status.isIn([
              'recording',
              'paused',
              'finalizing',
              'pending_sync',
            ]),
          )
          ..orderBy([(r) => OrderingTerm.desc(r.updatedAt)])
          ..limit(1))
        .getSingleOrNull();
  }

  /// Rides whose finish drain did not complete. Headless recovery
  /// ([RideSession.recoverPendingSync]) drains and finalizes each of these
  /// without starting location tracking.
  Future<List<LocalRide>> pendingSyncRides() {
    return (select(localRides)
          ..where((r) => r.status.isIn(['finalizing', 'pending_sync']))
          ..orderBy([(r) => OrderingTerm.asc(r.updatedAt)]))
        .get();
  }

  Future<void> markUploaded(String rideId, int seq) async {
    await (update(localRides)..where((r) => r.id.equals(rideId))).write(
      LocalRidesCompanion(
        uploadedSeq: Value(seq),
        updatedAt: Value(DateTime.now()),
      ),
    );
  }

  // --- offline route cache (explicit download, version-keyed) -------------

  Future<void> cacheRoute({
    required String id,
    required String routeJson,
    required int versionNo,
    String? geometryJson,
  }) async {
    await into(cachedRoutes).insertOnConflictUpdate(
      CachedRoutesCompanion(
        id: Value(id),
        routeJson: Value(routeJson),
        geometryJson: Value(geometryJson),
        versionNo: Value(versionNo),
        savedAt: Value(DateTime.now()),
      ),
    );
  }

  Future<List<CachedRoute>> allCachedRoutes() => (select(
    cachedRoutes,
  )..orderBy([(r) => OrderingTerm.desc(r.savedAt)])).get();

  Future<CachedRoute?> cachedRoute(String id) =>
      (select(cachedRoutes)..where((r) => r.id.equals(id))).getSingleOrNull();

  Future<void> uncacheRoute(String id) =>
      (delete(cachedRoutes)..where((r) => r.id.equals(id))).go();
}
