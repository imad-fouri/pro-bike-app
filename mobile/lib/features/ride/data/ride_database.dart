import 'package:drift/drift.dart';
import 'package:drift_flutter/drift_flutter.dart';

import 'ride_tables.dart';

part 'ride_database.g.dart';

@DriftDatabase(tables: [LocalRides, LocalPoints, CachedRoutes])
class RideDatabase extends _$RideDatabase {
  RideDatabase(super.e);

  factory RideDatabase.open() =>
      RideDatabase(driftDatabase(name: 'cyclecoach_rides_local'));

  factory RideDatabase.forTesting(QueryExecutor e) => RideDatabase(e);

  @override
  int get schemaVersion => 1;

    @override
    MigrationStrategy get migrationStrategy => MigrationStrategy(
          onUpgrade: (m, from, to) async {
            // Do not automatically delete existing ride data during incompatible upgrade.
            // Prefer explicit, backed-up reset if genuinely unavoidable.
            // If destructive reset becomes necessary, stop and request separate approval.
            if (from >= 3) {
              return;
            }
          },
        );

    Future<LocalRide?> unfinishedRide() async {
      final query = select(localRides)
        ..where(
          (t) =>
              t.status.equals('recording') |
              t.status.equals('pending_sync') |
              t.status.equals('finalizing') |
              t.status.equals('paused'),
        )
        ..orderBy([(t) => OrderingTerm.desc(t.startedAt)])
        ..limit(1);
      return query.getSingleOrNull();
    }

  Future<List<LocalRide>> pendingSyncRides() async {
    final query = select(localRides)
      ..where((t) => t.status.equals('pending_sync'))
      ..orderBy([(t) => OrderingTerm.desc(t.startedAt)]);
    return query.get();
  }

  Future<void> markUploaded(String localId, int seq) async {
    await (update(localRides)..where((t) => t.id.equals(localId))).write(
      LocalRidesCompanion(
        uploadedSeq: Value(seq),
        updatedAt: Value(DateTime.now()),
      ),
    );
  }

  Future<void> cacheRoute({
    required String id,
    required String routeJson,
    required int versionNo,
    String? geometryJson,
  }) async {
    await into(cachedRoutes).insertOnConflictUpdate(
      CachedRoutesCompanion.insert(
        id: id,
        routeJson: routeJson,
        versionNo: Value(versionNo),
        geometryJson: Value(geometryJson),
        savedAt: DateTime.now(),
      ),
    );
  }

  Future<void> uncacheRoute(String id) async {
    await (delete(cachedRoutes)..where((t) => t.id.equals(id))).go();
  }

  Future<CachedRoute?> cachedRoute(String id) async {
    return (select(cachedRoutes)..where((t) => t.id.equals(id))).getSingleOrNull();
  }

  Future<List<CachedRoute>> allCachedRoutes() async {
    final query = select(cachedRoutes)
      ..orderBy([(t) => OrderingTerm.desc(t.savedAt)]);
    return query.get();
  }
}
