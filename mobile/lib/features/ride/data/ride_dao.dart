import 'package:drift/drift.dart';

import 'ride_database.dart';
import 'ride_tables.dart';

part 'ride_dao.g.dart';

@DriftAccessor(tables: [LocalRides, LocalPoints])
class RideDao extends DatabaseAccessor<RideDatabase> with _$RideDaoMixin {
  RideDao(super.db);

  Future<String> insertRideWithPoints({
    required LocalRide ride,
    required List<LocalPoint> points,
  }) async {
    return transaction(() async {
      await into(db.localRides).insert(ride);
      if (points.isNotEmpty) {
        await batch((b) => b.insertAll(db.localPoints, points));
      }
      return ride.id;
    });
  }

  Future<List<LocalRide>> recentRides({int limit = 50}) async {
    final query = select(db.localRides)
      ..orderBy([(t) => OrderingTerm.desc(t.startedAt)])
      ..limit(limit);
    return query.get();
  }

  Future<LocalRide?> rideById(String id) async {
    return (select(db.localRides)..where((t) => t.id.equals(id)))
        .getSingleOrNull();
  }

  Future<List<LocalPoint>> pointsForRide(String rideId) async {
    final query = select(db.localPoints)
      ..where((t) => t.rideId.equals(rideId))
      ..orderBy([(t) => OrderingTerm.asc(t.recordedAt)]);
    return query.get();
  }

  Future<void> deleteRide(String id) async {
    await (delete(db.localRides)..where((t) => t.id.equals(id))).go();
  }
}
