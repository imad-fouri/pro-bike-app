import 'dart:convert';

import '../../../core/network/api_client.dart';
import '../domain/route.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
class RoutesRepository {
  final ApiClient api;
  static const prefix = '/api/v1/routes';

  const RoutesRepository(this.api);

  Future<({List<AppRoute> items, int total})> list({
    int page = 1,
    int pageSize = 20,
    String? activityType,
    bool includeArchived = false,
  }) async {
    final q = {
      'page': '$page',
      'page_size': '$pageSize',
      if (activityType case final a) 'activity_type': a,
      if (includeArchived) 'status_all': 'true',
    }.entries.map((e) => '${e.key}=${e.value}').join('&');
    final body = await api.get('$prefix?$q', auth: true);
    final items = (body['items'] as List)
        .map((e) => AppRoute.fromJson(e as Map<String, dynamic>))
        .toList();
    return (items: items, total: (body['total'] as num).toInt());
  }

  /// Detail; geometry only when [includeGeometry] (routes/:id?include_geometry).
  Future<({AppRoute route, RouteGeometry? geometry})> detail(
    String id, {
    bool includeGeometry = false,
  }) async {
    final q = includeGeometry ? '?include_geometry=true' : '';
    final body = await api.get('$prefix/$id$q', auth: true);
    final geometry = body['geometry'] is Map<String, dynamic>
        ? RouteGeometry.fromJson(body['geometry'] as Map<String, dynamic>)
        : null;
    return (route: AppRoute.fromJson(body), geometry: geometry);
  }

  Future<RouteGeometry> geometry(String id, {int? version}) async {
    final q = version == null ? '' : '?version=$version';
    final body = await api.get('$prefix/$id/geometry$q', auth: true);
    return RouteGeometry.fromJson(body);
  }

  Future<List<RouteVersionSummary>> versions(String id) async {
    final body = await api.get('$prefix/$id/versions', auth: true);
    return (body['items'] as List)
        .map((e) => RouteVersionSummary.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<AppRoute> create({
    required String name,
    String? description,
    required String activityType,
    required String privacy,
    required List<RoutePointInput> points,
  }) async {
    final body = await api.post(prefix, {
      'name': name,
      if (description != null && description.isNotEmpty)
        'description': description,
      'activity_type': activityType,
      'privacy': privacy,
      'points': [for (final p in points) p.toJson()],
    }, auth: true);
    return AppRoute.fromJson(body);
  }

  /// Optimistic concurrency: [expectedVersion] mismatch → 409 (reload UI).
  Future<AppRoute> update(
    String id, {
    required int expectedVersion,
    String? name,
    String? description,
    String? activityType,
    String? privacy,
    List<RoutePointInput>? points,
    String? changelog,
  }) async {
    final body = await api.patch('$prefix/$id', {
      'expected_version': expectedVersion,
      'name': ?name,
      'description': ?description,
      'activity_type': ?activityType,
      'privacy': ?privacy,
      if (points != null) 'points': [for (final p in points) p.toJson()],
      'changelog': ?changelog,
    }, auth: true);
    return AppRoute.fromJson(body);
  }

  Future<AppRoute> archive(String id) async {
    final body = await api.post('$prefix/$id/archive', {}, auth: true);
    return AppRoute.fromJson(body);
  }

  Future<AppRoute> restore(String id) async {
    final body = await api.post('$prefix/$id/restore', {}, auth: true);
    return AppRoute.fromJson(body);
  }

  Future<AppRoute> remove(String id) async {
    final body = await api.delete('$prefix/$id', auth: true);
    return AppRoute.fromJson(body);
  }

  Future<({AppRoute route, int importedPoints, int duplicatesRemoved})>
  importGpx({
    required List<int> bytes,
    required String filename,
    String? name,
    String activityType = 'road',
    String privacy = 'private',
  }) async {
    final body = await api.postMultipart(
      '$prefix/import/gpx',
      fileField: 'file',
      filename: filename,
      bytes: bytes,
      fields: {
        if (name != null && name.isNotEmpty) 'name': name,
        'activity_type': activityType,
        'privacy': privacy,
      },
      auth: true,
    );
    final route = AppRoute.fromJson(body['route'] as Map<String, dynamic>);
    return (
      route: route,
      importedPoints: (body['imported_points'] as num? ?? 0).toInt(),
      duplicatesRemoved: (body['duplicates_removed'] as num? ?? 0).toInt(),
    );
  }

  Future<({List<int> bytes, String filename})> exportGpx(String id) async {
    final res = await api.getBytes('$prefix/$id/export/gpx', auth: true);
    final disposition = res.headers['content-disposition'] ?? '';
    final match = RegExp('filename="([^"]+)"').firstMatch(disposition);
    return (bytes: res.bytes, filename: match?.group(1) ?? 'route.gpx');
  }

  /// Attach/detach a route on an owned ride (routeId == null detaches).
  Future<Map<String, dynamic>> attachToRide(
    String rideId, {
    required String? routeId,
    int? routeVersion,
  }) async {
    return api.post('/api/v1/rides/$rideId/route', {
      'route_id': routeId,
      'route_version': ?routeVersion,
    }, auth: true);
  }

  /// Raw JSON of a cached route (offline mirror keeps the server payload).
  static String encodeRoute(AppRoute route) => jsonEncode(route.toJson());

  static AppRoute decodeRoute(String raw) =>
      AppRoute.fromJson(jsonDecode(raw) as Map<String, dynamic>);
}
