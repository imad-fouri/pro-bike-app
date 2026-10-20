import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../../core/config/app_config.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../data/ride_recorder.dart';

/// Live ride map: OSM tiles, accepted-track polyline, rider marker.
///
/// The drawn track is exactly what the meters came from — [LiveRide.track]
/// holds metered (accepted) fixes only. An optional planned route draws as
/// a separate dashed layer underneath: the two geometries never mix, and
/// the rider is never forced to follow the plan.
class RideLiveMap extends StatefulWidget {
  final LiveRide live;
  final bool recording;
  final List<LatLng> planned;
  const RideLiveMap({
    super.key,
    required this.live,
    required this.recording,
    this.planned = const [],
  });

  @override
  State<RideLiveMap> createState() => _RideLiveMapState();
}

class _RideLiveMapState extends State<RideLiveMap> {
  final MapController _controller = MapController();
  bool _follow = true;
  bool _tilesFailed = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  List<LatLng> get _points => [
    for (final p in widget.live.track) LatLng(p.lat, p.lon),
  ];

  @override
  void didUpdateWidget(RideLiveMap oldWidget) {
    super.didUpdateWidget(oldWidget);
    final points = _points;
    if (_follow && points.isNotEmpty) {
      _controller.move(points.last, _controller.camera.zoom);
    }
  }

  void _recenter() {
    final points = _points;
    if (points.isEmpty) return;
    setState(() => _follow = true);
    _controller.move(points.last, 16);
  }

  void _fitTrack() {
    final points = _points;
    if (points.length < 2) {
      _recenter();
      return;
    }
    _controller.fitCamera(
      CameraFit.bounds(
        bounds: LatLngBounds.fromPoints(points),
        padding: const EdgeInsets.all(48),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final config = AppConfig.current;
    final points = _points;
    final latest = points.isEmpty ? null : points.last;

    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.md, 0, AppSpacing.md, 0),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Stack(
          children: [
            FlutterMap(
              mapController: _controller,
              options: MapOptions(
                initialCenter: latest ?? const LatLng(0, 0),
                initialZoom: latest == null ? 2 : 16,
                onPositionChanged: (position, hasGesture) {
                  // Manual exploration pauses follow; recenter resumes it.
                  if (hasGesture && _follow) {
                    setState(() => _follow = false);
                  }
                },
              ),
              children: [
                TileLayer(
                  urlTemplate: config.mapTileUrl,
                  userAgentPackageName: 'com.cyclecoach.app',
                  errorTileCallback: (tile, error, stackTrace) {
                    if (mounted && !_tilesFailed) {
                      setState(() => _tilesFailed = true);
                    }
                  },
                ),
                if (widget.planned.isNotEmpty || points.isNotEmpty)
                  PolylineLayer(
                    polylines: [
                      // Planned route first (underneath): dashed muted line,
                      // visually distinct from the recorded solid track.
                      if (widget.planned.isNotEmpty)
                        Polyline(
                          points: widget.planned,
                          strokeWidth: 5,
                          color: AppColors.textMuted,
                          pattern: StrokePattern.dashed(
                            segments: const [10.0, 8.0],
                          ),
                        ),
                      if (points.isNotEmpty)
                        Polyline(
                          points: points,
                          strokeWidth: 5,
                          color: AppColors.primary,
                        ),
                    ],
                  ),
                if (latest != null)
                  MarkerLayer(
                    markers: [
                      if (points.length > 1)
                        Marker(
                          point: points.first,
                          width: 24,
                          height: 24,
                          child: DecoratedBox(
                            decoration: BoxDecoration(
                              color: AppColors.background,
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: AppColors.primary,
                                width: 2,
                              ),
                            ),
                            child: const Icon(
                              Icons.play_arrow_rounded,
                              size: 14,
                              color: AppColors.primary,
                            ),
                          ),
                        ),
                      Marker(
                        point: latest,
                        width: 30,
                        height: 30,
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            color: AppColors.zone5,
                            shape: BoxShape.circle,
                            border: Border.all(color: Colors.white, width: 3),
                          ),
                        ),
                      ),
                    ],
                  ),
              ],
            ),
            PositionedDirectional(
              start: 12,
              top: 12,
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 6,
                ),
                decoration: BoxDecoration(
                  color: AppColors.background.withValues(alpha: 0.85),
                  borderRadius: BorderRadius.circular(AppRadius.pill),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.circle,
                      size: 10,
                      color: widget.recording
                          ? AppColors.zone5
                          : AppColors.textMuted,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      widget.recording
                          ? t.get('ride.recording')
                          : t.get('ride.paused'),
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            if (latest == null)
              PositionedDirectional(
                start: 12,
                bottom: 12,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.background.withValues(alpha: 0.85),
                    borderRadius: BorderRadius.circular(AppRadius.pill),
                  ),
                  child: Text(
                    t.get('ride.waitingFix'),
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
              ),
            if (_tilesFailed && latest != null)
              PositionedDirectional(
                start: 12,
                bottom: 12,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.background.withValues(alpha: 0.85),
                    borderRadius: BorderRadius.circular(AppRadius.pill),
                  ),
                  child: Text(
                    t.get('ride.tilesOffline'),
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
              ),
            PositionedDirectional(
              bottom: 6,
              end: 54,
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
            PositionedDirectional(
              end: 12,
              top: 12,
              child: _LiveMapFab(
                icon: Icons.fullscreen_rounded,
                tooltip: t.get('ride.fitTrack'),
                onPressed: points.length > 1 ? _fitTrack : null,
              ),
            ),
            PositionedDirectional(
              end: 12,
              bottom: 12,
              child: _LiveMapFab(
                icon: Icons.my_location_rounded,
                tooltip: t.get('ride.recenter'),
                highlighted: _follow && latest != null,
                onPressed: latest == null ? null : _recenter,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LiveMapFab extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool highlighted;
  const _LiveMapFab({
    required this.icon,
    required this.tooltip,
    this.onPressed,
    this.highlighted = false,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: AppColors.background.withValues(alpha: 0.85),
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onPressed,
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: Icon(
              icon,
              size: 20,
              color: highlighted ? AppColors.primary : AppColors.textOnDark,
            ),
          ),
        ),
      ),
    );
  }
}
