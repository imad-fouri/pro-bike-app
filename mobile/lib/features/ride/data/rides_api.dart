import '../../../core/network/api_client.dart';

/// Rides backend API. Chunk uploads capped at 500 server-side; client
/// sends 100 per chunk (SYNC_CHUNK_SIZE).
class RidesApi {
  final ApiClient api;
  static const prefix = '/api/v1/rides';
  static const chunkSize = 100;

  const RidesApi(this.api);

  Future<Map<String, dynamic>> createRide({
    required String bikeId,
    required String clientRideUuid,
    String? routeId,
    int? routeVersion,
  }) async {
    return api.post(prefix, {
      'bike_id': bikeId,
      'client_ride_uuid': clientRideUuid,
      'route_id': ?routeId,
      'route_version': ?routeVersion,
    }, auth: true);
  }

  Future<Map<String, dynamic>> uploadChunk({
    required String rideId,
    required List<Map<String, dynamic>> points,
  }) async {
    return api.post('$prefix/$rideId/points', {'points': points}, auth: true);
  }

  Future<Map<String, dynamic>> transition(String rideId, String action) async {
    return api.post('$prefix/$rideId/$action', {}, auth: true);
  }

  Future<Map<String, dynamic>> detail(String rideId) async {
    return api.get('$prefix/$rideId', auth: true);
  }
}
