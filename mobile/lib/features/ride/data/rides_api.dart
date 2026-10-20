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

  /// Paginated history (newest first). `status` filters server-side
  /// ('completed' for the history feed); null lists everything owned.
  Future<Map<String, dynamic>> listRides({
    int page = 1,
    int pageSize = 20,
    String? status,
  }) async {
    final query =
        'page=$page&page_size=$pageSize${status == null ? '' : '&status=$status'}';
    return api.get('$prefix?$query', auth: true);
  }

  /// Accepted fixes for the detail map, oldest first, bounded server-side.
  Future<Map<String, dynamic>> ridePoints(
    String rideId, {
    int limit = 2000,
  }) async {
    return api.get('$prefix/$rideId/points?limit=$limit', auth: true);
  }

  /// Dashboard aggregates over completed rides (UTC calendar windows).
  Future<Map<String, dynamic>> ridesOverview() async {
    return api.get('$prefix/stats/overview', auth: true);
  }
}
