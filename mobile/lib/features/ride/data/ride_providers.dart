import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/location_source.dart';
import '../domain/ride_repository.dart';
import 'drift_ride_repository.dart';
import 'geolocator_location_service.dart';
import 'ride_dao.dart';
import 'ride_database.dart';

final rideDatabaseProvider = Provider<RideDatabase>((ref) {
  final db = RideDatabase.open();
  ref.onDispose(() => db.close());
  return db;
});

final rideDaoProvider = Provider<RideDao>((ref) {
  final db = ref.watch(rideDatabaseProvider);
  return RideDao(db);
});

final rideRepositoryProvider = Provider<RideRepository>((ref) {
  final dao = ref.watch(rideDaoProvider);
  return DriftRideRepository(dao);
});

final locationServiceProvider = Provider<LocationSource>((ref) {
  return GeolocatorLocationService();
});
