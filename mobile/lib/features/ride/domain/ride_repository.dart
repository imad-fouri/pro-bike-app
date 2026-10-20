import 'ride.dart';
import 'ride_point.dart';

/// Local-only persistence boundary for recorded rides.
abstract class RideRepository {
  Future<void> saveRide(Ride ride, List<RidePoint> points);

  Future<List<Ride>> recentRides({int limit = 50});

  Future<Ride?> rideById(String id);

  Future<List<RidePoint>> pointsForRide(String rideId);

  Future<void> deleteRide(String id);
}
