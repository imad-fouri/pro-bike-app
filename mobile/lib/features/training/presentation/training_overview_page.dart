import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../domain/training_models.dart';
import '../domain/training_units.dart';
import '../presentation/training_providers.dart';

/// FTP, load and recovery. Every number that could not be calculated is
/// rendered as unavailable with the reason, never as 0 (ADR-10 §2).
class TrainingOverviewPage extends ConsumerWidget {
  const TrainingOverviewPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = AppLocalizations.of(context);
    final summary = ref.watch(trainingSummaryProvider);
    return summary.when(
      loading: () => const Center(
        child: CircularProgressIndicator(key: Key('training.loading')),
      ),
      error: (e, _) => _ErrorView(
        message: l10n.get('training.error.load'),
        onRetry: () => ref.invalidate(trainingSummaryProvider),
      ),
      data: (data) => RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(trainingSummaryProvider);
          await ref.read(trainingSummaryProvider.future);
        },
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _FtpCard(profile: data.profile),
            const SizedBox(height: 16),
            _LoadCard(summary: data),
            const SizedBox(height: 16),
            _RecoveryCard(recovery: data.recovery),
            const SizedBox(height: 16),
            _SuggestionCard(suggestion: data.suggestion),
            const SizedBox(height: 16),
            Text(
              l10n.get('training.recentActivities'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (data.recentActivities.isEmpty)
              _EmptyHint(text: l10n.get('training.noActivitiesHint'))
            else
              ...data.recentActivities.map(
                (a) => ActivityTile(
                  activity: a,
                  onReanalyze: a.rideId == null
                      ? null
                      : () => _reanalyze(context, ref, a.rideId!),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _reanalyze(
    BuildContext context,
    WidgetRef ref,
    String rideId,
  ) async {
    final l10n = AppLocalizations.of(context);
    final messenger = ScaffoldMessenger.of(context);
    final result = await ref.read(reanalyzeProvider.notifier).run(rideId);
    messenger.showSnackBar(
      SnackBar(
        content: Text(
          result == null
              ? l10n.get('training.unavailable')
              : l10n.get('training.reanalyzeDone'),
        ),
      ),
    );
  }
}

class _FtpCard extends StatelessWidget {
  final TrainingProfile profile;
  const _FtpCard({required this.profile});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return Card(
      key: const Key('training.ftpCard'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              l10n.get('training.profile'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Text(
                  l10n.get('training.ftp'),
                  style: theme.textTheme.bodyMedium,
                ),
                const SizedBox(width: 12),
                // The basis travels with the value: an estimate is never shown
                // as if it were measured.
                Text(
                  formatFtp(profile.ftpW),
                  key: const Key('training.ftpValue'),
                  style: theme.textTheme.headlineSmall,
                ),
                const SizedBox(width: 8),
                _BasisChip(profile: profile),
              ],
            ),
            if (profile.ftpSource != null) ...[
              const SizedBox(height: 4),
              Text(
                '${l10n.get('training.ftpSource.${profile.ftpSource!.wire}')} · '
                '${l10n.get('training.ftpUsed')}: ${formatFtp(profile.ftpW)}',
                style: theme.textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 8),
            Text(
              l10n.get('training.effectiveTimezone'),
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _BasisChip extends StatelessWidget {
  final TrainingProfile profile;
  const _BasisChip({required this.profile});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final key = 'training.ftpBasis.${profile.ftpBasis.name}';
    return Chip(
      key: Key('training.basis.${profile.ftpBasis.name}'),
      visualDensity: VisualDensity.compact,
      label: Text(l10n.get(key)),
    );
  }
}

class _LoadCard extends StatelessWidget {
  final TrainingSummary summary;
  const _LoadCard({required this.summary});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final latest = summary.loads.isEmpty ? null : summary.loads.last;
    return Card(
      key: const Key('training.loadCard'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              l10n.get('training.loads'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                _Metric(
                  label: l10n.get('training.ctl'),
                  value: latest == null ? unavailable : formatLoad(latest.ctl),
                  keyName: 'training.ctl',
                ),
                _Metric(
                  label: l10n.get('training.atl'),
                  value: latest == null ? unavailable : formatLoad(latest.atl),
                  keyName: 'training.atl',
                ),
                _Metric(
                  label: l10n.get('training.tsb'),
                  value: latest == null ? unavailable : formatLoad(latest.tsb),
                  keyName: 'training.tsb',
                ),
              ],
            ),
            const Divider(height: 24),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  l10n.get('training.weekLoad'),
                  style: theme.textTheme.bodyMedium,
                ),
                Text(
                  formatLoad(summary.weekPowerLoad),
                  key: const Key('training.weekLoad'),
                  style: theme.textTheme.titleMedium,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  final String label;
  final String value;
  final String keyName;
  const _Metric({
    required this.label,
    required this.value,
    required this.keyName,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: theme.textTheme.bodySmall),
        const SizedBox(height: 2),
        Text(value, key: Key(keyName), style: theme.textTheme.titleLarge),
      ],
    );
  }
}

class _RecoveryCard extends StatelessWidget {
  final RecoverySignals recovery;
  const _RecoveryCard({required this.recovery});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return Card(
      key: const Key('training.recoveryCard'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              l10n.get('training.recovery'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            Text(
              // No readiness claim, ever: this is a statement about load.
              l10n.get(recovery.l10nKey),
              key: const Key('training.recoveryStatus'),
              style: theme.textTheme.bodyMedium,
            ),
            if (recovery.isAvailable)
              ...recovery.signals.map(
                (s) => Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('• ${l10n.get('training.recovery.$s')}'),
                ),
              ),
            const SizedBox(height: 8),
            Text(
              '${l10n.get('training.version')}: ${recovery.version}',
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  final IntensitySuggestion suggestion;
  const _SuggestionCard({required this.suggestion});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return Card(
      key: const Key('training.suggestionCard'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              l10n.get('training.suggestion.ok'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            Text(
              suggestion.isAvailable
                  ? '${l10n.get('training.load')}: ${formatLoad(suggestion.targetLoad)}'
                  : l10n.get(suggestion.l10nKey),
              key: const Key('training.suggestionValue'),
              style: theme.textTheme.bodyLarge,
            ),
            const SizedBox(height: 4),
            Text(suggestion.reason, style: theme.textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

/// One analyzed activity. Sensor-derived rows are only shown when the ride
/// actually had that channel.
class ActivityTile extends StatelessWidget {
  final TrainingActivity activity;
  final VoidCallback? onReanalyze;

  const ActivityTile({super.key, required this.activity, this.onReanalyze});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    // The unit preference arrives with settings (§43); the API stays metric.
    final imperial = false;
    return Card(
      key: Key('training.activity.${activity.id}'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  formatTrainingDuration(activity.movingSeconds),
                  style: theme.textTheme.titleMedium,
                ),
                Text(
                  formatTrainingDistance(
                    activity.distanceM,
                    imperial: imperial,
                  ),
                  style: theme.textTheme.titleMedium,
                ),
              ],
            ),
            if (activity.hasNoSensors) ...[
              const SizedBox(height: 8),
              Text(
                l10n.get('training.noSensors'),
                key: const Key('training.noSensors'),
                style: theme.textTheme.bodySmall,
              ),
            ] else ...[
              if (activity.hasPower) ...[
                const SizedBox(height: 8),
                _Row(
                  label: l10n.get('training.normalizedPower'),
                  value: formatPower(activity.normalizedPowerW),
                  keyName: 'training.np',
                ),
                _Row(
                  label: l10n.get('training.averagePower'),
                  value: formatPower(activity.averagePowerW),
                  keyName: 'training.avgPower',
                ),
                _Row(
                  label: l10n.get('training.intensityFactor'),
                  value: formatIf(activity.intensityFactor),
                  keyName: 'training.if',
                ),
                _Row(
                  label: l10n.get('training.load'),
                  value: formatLoad(activity.powerLoad),
                  keyName: 'training.activityLoad',
                ),
              ],
              if (activity.hasHeartRate)
                _Row(
                  label: l10n.get('training.averageHr'),
                  value: formatHr(activity.averageHrBpm),
                  keyName: 'training.avgHr',
                ),
            ],
            if (activity.insufficientData)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  l10n.get('training.insufficientData'),
                  style: theme.textTheme.bodySmall,
                ),
              ),
            if (activity.powerZones.any((z) => z.seconds > 0)) ...[
              const SizedBox(height: 8),
              Text(
                l10n.get('training.powerZones'),
                style: theme.textTheme.bodySmall,
              ),
              ZoneBar(zones: activity.powerZones, total: activity.powerSeconds),
            ],
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  '${l10n.get('training.derivedFrom')}: ${activity.analysisVersion}',
                  style: theme.textTheme.bodySmall,
                ),
                if (onReanalyze != null)
                  TextButton(
                    key: Key('training.reanalyze.${activity.id}'),
                    onPressed: onReanalyze,
                    child: Text(l10n.get('training.reanalyze')),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  final String label;
  final String value;
  final String keyName;
  const _Row({required this.label, required this.value, required this.keyName});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: theme.textTheme.bodyMedium),
          Text(value, key: Key(keyName), style: theme.textTheme.bodyMedium),
        ],
      ),
    );
  }
}

/// Zone distribution as a proportional bar. A ride with no time in any zone
/// shows an empty bar rather than a full one.
class ZoneBar extends StatelessWidget {
  final List<ZoneSeconds> zones;
  final double total;
  const ZoneBar({super.key, required this.zones, required this.total});

  @override
  Widget build(BuildContext context) {
    if (total <= 0) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: SizedBox(
          height: 10,
          child: Row(
            children: [
              for (final zone in zones)
                if (zone.seconds > 0)
                  Expanded(
                    flex: (zoneShare(zone.seconds, total) * 1000).round().clamp(
                      1,
                      1000,
                    ),
                    child: Container(
                      key: Key('training.zone.${zone.zone}'),
                      color: zoneColor(context, zone.zone),
                    ),
                  ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Zone 1 is easiest. Seven power zones vs five HR zones, so the HR scale is
/// stretched to keep the colours comparable.
Color zoneColor(BuildContext context, int zone) {
  final seed = Theme.of(context).colorScheme.primary;
  final t = ((zone - 1) / 6).clamp(0.0, 1.0);
  return Color.lerp(seed.withValues(alpha: 0.35), seed, t)!;
}

class _EmptyHint extends StatelessWidget {
  final String text;
  const _EmptyHint({required this.text});

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 24),
    child: Text(
      text,
      key: const Key('training.emptyHint'),
      textAlign: TextAlign.center,
      style: Theme.of(context).textTheme.bodySmall,
    ),
  );
}

class _ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorView({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) => Center(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(message, key: const Key('training.error')),
        TextButton(onPressed: onRetry, child: const Text('Retry')),
      ],
    ),
  );
}
