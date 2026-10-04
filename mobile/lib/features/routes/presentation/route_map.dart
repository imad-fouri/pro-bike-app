import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../../core/config/app_config.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/route.dart';

/// Map surface for planned routes: polyline + start/end markers + attribution.
///
/// Tiles come from `MAP_TILE_URL` (raster only, no key committed). The
/// attribution chip is always rendered (OSM tile policy, §30). Offline basemap
/// tiles are out of scope — geometry still draws over an empty background.
class RouteMapView extends StatelessWidget {
  final List<RoutePointInput> points;
  final double height;
  final void Function(LatLng)? onTap;
  final void Function(LatLng)? onLongPress;
  final MapController? controller;

  const RouteMapView({
    super.key,
    required this.points,
    this.height = 260,
    this.onTap,
    this.onLongPress,
    this.controller,
  });

  List<LatLng> get _latlngs => [for (final p in points) LatLng(p.lat, p.lon)];

  @override
  Widget build(BuildContext context) {
    final config = AppConfig.current;
    final latlngs = _latlngs;
    final interactive = onTap != null;

    if (latlngs.isEmpty) {
      return SizedBox(
        height: height,
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

    final bounds = LatLngBounds.fromPoints(latlngs);
    final options = MapOptions(
      initialCenter: latlngs.first,
      initialZoom: 13,
      initialCameraFit: CameraFit.bounds(
        bounds: bounds,
        padding: const EdgeInsets.all(48),
      ),
      interactionOptions: InteractionOptions(
        flags: interactive
            ? InteractiveFlag.all & ~InteractiveFlag.rotate
            : InteractiveFlag.all,
      ),
      onTap: onTap == null ? null : (_, latlng) => onTap!(latlng),
      onLongPress: onLongPress == null
          ? null
          : (_, latlng) => onLongPress!(latlng),
    );

    return SizedBox(
      height: height,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Stack(
          children: [
            FlutterMap(
              mapController: controller,
              options: options,
              children: [
                TileLayer(
                  urlTemplate: config.mapTileUrl,
                  userAgentPackageName: 'com.cyclecoach.app',
                ),
                PolylineLayer(
                  polylines: [
                    Polyline(
                      points: latlngs,
                      strokeWidth: 5,
                      color: AppColors.primary,
                    ),
                  ],
                ),
                MarkerLayer(
                  markers: [
                    _marker(latlngs.first, Icons.play_arrow_rounded),
                    if (latlngs.length > 1)
                      _marker(latlngs.last, Icons.flag_rounded),
                  ],
                ),
              ],
            ),
            PositionedDirectional(
              bottom: 6,
              start: 6,
              child: _Attribution(text: config.mapAttribution),
            ),
          ],
        ),
      ),
    );
  }

  static Marker _marker(LatLng point, IconData icon) => Marker(
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

class _Attribution extends StatelessWidget {
  final String text;
  const _Attribution({required this.text});

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
    decoration: BoxDecoration(
      color: AppColors.background.withValues(alpha: 0.75),
      borderRadius: BorderRadius.circular(6),
    ),
    child: Text(
      text,
      style: const TextStyle(fontSize: 10, color: AppColors.textOnDark),
    ),
  );
}
