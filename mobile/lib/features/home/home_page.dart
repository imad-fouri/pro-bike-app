import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../../shared/widgets/ad_slot_widget.dart';
import '../../../shared/widgets/brand.dart';
import '../ads/domain/ad_policy.dart';
import '../auth/presentation/auth_state.dart';
import '../notifications/presentation/notification_providers.dart';
import '../notifications/presentation/notification_widgets.dart';
import '../ride/domain/ride_history.dart';
import '../ride/domain/units.dart';
import '../ride/presentation/history_providers.dart';
import '../ride/presentation/ride_providers.dart';

/// Dashboard: brand header, real overview aggregates, recent rides,
/// unfinished-ride recovery, record action, 5-tab bottom nav.
///
/// Every number below comes from persisted server data (overview aggregates
/// + history page one). No demo statistics: an empty account sees guidance
/// instead of invented cards.
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
          _UnfinishedRideBanner(),
          _SectionTitle(t.get('home.quickStats')),
          const SizedBox(height: AppSpacing.sm),
          const _OverviewStats(),
          const SizedBox(height: AppSpacing.lg),
          _RecentRides(onViewAll: () => context.go('/rides')),
          const SizedBox(height: AppSpacing.lg),
          FilledButton.icon(
            onPressed: () => context.go('/ride/start'),
            icon: const Icon(Icons.directions_bike_rounded),
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(56),
            ),
            label: Text(t.get('ride.record')),
          ),
          // The home slot, last in the scroll: below the primary action so
          // it can never push the record button out of reach. The screen
          // knows only the slot; policy, consent, and provider live in the
          // centralized widget.
          const AdSlotWidget(slot: AdSlot.home),
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

/// Server-driven overview stats with retry. An empty account gets guidance;
/// zeros render only when rides exist (true measured zeros), never as demo
/// filler — the empty branch owns the no-rides case.
class _OverviewStats extends ConsumerWidget {
  const _OverviewStats();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final overview = ref.watch(ridesOverviewProvider);
    return overview.when(
      loading: () => const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: CircularProgressIndicator(),
        ),
      ),
      error: (_, _) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(t.get('history.loadError')),
            const SizedBox(height: 12),
            FilledButton(
              onPressed: () => ref.invalidate(ridesOverviewProvider),
              child: Text(t.get('history.retry')),
            ),
          ],
        ),
      ),
      data: (o) {
        if (o.isEmpty) {
          return Column(
            children: [
              const Icon(
                Icons.directions_bike_rounded,
                size: 44,
                color: AppColors.textMuted,
              ),
              const SizedBox(height: 12),
              Text(
                t.get('home.emptyTitle'),
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                t.get('home.emptyBody'),
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppColors.textMuted),
              ),
            ],
          );
        }
        const imperial = false;
        return GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          mainAxisExtent: 92,
          children: [
            _OverviewStatCard(
              label: t.get('home.weeklyDistance'),
              value: formatDistance(o.weekDistanceM, imperial: imperial),
            ),
            _OverviewStatCard(
              label: t.get('home.monthDistance'),
              value: formatDistance(o.monthDistanceM, imperial: imperial),
            ),
            _OverviewStatCard(
              label: t.get('home.totalRides'),
              value: '${o.totalRides}',
            ),
            _OverviewStatCard(
              label: t.get('home.movingTime'),
              value: formatDuration(o.totalMovingSeconds),
            ),
            _OverviewStatCard(
              label: t.get('home.elevationGain'),
              value: formatElevation(o.totalGainM, imperial: imperial),
            ),
          ],
        );
      },
    );
  }
}

class _OverviewStatCard extends StatelessWidget {
  final String label;
  final String value;
  const _OverviewStatCard({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
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
            const SizedBox(height: 6),
            Text(
              value,
              style: Theme.of(
                context,
              ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
            ),
          ],
        ),
      ),
    );
  }
}

/// Recent completed rides (history page one, first five). Shares the feed's
/// provider, so dashboard and history can never disagree. Silent on loading
/// and empty (the overview section owns those states); retryable on error.
class _RecentRides extends ConsumerWidget {
  final VoidCallback onViewAll;
  const _RecentRides({required this.onViewAll});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final history = ref.watch(rideHistoryProvider);
    return history.when(
      loading: () => const SizedBox.shrink(),
      error: (_, _) => Center(
        child: TextButton(
          onPressed: () => ref.invalidate(rideHistoryProvider),
          child: Text(t.get('history.retry')),
        ),
      ),
      data: (state) {
        if (state.isEmpty) return const SizedBox.shrink();
        final recent = state.items.take(5).toList();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: _SectionTitle(t.get('home.recentRides'))),
                TextButton(
                  onPressed: onViewAll,
                  child: Text(t.get('home.viewAll')),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            for (final item in recent) _RecentRideTile(item: item),
          ],
        );
      },
    );
  }
}

class _RecentRideTile extends StatelessWidget {
  final RideHistoryItem item;
  const _RecentRideTile({required this.item});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: const Icon(
          Icons.directions_bike_rounded,
          color: AppColors.primary,
        ),
        title: Text(
          _formatDate(item.startedAt),
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        subtitle: Text(
          formatDistance(item.distanceM, imperial: false),
          style: const TextStyle(color: AppColors.textMuted),
        ),
        trailing: const Icon(
          Icons.chevron_right_rounded,
          color: AppColors.textMuted,
        ),
        onTap: () => context.go('/rides/${item.id}'),
      ),
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
