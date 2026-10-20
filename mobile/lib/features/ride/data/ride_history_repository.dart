import '../../../core/network/api_client.dart';
import '../domain/ride_history.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// History reads completed rides newest-first; the detail map reads the
/// bounded accepted-fix trail; the dashboard reads server aggregates.
/// Nothing here computes metrics — the server owns every number.
class RideHistoryRepository {
  final ApiClient api;

  const RideHistoryRepository(this.api);

  Future<({List<RideHistoryItem> items, int total})> historyPage({
    int page = 1,
    int pageSize = 20,
  }) async {
    final body = await api.get(
      '/api/v1/rides?page=$page&page_size=$pageSize&status=completed',
      auth: true,
    );
    final items = (body['items'] as List)
        .map((e) => RideHistoryItem.fromJson(e as Map<String, dynamic>))
        .toList();
    return (items: items, total: (body['total'] as num).toInt());
  }

  Future<RideHistoryItem> detail(String rideId) async {
    final body = await api.get('/api/v1/rides/$rideId', auth: true);
    return RideHistoryItem.fromJson(body);
  }

  Future<({List<RideTrackPoint> points, int total, bool truncated})>
  trackPoints(String rideId) async {
    final body = await api.get('/api/v1/rides/$rideId/points', auth: true);
    final points = (body['points'] as List)
        .map((e) => RideTrackPoint.fromJson(e as Map<String, dynamic>))
        .toList();
    return (
      points: points,
      total: (body['total'] as num).toInt(),
      truncated: body['truncated'] == true,
    );
  }

  Future<RidesOverview> overview() async {
    final body = await api.get('/api/v1/rides/stats/overview', auth: true);
    return RidesOverview.fromJson(body);
  }
}
