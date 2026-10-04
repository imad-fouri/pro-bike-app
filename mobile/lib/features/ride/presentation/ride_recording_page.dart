import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../data/ride_recorder.dart';
import '../domain/units.dart';
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
          Expanded(child: _MapPanel(recording: recording)),
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

/// Map stand-in (map SDK lands in a later phase): topographic-style panel
/// with a traced route, Recording pill and controls.
class _MapPanel extends StatelessWidget {
  final bool recording;
  const _MapPanel({required this.recording});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.md, 0, AppSpacing.md, 0),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Stack(
          children: [
            Positioned.fill(child: CustomPaint(painter: _RouteMapPainter())),
            Positioned(
              left: 12,
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
                      color: recording ? AppColors.zone5 : AppColors.textMuted,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      recording
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
            Positioned(
              right: 12,
              top: 12,
              child: _MapFab(icon: Icons.fullscreen_rounded, onPressed: () {}),
            ),
            Positioned(
              right: 12,
              bottom: 12,
              child: _MapFab(icon: Icons.my_location_rounded, onPressed: () {}),
            ),
          ],
        ),
      ),
    );
  }
}

class _MapFab extends StatelessWidget {
  final IconData icon;
  final VoidCallback onPressed;
  const _MapFab({required this.icon, required this.onPressed});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.background.withValues(alpha: 0.85),
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onPressed,
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Icon(icon, size: 20, color: AppColors.textOnDark),
        ),
      ),
    );
  }
}

class _RouteMapPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final bg = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Color(0xFF153A31), Color(0xFF0D2622)],
      ).createShader(Offset.zero & size);
    canvas.drawRect(Offset.zero & size, bg);

    // Contour bands for a topo feel.
    final contour = Paint()
      ..color = AppColors.primary.withValues(alpha: 0.06)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    for (var i = 1; i <= 6; i++) {
      final path = Path();
      final y = size.height * i / 7;
      path.moveTo(0, y);
      path.cubicTo(
        size.width * 0.3,
        y - 24,
        size.width * 0.6,
        y + 24,
        size.width,
        y - 12,
      );
      canvas.drawPath(path, contour);
    }

    // Traced route.
    final route = Path()
      ..moveTo(size.width * 0.15, size.height * 0.85)
      ..cubicTo(
        size.width * 0.30,
        size.height * 0.62,
        size.width * 0.22,
        size.height * 0.48,
        size.width * 0.45,
        size.height * 0.42,
      )
      ..cubicTo(
        size.width * 0.68,
        size.height * 0.36,
        size.width * 0.58,
        size.height * 0.20,
        size.width * 0.84,
        size.height * 0.15,
      );
    canvas.drawPath(
      route,
      Paint()
        ..color = const Color(0xFF2F80ED)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 5
        ..strokeCap = StrokeCap.round,
    );
    canvas.drawPath(
      route,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.35)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..strokeCap = StrokeCap.round,
    );

    // Start / current markers.
    canvas.drawCircle(
      Offset(size.width * 0.15, size.height * 0.85),
      8,
      Paint()..color = AppColors.primary,
    );
    canvas.drawCircle(
      Offset(size.width * 0.15, size.height * 0.85),
      4,
      Paint()..color = AppColors.background,
    );
    final head = Offset(size.width * 0.84, size.height * 0.15);
    canvas.drawCircle(head, 10, Paint()..color = AppColors.zone5);
    canvas.drawCircle(head, 5, Paint()..color = Colors.white);
  }

  @override
  bool shouldRepaint(_RouteMapPainter oldDelegate) => false;
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
