import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_providers.dart';
import '../../teams/presentation/team_providers.dart';
import '../domain/competition_models.dart';
import 'competition_providers.dart';

/// The `/performance` tab: a real leaderboard, replacing the old placeholder.
///
/// Nothing here is invented or mocked. The board is the server's answer to the
/// facts this screen names — scope, period, metric — and the scope params that
/// need one (country, city, team, category) are taken from the viewer's own
/// profile/teams, never typed in. If the viewer has no such fact yet (no team,
/// no country), the scope shows why instead of firing a request the server
/// would refuse.
class RankingsPage extends ConsumerStatefulWidget {
  const RankingsPage({super.key});

  @override
  ConsumerState<RankingsPage> createState() => _RankingsPageState();
}

class _RankingsPageState extends ConsumerState<RankingsPage> {
  RankingScope _scope = RankingScope.global;
  RankingPeriod _period = RankingPeriod.weekly;
  RankingMetric _metric = RankingMetric.distance;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final profile = ref.watch(mySocialProfileProvider).value;
    final teams = ref.watch(myTeamsProvider(false)).value;

    final query = _queryFor(
      scope: _scope,
      country: profile?.countryCode,
      city: profile?.city,
      category: profile?.cyclingCategory,
      teamId: teams?.items.isEmpty == false ? teams!.items.first.id : null,
    );

    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('rankings.title')),
        actions: [
          IconButton(
            tooltip: t.get('competition.challenges'),
            icon: const Icon(Icons.emoji_events_outlined),
            onPressed: () => context.go('/challenges'),
          ),
        ],
      ),
      body: Column(
        children: [
          _ScopeSelector(
            selected: _scope,
            onChanged: (s) {
              if (s == _scope) return;
              setState(() => _scope = s);
            },
          ),
          _PeriodSelector(
            selected: _period,
            onChanged: (p) {
              if (p == _period) return;
              setState(() => _period = p);
            },
          ),
          _MetricSelector(
            selected: _metric,
            onChanged: (m) {
              if (m == _metric) return;
              setState(() => _metric = m);
            },
          ),
          const Divider(height: 1),
          Expanded(
            child: _Board(
              scope: _scope,
              query: query,
              metric: _metric,
              viewerId: profile?.userId,
            ),
          ),
        ],
      ),
    );
  }

  RankingQuery? _queryFor({
    required RankingScope scope,
    String? country,
    String? city,
    String? category,
    String? teamId,
  }) {
    switch (scope) {
      case RankingScope.global:
      case RankingScope.friends:
        return RankingQuery(scope: scope, period: _period, metric: _metric);
      case RankingScope.country:
        if (country == null || country.isEmpty) return null;
        return RankingQuery(
          scope: scope,
          period: _period,
          metric: _metric,
          country: country,
        );
      case RankingScope.city:
        if (city == null || city.isEmpty) return null;
        return RankingQuery(
          scope: scope,
          period: _period,
          metric: _metric,
          city: city,
        );
      case RankingScope.team:
        if (teamId == null || teamId.isEmpty) return null;
        return RankingQuery(
          scope: scope,
          period: _period,
          metric: _metric,
          teamId: teamId,
        );
      case RankingScope.category:
        if (category == null || category.isEmpty) return null;
        return RankingQuery(
          scope: scope,
          period: _period,
          metric: _metric,
          category: category,
        );
    }
  }
}

class _ScopeSelector extends StatelessWidget {
  final RankingScope selected;
  final ValueChanged<RankingScope> onChanged;
  const _ScopeSelector({required this.selected, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return SizedBox(
      height: 44,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
        children: [
          for (final scope in RankingScope.values)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text(t.get(scope.labelKey)),
                selected: scope == selected,
                onSelected: (_) => onChanged(scope),
              ),
            ),
        ],
      ),
    );
  }
}

class _PeriodSelector extends StatelessWidget {
  final RankingPeriod selected;
  final ValueChanged<RankingPeriod> onChanged;
  const _PeriodSelector({required this.selected, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      child: SegmentedButton<RankingPeriod>(
        segments: [
          for (final period in RankingPeriod.values)
            ButtonSegment(value: period, label: Text(t.get(period.labelKey))),
        ],
        selected: {selected},
        onSelectionChanged: (s) => onChanged(s.first),
      ),
    );
  }
}

class _MetricSelector extends StatelessWidget {
  final RankingMetric selected;
  final ValueChanged<RankingMetric> onChanged;
  const _MetricSelector({required this.selected, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return SizedBox(
      height: 44,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
        children: [
          for (final metric in RankingMetric.values)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text(t.get(metric.labelKey)),
                selected: metric == selected,
                onSelected: (_) => onChanged(metric),
              ),
            ),
        ],
      ),
    );
  }
}

class _Board extends ConsumerWidget {
  final RankingScope scope;
  final RankingQuery? query;
  final RankingMetric metric;
  final String? viewerId;

  const _Board({
    required this.scope,
    required this.query,
    required this.metric,
    this.viewerId,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final q = query;
    if (q == null) {
      return _CenteredMessage(text: t.get(_unavailableKey(scope)));
    }
    final board = ref.watch(rankingsProvider(q));
    return board.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => _CenteredMessage(
        text: '$e',
        action: FilledButton.tonal(
          onPressed: () => ref.invalidate(rankingsProvider(q)),
          child: Text(t.get('rankings.retry')),
        ),
      ),
      data: (page) {
        if (page.items.isEmpty && page.total == 0) {
          return _CenteredMessage(text: t.get('rankings.empty'));
        }
        return ListView(
          padding: const EdgeInsets.all(AppSpacing.md),
          children: [
            if (page.periodStart != null || page.periodEnd != null)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: Text(
                  t.getWith('rankings.window', {
                    'start': _ymd(page.periodStart),
                    'end': _ymd(page.periodEnd),
                  }),
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textMuted,
                  ),
                ),
              ),
            for (final row in page.items)
              _RankingTile(
                row: row,
                metric: metric,
                isViewer: row.userId == viewerId,
              ),
            if (page.viewerRank != null)
              Padding(
                padding: const EdgeInsets.only(top: AppSpacing.md),
                child: _ViewerCard(
                  rank: page.viewerRank!,
                  value: _formatValue(page.viewerValue ?? 0, metric),
                ),
              ),
          ],
        );
      },
    );
  }

  String _unavailableKey(RankingScope scope) => switch (scope) {
    RankingScope.team => 'rankings.noTeam',
    RankingScope.country => 'rankings.noCountry',
    RankingScope.city => 'rankings.noCity',
    RankingScope.category => 'rankings.noCategory',
    _ => 'rankings.empty',
  };
}

class _RankingTile extends StatelessWidget {
  final RankingRow row;
  final RankingMetric metric;
  final bool isViewer;

  const _RankingTile({
    required this.row,
    required this.metric,
    required this.isViewer,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      color: isViewer ? AppColors.primary.withValues(alpha: 0.08) : null,
      child: ListTile(
        leading: CircleAvatar(radius: 18, child: Text('${row.rank}')),
        title: Text(
          row.label,
          style: TextStyle(fontWeight: isViewer ? FontWeight.w700 : null),
        ),
        subtitle: Text('@${row.username ?? row.userId}'),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              _formatValue(row.value, metric),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            Text(
              t.getWith('rankings.rides', {'count': '${row.rides}'}),
              style: const TextStyle(fontSize: 11, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
    );
  }
}

class _ViewerCard extends StatelessWidget {
  final int rank;
  final String value;
  const _ViewerCard({required this.rank, required this.value});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: ListTile(
        leading: const Icon(Icons.person_pin_circle, color: AppColors.primary),
        title: Text(t.get('rankings.you')),
        subtitle: Text(
          t.getWith('rankings.yourRank', {'rank': '$rank', 'value': value}),
        ),
      ),
    );
  }
}

class _CenteredMessage extends StatelessWidget {
  final String text;
  final Widget? action;
  const _CenteredMessage({required this.text, this.action});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(text, textAlign: TextAlign.center),
            if (action != null) ...[
              const SizedBox(height: AppSpacing.md),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}

String _formatValue(double value, RankingMetric metric) {
  if (metric == RankingMetric.rides) return '${value.round()}';
  final rounded = value.toStringAsFixed(1);
  return rounded.endsWith('.0') ? value.toStringAsFixed(0) : rounded;
}

String _ymd(DateTime? d) {
  if (d == null) return '';
  final m = d.month.toString().padLeft(2, '0');
  final day = d.day.toString().padLeft(2, '0');
  return '${d.year}-$m-$day';
}
