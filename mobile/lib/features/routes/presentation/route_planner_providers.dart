import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/route_planner_repository.dart';
import '../domain/route_planner.dart';

final routePlannerRepositoryProvider = Provider<RoutePlannerRepository>((ref) {
  return RoutePlannerRepository(ref.watch(apiClientProvider));
});

/// One planning waypoint: a display label plus coordinates.
class PlanWaypoint {
  final String label;
  final double lat;
  final double lon;

  const PlanWaypoint({
    required this.label,
    required this.lat,
    required this.lon,
  });

  PlannedPoint get point => PlannedPoint(lat: lat, lon: lon);
}

/// Full planner state. Waypoint edits invalidate the calculated route so a
/// stale polyline can never pass for the current inputs.
class PlannerState {
  final PlanWaypoint? start;
  final PlanWaypoint? destination;
  final List<PlanWaypoint> stops;
  final AsyncValue<List<PlaceResult>>? search;
  final String? searchTarget;
  final AsyncValue<PlannedRoute?>? plan;

  const PlannerState({
    this.start,
    this.destination,
    this.stops = const [],
    this.search,
    this.searchTarget,
    this.plan,
  });

  List<PlanWaypoint> get waypoints {
    final points = <PlanWaypoint>[];
    final first = start;
    if (first != null) points.add(first);
    points.addAll(stops);
    final last = destination;
    if (last != null) points.add(last);
    return points;
  }

  bool get canPlan => start != null && destination != null;

  PlannerState copyWith({
    PlanWaypoint? start,
    PlanWaypoint? destination,
    List<PlanWaypoint>? stops,
    AsyncValue<List<PlaceResult>>? search,
    String? searchTarget,
    AsyncValue<PlannedRoute?>? plan,
    bool clearStart = false,
    bool clearDestination = false,
    bool clearSearch = false,
    bool clearPlan = false,
  }) {
    return PlannerState(
      start: clearStart ? null : (start ?? this.start),
      destination: clearDestination ? null : (destination ?? this.destination),
      stops: stops ?? this.stops,
      search: clearSearch ? null : (search ?? this.search),
      searchTarget: clearSearch ? null : (searchTarget ?? this.searchTarget),
      plan: clearPlan ? null : (plan ?? this.plan),
    );
  }
}

class RoutePlanner extends Notifier<PlannerState> {
  /// Hard cap mirrors the server's waypoint limit.
  static const maxWaypoints = 25;

  @override
  PlannerState build() => const PlannerState();

  void setStart(PlanWaypoint point) {
    state = state.copyWith(start: point, clearSearch: true, clearPlan: true);
  }

  void setDestination(PlanWaypoint point) {
    state = state.copyWith(
      destination: point,
      clearSearch: true,
      clearPlan: true,
    );
  }

  void clearStart() {
    state = state.copyWith(clearStart: true, clearPlan: true);
  }

  void clearDestination() {
    state = state.copyWith(clearDestination: true, clearPlan: true);
  }

  /// Returns false when the waypoint cap is reached.
  bool addStop(PlanWaypoint point) {
    if (state.waypoints.length >= maxWaypoints) return false;
    state = state.copyWith(
      stops: [...state.stops, point],
      clearSearch: true,
      clearPlan: true,
    );
    return true;
  }

  void removeStopAt(int index) {
    final stops = [...state.stops]..removeAt(index);
    state = state.copyWith(stops: stops, clearPlan: true);
  }

  void swap() {
    final start = state.start;
    final destination = state.destination;
    if (start == null || destination == null) return;
    state = state.copyWith(
      start: destination,
      destination: start,
      clearPlan: true,
    );
  }

  void clearSearch() {
    state = state.copyWith(clearSearch: true);
  }

  Future<void> search(String query, {required String target}) async {
    final q = query.trim();
    if (q.length < 3) {
      state = state.copyWith(clearSearch: true);
      return;
    }
    state = state.copyWith(search: const AsyncLoading(), searchTarget: target);
    state = state.copyWith(
      search: await AsyncValue.guard(
        () => ref.read(routePlannerRepositoryProvider).search(q),
      ),
      searchTarget: target,
    );
  }

  Future<void> plan() async {
    final waypoints = state.waypoints;
    if (waypoints.length < 2) return;
    state = state.copyWith(plan: const AsyncLoading());
    state = state.copyWith(
      plan: await AsyncValue.guard(
        () => ref
            .read(routePlannerRepositoryProvider)
            .plan(waypoints: [for (final w in waypoints) w.point]),
      ),
    );
  }
}

final routePlannerProvider = NotifierProvider<RoutePlanner, PlannerState>(
  RoutePlanner.new,
);
