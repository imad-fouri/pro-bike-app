import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../bikes/presentation/bikes_providers.dart';
import '../domain/ride_history.dart';
import '../domain/units.dart';
import 'history_providers.dart';

/// Completed-ride history, newest first. Server-paginated with pull to
/// refresh; every number comes from persisted server summaries.
///
/// Dates render numeric (locale-neutral, RTL-safe) rather than through
/// locale date symbols, which would need async symbol initialization per
/// locale. Bike names join client-side from the already-fetched bike list.
class RideHistoryPage extends ConsumerStatefulWidget {
  const RideHistoryPage({super.key});

  @override
  ConsumerState<RideHistoryPage> createState() => _RideHistoryPageState();
}

class _RideHistoryPageState extends ConsumerState<RideHistoryPage> {
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    // 0.8 of the scrolled extent: a page lands before the rider hits the end.
    if (_scroll.position.pixels >= _scroll.position.maxScrollExtent * 0.8) {
      ref.read(rideHistoryProvider.notifier).loadMore();
    }
  }

  Future<void> _refresh() => ref.read(rideHistoryProvider.notifier).refresh();

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final history = ref.watch(rideHistoryProvider);
    return Scaffold(
      appBar: AppBar(title: Text(t.get('history.title'))),
      body: history.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _ErrorView(
          messageKey:
              e is ApiException &&
                  (e.code == 'NETWORK_ERROR' || e.code == 'NETWORK_TIMEOUT')
              ? 'history.offline'
              : 'history.loadError',
          onRetry: _refresh,
        ),
        data: (state) {
          if (state.isEmpty) {
            return RefreshIndicator(
              onRefresh: _refresh,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(24),
                children: [
                  const SizedBox(height: 64),
                  const Icon(
                    Icons.directions_bike_rounded,
                    size: 56,
                    color: AppColors.textMuted,
                  ),
                  const SizedBox(height: 16),
                  Text(
                    t.get('history.emptyTitle'),
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    t.get('history.emptyBody'),
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: AppColors.textMuted),
                  ),
                  const SizedBox(height: 24),
                  FilledButton.icon(
                    onPressed: () => context.go('/ride/start'),
                    icon: const Icon(Icons.add_rounded),
                    label: Text(t.get('ride.record')),
                  ),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView.builder(
              controller: _scroll,
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
              itemCount: state.items.length + (state.hasMore ? 1 : 0),
              itemBuilder: (context, i) {
                if (i >= state.items.length) {
                  return const Padding(
                    padding: EdgeInsets.symmetric(vertical: 16),
                    child: Center(
                      child: SizedBox(
                        width: 24,
                        height: 24,
                        child: CircularProgressIndicator(strokeWidth: 2.5),
                      ),
                    ),
                  );
                }
                return _HistoryTile(item: state.items[i]);
              },
            ),
          );
        },
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String messageKey;
  final Future<void> Function() onRetry;
  const _ErrorView({required this.messageKey, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.cloud_off_rounded,
              size: 48,
              color: AppColors.textMuted,
            ),
            const SizedBox(height: 12),
            Text(t.get(messageKey), textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: onRetry,
              child: Text(t.get('history.retry')),
            ),
          ],
        ),
      ),
    );
  }
}

class _HistoryTile extends ConsumerWidget {
  final RideHistoryItem item;
  const _HistoryTile({required this.item});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final bikes = ref.watch(bikeListProvider).value ?? const [];
    String? bikeName;
    for (final b in bikes) {
      if (b.id == item.bikeId) {
        bikeName = b.name;
        break;
      }
    }
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        borderRadius: BorderRadius.circular(AppRadius.card),
        onTap: () => context.go('/rides/${item.id}'),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _formatDate(item.startedAt),
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                  ),
                  const Icon(
                    Icons.chevron_right_rounded,
                    color: AppColors.textMuted,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                formatDistance(item.distanceM, imperial: false),
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 16,
                runSpacing: 4,
                children: [
                  _Stat(
                    Icons.timer_outlined,
                    formatDuration(item.movingSeconds),
                  ),
                  _Stat(
                    Icons.speed_rounded,
                    formatSpeed(item.avgSpeedMS, imperial: false),
                  ),
                  _Stat(
                    Icons.trending_up_rounded,
                    formatElevation(item.gainM, imperial: false),
                  ),
                  if (bikeName != null)
                    _Stat(Icons.pedal_bike_rounded, bikeName),
                ],
              ),
              // Accessibility label carries the same facts for screen readers.
              Semantics(
                label:
                    '${_formatDate(item.startedAt)}, '
                    '${formatDistance(item.distanceM, imperial: false)}, '
                    '${t.get('ride.moving')} '
                    '${formatDuration(item.movingSeconds)}',
                child: const SizedBox.shrink(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  final IconData icon;
  final String text;
  const _Stat(this.icon, this.text);

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 14, color: AppColors.textMuted),
        const SizedBox(width: 4),
        Text(
          text,
          style: const TextStyle(fontSize: 13, color: AppColors.textMuted),
        ),
      ],
    );
  }
}

/// Numeric `YYYY-MM-DD HH:MM` in the device timezone: unambiguous in every
/// locale and safe under RTL (digits keep their order).
String _formatDate(DateTime? at) {
  if (at == null) return '—';
  final l = at.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${l.year}-${two(l.month)}-${two(l.day)} '
      '${two(l.hour)}:${two(l.minute)}';
}
