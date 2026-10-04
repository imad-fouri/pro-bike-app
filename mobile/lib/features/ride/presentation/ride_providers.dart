import 'dart:async';

import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/ride_database.dart';
import '../data/ride_recorder.dart';
import '../data/rides_api.dart';
import '../domain/location_source.dart';

final rideDatabaseProvider = FutureProvider<RideDatabase>((ref) async {
  return RideDatabase.open();
});

final ridesApiProvider = Provider<RidesApi>((ref) {
  return RidesApi(ref.watch(apiClientProvider));
});

final locationSourceProvider = Provider<LocationSource>(
  (_) => GeolocatorSource(),
);

/// Crash/restart recovery probe: unfinished local ride, if any.
final unfinishedRideProvider = FutureProvider((ref) async {
  final db = await ref.watch(rideDatabaseProvider.future);
  return db.unfinishedRide();
});

/// Rides stuck mid-finalize: finish was requested but the drain did not
/// complete (offline, failure, or process kill). Headless recovery drains
/// these without starting location tracking.
final pendingSyncProvider = FutureProvider((ref) async {
  final db = await ref.watch(rideDatabaseProvider.future);
  return db.pendingSyncRides();
});

/// Owns the active recorder lifecycle. Null state = no active ride.
class RideSession extends Notifier<LiveRide?> {
  RideRecorder? _rec;
  StreamSubscription<LiveRide>? _liveSub;

  @override
  LiveRide? build() => null;

  Future<LocationPermissionState> startRide({
    required String bikeId,
    required String bikeName,
    String? routeId,
    String? resumeLocalId,
  }) async {
    final db = await ref.read(rideDatabaseProvider.future);
    _rec = RideRecorder(
      db: db,
      rides: ref.read(ridesApiProvider),
      location: ref.read(locationSourceProvider),
    );
    await _liveSub?.cancel();
    _liveSub = _rec!.updates.listen((live) => state = live);
    return _rec!.start(
      bikeId: bikeId,
      bikeName: bikeName,
      routeId: routeId,
      resumeLocalId: resumeLocalId,
    );
  }

  Future<void> pause() async {
    await _rec?.pause();
  }

  Future<void> resume() async {
    await _rec?.resume();
  }

  Future<Map<String, dynamic>?> finish() async {
    final summary = await _rec?.finish();
    await _liveSub?.cancel();
    return summary;
  }

  Future<void> discardRide() async {
    await _rec?.discard();
    await _liveSub?.cancel();
    state = null;
  }

  Future<void> syncNow() async {
    await _rec?.syncNow();
  }

  /// Headless finalize of a stuck ride: attach without location tracking,
  /// drain every pending chunk, then finalize the server ride. Returns the
  /// server summary, or null when the drain could not complete.
  Future<Map<String, dynamic>?> finishPendingRide(String localId) async {
    final db = await ref.read(rideDatabaseProvider.future);
    final rec = RideRecorder(
      db: db,
      rides: ref.read(ridesApiProvider),
      location: ref.read(locationSourceProvider),
    );
    try {
      if (!await rec.attachForSync(localId)) return null;
      final summary = await rec.drainAndFinalize();
      if (summary != null) {
        await (db.update(
          db.localRides,
        )..where((r) => r.id.equals(localId))).write(
          LocalRidesCompanion(
            status: const Value('completed'),
            updatedAt: Value(DateTime.now()),
          ),
        );
        ref
          ..invalidate(unfinishedRideProvider)
          ..invalidate(pendingSyncProvider);
      }
      return summary;
    } finally {
      await rec.dispose();
    }
  }

  /// Recover every stuck finalize after a restart: new recorder instances
  /// against the same durable database, no in-memory timer involved. Returns
  /// the number of rides brought to completed.
  Future<int> recoverPendingSync() async {
    final db = await ref.read(rideDatabaseProvider.future);
    final pending = await db.pendingSyncRides();
    var completed = 0;
    for (final ride in pending) {
      if (await finishPendingRide(ride.id) != null) completed++;
    }
    return completed;
  }
}

final rideSessionProvider = NotifierProvider<RideSession, LiveRide?>(
  RideSession.new,
);
