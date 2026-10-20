import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:latlong2/latlong.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../routes/presentation/routes_providers.dart';
import '../data/ride_recorder.dart';
import '../domain/units.dart';
import 'ride_live_map.dart';
import 'ride_providers.dart';

/// Recording screen from the mockup: map panel with Recording pill,
/// glanceable metric grid, big round pause control, finish requires
/// confirmation.
class RideRecordingPage extends ConsumerWidget {
  const RideRecordingPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final live = ref.watch(rideSessionProvider);
    if (live == null) {
      // No active ride (e.g. deep link) → start flow.
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => context.go('/ride/start'),
      );
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    const imperial = false; // TODO Phase 5: profile measurement system
    final m = live.metrics;
    final recording = live.phase == RecordingPhase.recording;
    // Planned-route overlay: display-only geometry for the pinned route, if
    // any. Unavailable or failed geometry leaves the live track untouched.
    final planned = live.routeId == null
        ? const <LatLng>[]
        : ref
              .watch(routeGeometryProvider(live.routeId!))
              .maybeWhen(
                data: (g) => [for (final p in g.points) LatLng(p.lat, p.lon)],
                orElse: () => const <LatLng>[],
              );
    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('ride.liveRide')),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => _confirmFinish(context, ref),
        ),
        actions: [
          if (live.syncState != 'idle')
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: Center(
                child: Text(
                  t.get('ride.sync.${live.syncState}'),
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textMuted,
                  ),
                ),
              ),
            ),
          _GpsDot(quality: live.gpsQuality),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: RideLiveMap(
              live: live,
              recording: recording,
              planned: planned,
            ),
          ),
          if (live.backgroundLimited)
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.md,
                AppSpacing.sm,
                AppSpacing.md,
                0,
              ),
              child: Text(
                t.get('ride.bgLimited'),
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: AppColors.zone3),
              ),
            ),
          const SizedBox(height: AppSpacing.md),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
            child: Column(
              children: [
                _MetricTile(
                  label: t.get('ride.distance'),
                  value: formatDistance(m.distanceM, imperial: imperial),
                  hero: true,
                ),
                const SizedBox(height: AppSpacing.sm),
                Row(
                  children: [
                    Expanded(
                      child: _MetricTile(
                        label: t.get('ride.speed'),
                        value: formatSpeed(
                          live.currentSpeedMS ?? 0,
                          imperial: imperial,
                        ),
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: _MetricTile(
                        label: t.get('ride.avgSpeed'),
                        value: formatSpeed(m.avgSpeedMS, imperial: imperial),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.sm),
                Row(
                  children: [
                    Expanded(
                      child: _MetricTile(
                        label: t.get('ride.climb'),
                        value: formatElevation(m.gainM, imperial: imperial),
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: _MetricTile(
                        label: t.get('ride.heartRate'),
                        value: '—',
                        valueIcon: Icons.favorite_rounded,
                        iconColor: AppColors.zone5,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: _MetricTile(
                        label: t.get('ride.cadence'),
                        value: '—',
                      ),
                    ),
                  ],
                ),
                if (!recording)
                  Padding(
                    padding: const EdgeInsets.only(top: AppSpacing.md),
                    child: Text(
                      t.get('ride.paused'),
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: AppColors.zone3,
                        fontWeight: FontWeight.w700,
                        fontSize: 16,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              0,
              AppSpacing.md,
              AppSpacing.lg,
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                _RoundControl(
                  icon: Icons.stop_rounded,
                  tooltip: t.get('ride.finish'),
                  color: AppColors.zone5,
                  onPressed: () => _confirmFinish(context, ref),
                ),
                const SizedBox(width: 48),
                _PrimaryPauseButton(
                  recording: recording,
                  label: recording ? t.get('ride.pause') : t.get('ride.resume'),
                  onPause: () => ref.read(rideSessionProvider.notifier).pause(),
                  onResume: () =>
                      ref.read(rideSessionProvider.notifier).resume(),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmFinish(BuildContext context, WidgetRef ref) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(t.get('ride.finishTitle')),
        content: Text(t.get('ride.finishBody')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: Text(t.get('bikes.cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(t.get('ride.finish')),
          ),
        ],
      ),
    );
    if (ok == true && context.mounted) {
      await ref.read(rideSessionProvider.notifier).finish();
      if (context.mounted) context.go('/ride/summary');
    }
  }
}

class _MetricTile extends StatelessWidget {
  final String label;
  final String value;
  final bool hero;
  final IconData? valueIcon;
  final Color? iconColor;
  const _MetricTile({
    required this.label,
    required this.value,
    this.hero = false,
    this.valueIcon,
    this.iconColor,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.all(hero ? AppSpacing.md : AppSpacing.sm + 4),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
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
          Row(
            children: [
              if (valueIcon != null) ...[
                Icon(valueIcon, size: 18, color: iconColor),
                const SizedBox(width: 6),
              ],
              Text(
                value,
                style: TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: hero ? 40 : 22,
                  height: 1.1,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _PrimaryPauseButton extends StatelessWidget {
  final bool recording;
  final String label;
  final VoidCallback onPause;
  final VoidCallback onResume;
  const _PrimaryPauseButton({
    required this.recording,
    required this.label,
    required this.onPause,
    required this.onResume,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: label,
      child: Material(
        key: const Key('ride-pause-button'),
        color: recording ? AppColors.primary : AppColors.zone5,
        shape: const CircleBorder(),
        elevation: 4,
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: recording ? onPause : onResume,
          child: SizedBox(
            width: 76,
            height: 76,
            child: Icon(
              recording ? Icons.pause_rounded : Icons.play_arrow_rounded,
              size: 40,
              color: AppColors.background,
            ),
          ),
        ),
      ),
    );
  }
}

class _RoundControl extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;
  final Color? color;
  const _RoundControl({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface2,
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onPressed,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Icon(icon, size: 24, color: color ?? AppColors.textOnDark),
        ),
      ),
    );
  }
}

class _GpsDot extends StatelessWidget {
  final String quality;
  const _GpsDot({required this.quality});

  @override
  Widget build(BuildContext context) {
    final color = switch (quality) {
      'good' => AppColors.primary,
      'degraded' => AppColors.zone4,
      _ => AppColors.textMuted,
    };
    return Padding(
      padding: const EdgeInsets.only(right: 16),
      child: Icon(Icons.circle, color: color, size: 14),
    );
  }
}
