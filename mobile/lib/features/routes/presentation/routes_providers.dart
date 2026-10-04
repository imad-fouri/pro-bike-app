import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../../ride/presentation/ride_providers.dart';
import '../data/routes_repository.dart';
import '../domain/route.dart';

final routesRepositoryProvider = Provider<RoutesRepository>(
  (ref) => RoutesRepository(ref.watch(apiClientProvider)),
);

/// Route list: loading / data / empty / error. Refresh after every mutation.
class RouteList extends AsyncNotifier<List<AppRoute>> {
  late RoutesRepository _repo;

  @override
  Future<List<AppRoute>> build() async {
    _repo = ref.watch(routesRepositoryProvider);
    final result = await _repo.list(pageSize: 100, includeArchived: true);
    return result.items;
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final result = await _repo.list(pageSize: 100, includeArchived: true);
      return result.items;
    });
  }

  Future<AppRoute> create({
    required String name,
    String? description,
    required String activityType,
    required String privacy,
    required List<RoutePointInput> points,
  }) async {
    final route = await _repo.create(
      name: name,
      description: description,
      activityType: activityType,
      privacy: privacy,
      points: points,
    );
    await refresh();
    return route;
  }

  Future<AppRoute> updateRoute(
    String id, {
    required int expectedVersion,
    String? name,
    String? description,
    String? activityType,
    String? privacy,
    List<RoutePointInput>? points,
    String? changelog,
  }) async {
    final route = await _repo.update(
      id,
      expectedVersion: expectedVersion,
      name: name,
      description: description,
      activityType: activityType,
      privacy: privacy,
      points: points,
      changelog: changelog,
    );
    ref.invalidate(routeDetailProvider);
    ref.invalidate(routeVersionsProvider);
    await refresh();
    return route;
  }

  Future<AppRoute> archive(String id) async {
    final route = await _repo.archive(id);
    ref.invalidate(routeDetailProvider);
    await refresh();
    return route;
  }

  Future<AppRoute> restore(String id) async {
    final route = await _repo.restore(id);
    ref.invalidate(routeDetailProvider);
    await refresh();
    return route;
  }

  Future<void> remove(String id) async {
    await _repo.remove(id);
    ref.invalidate(routeDetailProvider);
    await refresh();
  }

  Future<({AppRoute route, int importedPoints, int duplicatesRemoved})>
  importGpx({
    required List<int> bytes,
    required String filename,
    String? name,
    String activityType = 'road',
    String privacy = 'private',
  }) async {
    final result = await _repo.importGpx(
      bytes: bytes,
      filename: filename,
      name: name,
      activityType: activityType,
      privacy: privacy,
    );
    await refresh();
    return result;
  }
}

final routeListProvider = AsyncNotifierProvider<RouteList, List<AppRoute>>(
  RouteList.new,
);

/// Detail with geometry (map needs it on open).
final routeDetailProvider =
    FutureProvider.family<({AppRoute route, RouteGeometry? geometry}), String>(
      (ref, id) =>
          ref.watch(routesRepositoryProvider).detail(id, includeGeometry: true),
    );

/// Geometry by `'routeId'` or `'routeId#version'` (historical versions).
final routeGeometryProvider = FutureProvider.autoDispose
    .family<RouteGeometry, String>((ref, key) {
      final parts = key.split('#');
      final version = parts.length > 1 ? int.tryParse(parts[1]) : null;
      return ref
          .watch(routesRepositoryProvider)
          .geometry(parts[0], version: version);
    });

final routeVersionsProvider =
    FutureProvider.family<List<RouteVersionSummary>, String>((ref, id) {
      return ref.watch(routesRepositoryProvider).versions(id);
    });

/// Explicit offline downloads (never a silent sync of every route, §39).
final offlineRoutesProvider = FutureProvider<List<AppRoute>>((ref) async {
  final db = await ref.watch(rideDatabaseProvider.future);
  final rows = await db.allCachedRoutes();
  return rows.map((r) => RoutesRepository.decodeRoute(r.routeJson)).toList();
});

final offlineGeometryProvider = FutureProvider.autoDispose
    .family<RouteGeometry?, String>((ref, id) async {
      final db = await ref.watch(rideDatabaseProvider.future);
      final row = await db.cachedRoute(id);
      final raw = row?.geometryJson;
      if (raw == null) return null;
      return RouteGeometry.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    });
