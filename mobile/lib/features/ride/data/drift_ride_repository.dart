import '../domain/ride.dart';
import '../domain/ride_point.dart';
import '../domain/ride_repository.dart';
import 'ride_database.dart';
import 'ride_dao.dart';

class DriftRideRepository implements RideRepository {
  final RideDao _dao;

  DriftRideRepository(this._dao);

  @override
  Future<void> deleteRide(String id) => _dao.deleteRide(id);

  @override
  Future<List<RidePoint>> pointsForRide(String rideId) async {
    final rows = await _dao.pointsForRide(rideId);
    return rows.map((r) => _toPoint(r)).toList();
  }

  @override
  Future<Ride?> rideById(String id) async {
    final row = await _dao.rideById(id);
    if (row == null) return null;
    return _toRide(row);
  }

  @override
  Future<List<Ride>> recentRides({int limit = 50}) async {
    final rows = await _dao.recentRides(limit: limit);
    return rows.map((r) => _toRide(r)).toList();
  }

  @override
  Future<void> saveRide(Ride ride, List<RidePoint> points) async {
    await _dao.insertRideWithPoints(
      ride: _fromRide(ride),
      points: points.map((p) => _fromPoint(ride.id, p)).toList(),
    );
  }

  Ride _toRide(LocalRide r) => Ride(
        id: r.id,
        bikeId: r.bikeId,
        startedAt: r.startedAt,
        endedAt: r.endedAt ?? r.startedAt,
        distanceMeters: 0.0,
        movingSeconds: 0,
        durationSeconds: 0,
        avgSpeedMps: 0.0,
        maxSpeedMps: 0.0,
        elevationGainMeters: 0.0,
      );

  LocalRide _fromRide(Ride r) => LocalRide(
        id: r.id,
        serverId: null,
        bikeId: r.bikeId ?? '',
        routeId: null,
        status: 'completed',
        startedAt: r.startedAt,
        endedAt: r.endedAt,
        uploadedSeq: -1,
        updatedAt: DateTime.now(),
      );

  RidePoint _toPoint(LocalPoint r) => RidePoint(
        lat: r.lat,
        lng: r.lon,
        altitude: r.alt,
        speedMps: r.speed,
        accuracyMeters: r.accuracy,
        timestamp: r.recordedAt,
      );

  LocalPointsCompanion _fromPoint(String rideId, RidePoint p) =>
      LocalPointsCompanion.insert(
        rideId: rideId,
        pointUuid: p.pointUuid ?? '',
        seq: p.seq ?? 0,
        lat: p.lat,
        lon: p.lng,
        alt: p.altitude == null ? const Value.absent() : Value(p.altitude),
        accuracy: p.accuracyMeters == null
            ? const Value.absent()
            : Value(p.accuracyMeters),
        speed: p.speedMps == null ? const Value.absent() : Value(p.speedMps),
        heading: const Value.absent(),
        recordedAt: p.timestamp,
        accepted: const Value(true) as Value<bool>,
        reason: const Value.absent(),
        uploaded: const Value(false) as Value<bool>,
      );
}
