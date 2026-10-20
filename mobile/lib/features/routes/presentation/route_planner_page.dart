import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:latlong2/latlong.dart';

import '../../../core/config/app_config.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../ride/domain/units.dart';
import '../domain/route.dart';
import '../domain/route_planner.dart';
import 'route_planner_providers.dart';
import 'routes_providers.dart';

/// Cycling route planner: search start/destination, add stops by search or
/// map tap, calculate the provider's cycling route, preview it, and save it
/// through the existing route storage (then the detail screen owns edit,
/// export, and ride-from-route).
///
/// Exactly one cycling profile exists because the provider exposes exactly
/// one bike profile. Elevation and surface data are honestly unavailable:
/// the provider returns neither, and no values are invented.
class RoutePlannerPage extends ConsumerStatefulWidget {
  const RoutePlannerPage({super.key});

  @override
  ConsumerState<RoutePlannerPage> createState() => _RoutePlannerPageState();
}

class _TapTarget {
  static const start = 'start';
  static const stop = 'stop';
  static const destination = 'destination';
}

class _RoutePlannerPageState extends ConsumerState<RoutePlannerPage> {
  final _startController = TextEditingController();
  final _destController = TextEditingController();
  final _mapController = MapController();
  Timer? _debounce;
  String _tapTarget = _TapTarget.start;
  bool _saving = false;
  bool _mapReady = false;

  static const _debounceTime = Duration(milliseconds: 800);

  @override
  void dispose() {
    _debounce?.cancel();
    _startController.dispose();
    _destController.dispose();
    _mapController.dispose();
    super.dispose();
  }

  void _onQueryChanged(String query, String target) {
    _debounce?.cancel();
    if (query.trim().length < 3) {
      ref.read(routePlannerProvider.notifier).clearSearch();
      return;
    }
    _debounce = Timer(_debounceTime, () {
      ref.read(routePlannerProvider.notifier).search(query, target: target);
    });
  }

  void _onMapTap(LatLng point) {
    final t = context.l10n;
    final notifier = ref.read(routePlannerProvider.notifier);
    final label =
        '${point.latitude.toStringAsFixed(5)}, ${point.longitude.toStringAsFixed(5)}';
    final waypoint = PlanWaypoint(
      label: label,
      lat: point.latitude,
      lon: point.longitude,
    );
    switch (_tapTarget) {
      case _TapTarget.start:
        notifier.setStart(waypoint);
        _startController.text = label;
        setState(() => _tapTarget = _TapTarget.destination);
      case _TapTarget.destination:
        notifier.setDestination(waypoint);
        _destController.text = label;
        setState(() => _tapTarget = _TapTarget.stop);
      case _TapTarget.stop:
        if (!notifier.addStop(waypoint)) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(t.get('planner.needTwoPoints'))),
          );
        }
    }
  }

  Future<void> _save() async {
    final t = context.l10n;
    final plan = ref.read(routePlannerProvider).plan?.value;
    if (plan == null) return;
    final nameController = TextEditingController();
    String activity = 'road';
    final saved = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(t.get('planner.save')),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                key: const Key('planner.save.name'),
                controller: nameController,
                decoration: InputDecoration(
                  labelText: t.get('planner.routeName'),
                ),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: activity,
                decoration: InputDecoration(
                  labelText: t.get('routes.activity'),
                ),
                items: [
                  for (final a in const [
                    'road',
                    'gravel',
                    'mountain_bike',
                    'touring',
                    'bikepacking',
                    'commuting',
                    'e_bike',
                    'other',
                  ])
                    DropdownMenuItem(
                      value: a,
                      child: Text(t.get('routes.activity.$a')),
                    ),
                ],
                onChanged: (v) => activity = v ?? 'road',
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: Text(t.get('bikes.cancel')),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(c, nameController.text.trim().isNotEmpty),
            child: Text(t.get('planner.save')),
          ),
        ],
      ),
    );
    if (saved != true || !mounted) return;
    setState(() => _saving = true);
    try {
      final route = await ref
          .read(routesRepositoryProvider)
          .create(
            name: nameController.text.trim(),
            activityType: activity,
            privacy: 'private',
            points: [
              for (final p in plan.points)
                RoutePointInput(lat: p.lat, lon: p.lon),
            ],
          );
      if (mounted) context.go('/routes/${route.id}');
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(_planErrorKey(e.code))));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  String _planErrorKey(String code) {
    final t = context.l10n;
    if (code == 'ROUTE_UNREACHABLE') return t.get('planner.unreachable');
    if (code == 'NETWORK_ERROR' || code == 'NETWORK_TIMEOUT') {
      return t.get('history.offline');
    }
    return t.get('planner.planFailed');
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final planner = ref.watch(routePlannerProvider);
    final planValue = planner.plan?.value;
    return Scaffold(
      appBar: AppBar(title: Text(t.get('planner.title'))),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Column(
              children: [
                Row(
                  children: [
                    Expanded(
                      child: _SearchField(
                        key: const Key('planner.start'),
                        controller: _startController,
                        hint:
                            '${t.get('planner.start')} — ${t.get('planner.searchHint')}',
                        icon: Icons.trip_origin_rounded,
                        onChanged: (v) => _onQueryChanged(v, _TapTarget.start),
                        onClear: () {
                          _startController.clear();
                          ref.read(routePlannerProvider.notifier).clearStart();
                        },
                      ),
                    ),
                    IconButton(
                      key: const Key('planner.swap'),
                      tooltip: t.get('planner.swap'),
                      icon: const Icon(Icons.swap_vert_rounded),
                      onPressed: () {
                        ref.read(routePlannerProvider.notifier).swap();
                        final tmp = _startController.text;
                        _startController.text = _destController.text;
                        _destController.text = tmp;
                      },
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                _SearchField(
                  key: const Key('planner.destination'),
                  controller: _destController,
                  hint:
                      '${t.get('planner.destination')} — ${t.get('planner.searchHint')}',
                  icon: Icons.location_on_rounded,
                  onChanged: (v) => _onQueryChanged(v, _TapTarget.destination),
                  onClear: () {
                    _destController.clear();
                    ref.read(routePlannerProvider.notifier).clearDestination();
                  },
                ),
                _SearchResults(
                  onPick: (place, target) {
                    final waypoint = PlanWaypoint(
                      label: place.name,
                      lat: place.lat,
                      lon: place.lon,
                    );
                    final notifier = ref.read(routePlannerProvider.notifier);
                    if (target == _TapTarget.start) {
                      notifier.setStart(waypoint);
                      _startController.text = place.name;
                    } else {
                      notifier.setDestination(waypoint);
                      _destController.text = place.name;
                    }
                    // The controller links on the map's first frame; picks
                    // before that (or tests without layout) skip the jump.
                    if (_mapReady) {
                      _mapController.move(LatLng(place.lat, place.lon), 13);
                    }
                  },
                ),
                if (planner.stops.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  _StopsRow(
                    onRemove: (i) =>
                        ref.read(routePlannerProvider.notifier).removeStopAt(i),
                  ),
                ],
                const SizedBox(height: 8),
                SegmentedButton<String>(
                  segments: [
                    ButtonSegment(
                      value: _TapTarget.start,
                      label: Text(t.get('planner.start')),
                    ),
                    ButtonSegment(
                      value: _TapTarget.stop,
                      label: Text(t.get('planner.addStop')),
                    ),
                    ButtonSegment(
                      value: _TapTarget.destination,
                      label: Text(t.get('planner.destination')),
                    ),
                  ],
                  selected: {_tapTarget},
                  onSelectionChanged: (s) =>
                      setState(() => _tapTarget = s.first),
                ),
                const SizedBox(height: 4),
                Text(
                  t.get('planner.tapHint'),
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: _PlannerMap(
              controller: _mapController,
              onReady: () => _mapReady = true,
              onTap: _onMapTap,
            ),
          ),
          _ResultBar(
            onSave: planValue == null || _saving ? null : _save,
            saving: _saving,
          ),
        ],
      ),
    );
  }
}

class _SearchField extends StatelessWidget {
  final TextEditingController controller;
  final String hint;
  final IconData icon;
  final void Function(String) onChanged;
  final VoidCallback onClear;
  const _SearchField({
    super.key,
    required this.controller,
    required this.hint,
    required this.icon,
    required this.onChanged,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      textInputAction: TextInputAction.search,
      decoration: InputDecoration(
        hintText: hint,
        prefixIcon: Icon(icon),
        suffixIcon: controller.text.isEmpty
            ? null
            : IconButton(
                icon: const Icon(Icons.clear_rounded),
                onPressed: onClear,
              ),
      ),
      onChanged: onChanged,
      onSubmitted: onChanged,
    );
  }
}

/// Search results for the focused field: loading spinner, rows, empty and
/// error states. A pick assigns the waypoint and dismisses the list.
class _SearchResults extends ConsumerWidget {
  final void Function(PlaceResult place, String target) onPick;
  const _SearchResults({required this.onPick});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final planner = ref.watch(routePlannerProvider);
    final search = planner.search;
    if (search == null) return const SizedBox.shrink();
    return search.when(
      loading: () => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            const SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            const SizedBox(width: 8),
            Text(
              t.get('planner.searching'),
              style: const TextStyle(color: AppColors.textMuted),
            ),
          ],
        ),
      ),
      error: (e, _) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Text(
          e is ApiException &&
                  (e.code == 'NETWORK_ERROR' || e.code == 'NETWORK_TIMEOUT')
              ? t.get('history.offline')
              : t.get('planner.searchError'),
          style: const TextStyle(color: AppColors.zone4),
        ),
      ),
      data: (places) {
        if (places.isEmpty) {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Text(
              t.get('planner.noResults'),
              style: const TextStyle(color: AppColors.textMuted),
            ),
          );
        }
        final target = planner.searchTarget ?? _TapTarget.destination;
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (final place in places.take(4))
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: const Icon(
                  Icons.location_on_outlined,
                  color: AppColors.textMuted,
                ),
                title: Text(
                  place.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () => onPick(place, target),
              ),
          ],
        );
      },
    );
  }
}

class _StopsRow extends ConsumerWidget {
  final void Function(int index) onRemove;
  const _StopsRow({required this.onRemove});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final stops = ref.watch(routePlannerProvider).stops;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          t.get('planner.stops'),
          style: const TextStyle(
            fontSize: 12,
            color: AppColors.textMuted,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            for (var i = 0; i < stops.length; i++)
              Chip(
                label: Text(stops[i].label, overflow: TextOverflow.ellipsis),
                deleteIcon: const Icon(Icons.close_rounded, size: 16),
                deleteButtonTooltipMessage: t.get('planner.removeStop'),
                onDeleted: () => onRemove(i),
              ),
          ],
        ),
      ],
    );
  }
}

/// Planner map: tiles, waypoint markers, and the calculated route polyline.
///
/// Waypoints draw as start/destination/stop markers; the provider route
/// draws as one blue polyline. Taps place points per the selected mode.
class _PlannerMap extends ConsumerWidget {
  final MapController controller;
  final VoidCallback onReady;
  final void Function(LatLng point) onTap;
  const _PlannerMap({
    required this.controller,
    required this.onReady,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final config = AppConfig.current;
    final planner = ref.watch(routePlannerProvider);
    final planPoints = [
      for (final p in planner.plan?.value?.points ?? const <PlannedPoint>[])
        LatLng(p.lat, p.lon),
    ];
    final waypointMarkers = <Marker>[
      if (planner.start != null)
        _waypointMarker(planner.start!, Icons.play_arrow_rounded),
      for (final stop in planner.stops)
        Marker(
          point: LatLng(stop.lat, stop.lon),
          width: 26,
          height: 26,
          child: const DecoratedBox(
            decoration: BoxDecoration(
              color: AppColors.gold,
              shape: BoxShape.circle,
            ),
            child: Icon(Icons.circle, size: 10, color: AppColors.background),
          ),
        ),
      if (planner.destination != null)
        _waypointMarker(planner.destination!, Icons.flag_rounded),
    ];
    final initial = planPoints.isNotEmpty
        ? planPoints.first
        : planner.start != null
        ? LatLng(planner.start!.lat, planner.start!.lon)
        : const LatLng(20, 0);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Stack(
          children: [
            FlutterMap(
              mapController: controller,
              options: MapOptions(
                initialCenter: initial,
                initialZoom: planPoints.isNotEmpty ? 11 : 2,
                onMapReady: onReady,
                onTap: (_, latlng) => onTap(latlng),
              ),
              children: [
                TileLayer(
                  urlTemplate: config.mapTileUrl,
                  userAgentPackageName: 'com.cyclecoach.app',
                ),
                if (planPoints.isNotEmpty)
                  PolylineLayer(
                    polylines: [
                      Polyline(
                        points: planPoints,
                        strokeWidth: 5,
                        color: AppColors.primary,
                      ),
                    ],
                  ),
                MarkerLayer(markers: waypointMarkers),
              ],
            ),
            PositionedDirectional(
              bottom: 6,
              start: 6,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                decoration: BoxDecoration(
                  color: AppColors.background.withValues(alpha: 0.75),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  config.mapAttribution,
                  style: const TextStyle(
                    fontSize: 10,
                    color: AppColors.textOnDark,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  static Marker _waypointMarker(PlanWaypoint waypoint, IconData icon) {
    return Marker(
      point: LatLng(waypoint.lat, waypoint.lon),
      width: 30,
      height: 30,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: AppColors.background,
          shape: BoxShape.circle,
          border: Border.all(color: AppColors.primary, width: 2),
        ),
        child: Icon(icon, size: 18, color: AppColors.primary),
      ),
    );
  }
}

/// Result bar: provider distance + duration, elevation honestly
/// unavailable, plan button, and save once a route exists.
class _ResultBar extends ConsumerWidget {
  final VoidCallback? onSave;
  final bool saving;
  const _ResultBar({required this.onSave, required this.saving});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final planner = ref.watch(routePlannerProvider);
    final plan = planner.plan;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (plan != null)
            plan.when(
              loading: () => Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: 8),
                  Text(t.get('planner.planning')),
                ],
              ),
              error: (e, _) => Text(
                _errorText(t, e),
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppColors.zone4),
              ),
              data: (route) {
                if (route == null) return const SizedBox.shrink();
                return Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        _ResultStat(
                          t.get('ride.distance'),
                          formatDistance(route.distanceM, imperial: false),
                        ),
                        const SizedBox(width: 24),
                        _ResultStat(
                          t.get('planner.duration'),
                          formatDuration(route.durationS.round()),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      t.get('planner.elevationUnavailable'),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textMuted,
                      ),
                    ),
                  ],
                );
              },
            ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: FilledButton(
                  key: const Key('planner.calculate'),
                  onPressed:
                      planner.canPlan &&
                          planner.plan is! AsyncLoading<PlannedRoute?>
                      ? () => ref.read(routePlannerProvider.notifier).plan()
                      : null,
                  child: Text(t.get('planner.plan')),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton.tonal(
                  key: const Key('planner.save'),
                  onPressed: saving ? null : onSave,
                  child: saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Text(t.get('planner.save')),
                ),
              ),
            ],
          ),
          if (!planner.canPlan)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                t.get('planner.needTwoPoints'),
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textMuted,
                ),
              ),
            ),
        ],
      ),
    );
  }

  String _errorText(AppLocalizations t, Object e) {
    if (e is! ApiException) return t.get('planner.planFailed');
    if (e.code == 'ROUTE_UNREACHABLE') return t.get('planner.unreachable');
    if (e.code == 'NETWORK_ERROR' || e.code == 'NETWORK_TIMEOUT') {
      return t.get('history.offline');
    }
    return t.get('planner.planFailed');
  }
}

class _ResultStat extends StatelessWidget {
  final String label;
  final String value;
  const _ResultStat(this.label, this.value);

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          value,
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
        ),
        Text(
          label,
          style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
        ),
      ],
    );
  }
}
