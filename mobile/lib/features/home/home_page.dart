import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../../shared/widgets/brand.dart';
import '../auth/presentation/auth_state.dart';
import '../notifications/presentation/notification_providers.dart';
import '../notifications/presentation/notification_widgets.dart';
import '../ride/presentation/ride_providers.dart';

/// Dashboard from the approved mockup: brand header, Today's Workout,
/// Quick Stats with sparklines, Next Event, 5-tab bottom nav.
class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  static const _tabs = [
    '/home',
    '/rides',
    '/training',
    '/performance',
    '/profile',
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final user = ref.watch(authProvider).user;
    final location = GoRouterState.of(context).matchedLocation;
    final index = _tabs.indexOf(location).clamp(0, _tabs.length - 1);

    return Scaffold(
      appBar: AppBar(
        title: const BrandLockup(),
        actions: [
          // Phase 8.4: the bell now opens the notification center rather than
          // the friend list. It used to point at '/friends', which was a
          // placeholder that became misleading the moment a real notification
          // center existed.
          NotificationBell(
            unreadCount: ref.watch(unreadCountProvider).value ?? 0,
            onTap: () => context.go('/notifications'),
          ),
          IconButton(
            icon: const Icon(Icons.person_outline_rounded),
            onPressed: () => context.go('/profile'),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          if (user != null) ...[
            Text(
              user.displayName,
              style: Theme.of(
                context,
              ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              t.get('welcome'),
              style: Theme.of(
                context,
              ).textTheme.bodyMedium?.copyWith(color: AppColors.textMuted),
            ),
            const SizedBox(height: AppSpacing.lg),
          ],
          _TodaysWorkoutCard(onViewPlan: () => context.go('/training')),
          const SizedBox(height: AppSpacing.lg),
          _UnfinishedRideBanner(),
          _SectionTitle(t.get('home.quickStats')),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: _QuickStatCard(
                  label: t.get('home.weeklyDistance'),
                  value: '180',
                  unit: 'km',
                  delta: '+12%',
                  trend: const [6, 8, 7, 10, 9, 13, 15],
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _QuickStatCard(
                  label: t.get('home.elevationGain'),
                  value: '2,400',
                  unit: 'm',
                  delta: '+18%',
                  trend: const [4, 6, 5, 9, 8, 12, 16],
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          _SectionTitle(t.get('home.nextEvent')),
          const SizedBox(height: AppSpacing.sm),
          _NextEventCard(
            meta: t.get('home.eventMeta'),
            onTap: () => context.go('/group-rides'),
          ),
          const SizedBox(height: AppSpacing.lg),
          FilledButton.icon(
            onPressed: () => context.go('/ride/start'),
            icon: const Icon(Icons.directions_bike_rounded),
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(56),
            ),
            label: Text(t.get('ride.record')),
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => context.go(_tabs[i]),
        destinations: [
          NavigationDestination(
            icon: const Icon(Icons.home_outlined),
            selectedIcon: const Icon(Icons.home_rounded),
            label: t.get('home.nav.home'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.route_outlined),
            selectedIcon: const Icon(Icons.route_rounded),
            label: t.get('home.nav.rides'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.event_note_outlined),
            selectedIcon: const Icon(Icons.event_note_rounded),
            label: t.get('home.nav.plan'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.insights_outlined),
            selectedIcon: const Icon(Icons.insights_rounded),
            label: t.get('home.nav.performance'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.person_outline_rounded),
            selectedIcon: const Icon(Icons.person_rounded),
            label: t.get('home.nav.profile'),
          ),
        ],
      ),
    );
  }
}

/// Surfaces an interrupted or half-synced ride from Home, so the existing
/// `/ride/recovery` page is reachable after a restart instead of orphaned.
/// Only renders when the recovery probe finds a row.
class _UnfinishedRideBanner extends ConsumerWidget {
  const _UnfinishedRideBanner();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final unfinished = ref.watch(unfinishedRideProvider);
    return unfinished.maybeWhen(
      data: (ride) {
        if (ride == null) return const SizedBox.shrink();
        return Column(
          children: [
            Card(
              child: ListTile(
                title: Text(t.get('ride.recovery')),
                subtitle: Text(t.get('ride.unfinishedBody')),
                trailing: FilledButton(
                  onPressed: () => context.go('/ride/recovery'),
                  child: Text(t.get('ride.resumeRide')),
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
          ],
        );
      },
      orElse: () => const SizedBox.shrink(),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  final String text;
  const _SectionTitle(this.text);

  @override
  Widget build(BuildContext context) => Text(
    text,
    style: Theme.of(
      context,
    ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
  );
}

class _TodaysWorkoutCard extends StatelessWidget {
  final VoidCallback onViewPlan;
  const _TodaysWorkoutCard({required this.onViewPlan});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AppRadius.card),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF1E4D3B), Color(0xFF12352C)],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            t.get('home.todaysWorkout'),
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            t.get('home.workoutType'),
            style: const TextStyle(
              color: AppColors.accentLime,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              _Pill(label: t.get('home.workoutDistance'), icon: Icons.route),
              const SizedBox(width: 8),
              _Pill(
                label: t.get('home.workoutDuration'),
                icon: Icons.timer_outlined,
              ),
              const SizedBox(width: 8),
              _Pill(
                label: t.get('home.workoutZone'),
                icon: Icons.favorite_outline,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Align(
            alignment: Alignment.centerLeft,
            child: FilledButton.icon(
              onPressed: onViewPlan,
              icon: const Icon(Icons.directions_bike_rounded, size: 18),
              label: Text(t.get('home.viewPlan')),
            ),
          ),
        ],
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  final String label;
  final IconData icon;
  const _Pill({required this.label, required this.icon});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: AppColors.primary),
          const SizedBox(width: 6),
          Text(
            label,
            style: const TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: AppColors.textOnDark,
            ),
          ),
        ],
      ),
    );
  }
}

class _QuickStatCard extends StatelessWidget {
  final String label;
  final String value;
  final String unit;
  final String delta;
  final List<double> trend;
  const _QuickStatCard({
    required this.label,
    required this.value,
    required this.unit,
    required this.delta,
    required this.trend,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
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
            const SizedBox(height: 6),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  value,
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(width: 4),
                Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Text(
                    unit,
                    style: const TextStyle(
                      color: AppColors.textMuted,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                const Spacer(),
                Text(
                  delta,
                  style: const TextStyle(
                    color: AppColors.primary,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            SizedBox(
              height: 36,
              width: double.infinity,
              child: CustomPaint(
                painter: _SparklinePainter(
                  values: trend,
                  color: AppColors.primary,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _NextEventCard extends StatelessWidget {
  final String meta;
  final VoidCallback onTap;
  const _NextEventCard({required this.meta, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: InkWell(
        // Phase 9: the "next event" of a cycling app is a scheduled group ride.
        // It pointed at `/routes`, which is not an event at all - a route is a
        // path, and routes already have their own tab and profile entry.
        key: const Key('home.nextEvent'),
        borderRadius: BorderRadius.circular(AppRadius.card),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Row(
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  gradient: const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [AppColors.primary, AppColors.primaryDark],
                  ),
                ),
                child: const Icon(
                  Icons.terrain_rounded,
                  color: AppColors.background,
                  size: 28,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      t.get('home.eventTitle'),
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      meta,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textMuted,
                      ),
                    ),
                  ],
                ),
              ),
              const Icon(
                Icons.chevron_right_rounded,
                color: AppColors.textMuted,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  final List<double> values;
  final Color color;
  const _SparklinePainter({required this.values, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final min = values.reduce((a, b) => a < b ? a : b);
    final max = values.reduce((a, b) => a > b ? a : b);
    final span = (max - min) == 0 ? 1.0 : max - min;
    final step = size.width / (values.length - 1);
    final points = [
      for (var i = 0; i < values.length; i++)
        Offset(
          i * step,
          size.height - ((values[i] - min) / span) * size.height,
        ),
    ];
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (var i = 1; i < points.length; i++) {
      path.lineTo(points[i].dx, points[i].dy);
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );
    final area = Path()..moveTo(points.first.dx, points.first.dy);
    for (var i = 1; i < points.length; i++) {
      area.lineTo(points[i].dx, points[i].dy);
    }
    area
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(area, Paint()..color = color.withValues(alpha: 0.12));
  }

  @override
  bool shouldRepaint(_SparklinePainter old) =>
      old.values != values || old.color != color;
}
