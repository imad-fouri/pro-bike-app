import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:latlong2/latlong.dart';

import '../../../core/config/app_config.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../bikes/presentation/bikes_providers.dart';
import '../domain/units.dart';
import 'history_providers.dart';

/// Completed-ride detail: actual recorded geometry on the shared map
/// configuration, persisted server metrics, and honest unavailable states.
///
/// Nothing is computed here: every number below is the server's persisted
/// summary, and the polyline is the accepted-fix trail the meters came
/// from. Metrics missing from the payload render as '—', never as zeros.
class RideDetailPage extends ConsumerWidget {
  final String rideId;
  const RideDetailPage({super.key, required this.rideId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final detail = ref.watch(rideDetailProvider(rideId));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('detail.title'))),
      body: detail.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) {
          final notFound =
              e is ApiException &&
              (e.status == 404 || e.code == 'RIDE_NOT_FOUND');
          return Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(
                    Icons.search_off_rounded,
                    size: 48,
                    color: AppColors.textMuted,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    t.get(notFound ? 'detail.notFound' : 'detail.loadError'),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 16),
                  if (notFound)
                    FilledButton(
                      onPressed: () => context.go('/rides'),
                      child: Text(t.get('history.title')),
                    )
                  else
                    FilledButton(
                      onPressed: () =>
                          ref.invalidate(rideDetailProvider(rideId)),
                      child: Text(t.get('history.retry')),
                    ),
                ],
              ),
            ),
          );
        },
        data: (state) => _DetailBody(key: ValueKey(rideId), rideId: rideId),
      ),
    );
  }
}

class _DetailBody extends ConsumerWidget {
  final String rideId;
  const _DetailBody({super.key, required this.rideId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final state = ref.watch(rideDetailProvider(rideId)).value!;
    final ride = state.ride;
    final bikes = ref.watch(bikeListProvider).value ?? const [];
    String? bikeName;
    for (final b in bikes) {
      if (b.id == ride.bikeId) {
        bikeName = b.name;
        break;
      }
    }
    const imperial = false;
    final points = [for (final p in state.points) LatLng(p.lat, p.lon)];
    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(rideDetailProvider(rideId)),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          _DetailMap(points: points),
          if (state.truncated) ...[
            const SizedBox(height: 8),
            Text(
              t.getWith('detail.truncatedTrack', {'count': '${points.length}'}),
              style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
            ),
          ],
          const SizedBox(height: 16),
          Text(
            formatDistance(ride.distanceM, imperial: imperial),
            style: Theme.of(
              context,
            ).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 12),
          _MetricGrid(
            entries: [
              (
                t.get('ride.elapsed'),
                ride.elapsedSeconds > 0
                    ? formatDuration(ride.elapsedSeconds)
                    : '—',
              ),
              (
                t.get('ride.moving'),
                ride.movingSeconds > 0
                    ? formatDuration(ride.movingSeconds)
                    : '—',
              ),
              (
                t.get('ride.avgSpeed'),
                formatSpeed(ride.avgSpeedMS, imperial: imperial),
              ),
              (
                t.get('ride.maxSpeed'),
                formatSpeed(ride.maxSpeedMS, imperial: imperial),
              ),
              (
                t.get('ride.climb'),
                formatElevation(ride.gainM, imperial: imperial),
              ),
              (t.get('detail.started'), _formatDate(ride.startedAt)),
              (t.get('detail.finished'), _formatDate(ride.endedAt)),
              (t.get('ride.bike'), bikeName ?? '—'),
            ],
          ),
        ],
      ),
    );
  }
}

class _DetailMap extends StatelessWidget {
  final List<LatLng> points;
  const _DetailMap({required this.points});

  @override
  Widget build(BuildContext context) {
    final config = AppConfig.current;
    if (points.isEmpty) {
      return SizedBox(
        height: 220,
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(AppRadius.card),
            border: Border.all(color: AppColors.surface2),
          ),
          child: Center(
            child: Text(
              config.mapAttribution,
              style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
            ),
          ),
        ),
      );
    }
    return SizedBox(
      height: 280,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Stack(
          children: [
            FlutterMap(
              options: MapOptions(
                initialCenter: points.first,
                initialZoom: 13,
                initialCameraFit: CameraFit.bounds(
                  bounds: LatLngBounds.fromPoints(points),
                  padding: const EdgeInsets.all(48),
                ),
              ),
              children: [
                TileLayer(
                  urlTemplate: config.mapTileUrl,
                  userAgentPackageName: 'com.cyclecoach.app',
                ),
                PolylineLayer(
                  polylines: [
                    Polyline(
                      points: points,
                      strokeWidth: 5,
                      color: AppColors.primary,
                    ),
                  ],
                ),
                MarkerLayer(
                  markers: [
                    _dot(points.first, Icons.play_arrow_rounded),
                    if (points.length > 1)
                      _dot(points.last, Icons.flag_rounded),
                  ],
                ),
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

  static Marker _dot(LatLng point, IconData icon) => Marker(
    point: point,
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

class _MetricGrid extends StatelessWidget {
  final List<(String, String)> entries;
  const _MetricGrid({required this.entries});

  @override
  Widget build(BuildContext context) {
    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        mainAxisSpacing: 12,
        crossAxisSpacing: 12,
        mainAxisExtent: 76,
      ),
      itemCount: entries.length,
      itemBuilder: (context, i) {
        final (label, value) = entries[i];
        return Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(AppRadius.card),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                label,
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textMuted,
                  fontWeight: FontWeight.w500,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                value,
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

/// Numeric `YYYY-MM-DD HH:MM` in the device timezone (see history feed).
String _formatDate(DateTime? at) {
  if (at == null) return '—';
  final l = at.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${l.year}-${two(l.month)}-${two(l.day)} '
      '${two(l.hour)}:${two(l.minute)}';
}
