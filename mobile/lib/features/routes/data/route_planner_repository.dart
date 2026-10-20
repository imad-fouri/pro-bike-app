import '../../../core/network/api_client.dart';
import '../domain/route_planner.dart';

/// Real backend planner proxy via the shared [ApiClient]. No mock data.
///
/// Search and routing both run server-side (provider policy, rate limits,
/// and any future credentials stay on the server). Geometry returned here
/// is the provider's own — passed through, never altered.
class RoutePlannerRepository {
  final ApiClient api;
  static const prefix = '/api/v1/routes';

  const RoutePlannerRepository(this.api);

  Future<List<PlaceResult>> search(String query, {int limit = 5}) async {
    final body = await api.get(
      '$prefix/geocode?q=${Uri.encodeQueryComponent(query)}&limit=$limit',
      auth: true,
    );
    return [
      for (final e in (body['results'] as List? ?? const []))
        PlaceResult.fromJson(e as Map<String, dynamic>),
    ];
  }

  Future<PlannedRoute> plan({
    required List<PlannedPoint> waypoints,
    String profile = 'cycling',
  }) async {
    final body = await api.post('$prefix/plan', {
      'points': [
        for (final p in waypoints) {'lat': p.lat, 'lon': p.lon},
      ],
      'profile': profile,
    }, auth: true);
    return PlannedRoute.fromJson(body);
  }
}
