import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/competition_models.dart';
import 'competition_providers.dart';

/// One challenge and its leaderboard.
///
/// Every button is derived from the server's own flags — `canJoin`, `canLeave`,
/// `is_creator`, `state` — never from a locally derived guess. After an action
/// the detail and the board are re-read, so the buttons on screen always match
/// what the server now allows.
class ChallengeDetailPage extends ConsumerWidget {
  final String challengeId;
  const ChallengeDetailPage({super.key, required this.challengeId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final challenge = ref.watch(challengeDetailProvider(challengeId));
    final actions = ref.watch(challengeActionsProvider);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('competition.challengeDetail'))),
      body: challenge.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _Message(
          text: '$e',
          action: FilledButton.tonal(
            onPressed: () =>
                ref.invalidate(challengeDetailProvider(challengeId)),
            child: Text(t.get('competition.retry')),
          ),
        ),
        data: (c) => RefreshIndicator(
          onRefresh: () async {
            ref.invalidate(challengeDetailProvider(challengeId));
            ref.invalidate(challengeLeaderboardProvider(challengeId));
            await ref.read(challengeDetailProvider(challengeId).future);
          },
          child: ListView(
            padding: const EdgeInsets.all(AppSpacing.md),
            children: [
              _Header(challenge: c),
              const SizedBox(height: AppSpacing.md),
              _ProgressCard(
                challenge: c,
                busy:
                    actions.contains(ChallengeActions.key('join', c.id)) ||
                    actions.contains(ChallengeActions.key('leave', c.id)) ||
                    actions.contains(ChallengeActions.key('publish', c.id)) ||
                    actions.contains(ChallengeActions.key('cancel', c.id)),
                onJoin: () => _act(
                  context,
                  ref,
                  c,
                  () => ref.read(challengeActionsProvider.notifier).join(c.id),
                ),
                onLeave: () => _act(
                  context,
                  ref,
                  c,
                  () => ref.read(challengeActionsProvider.notifier).leave(c.id),
                ),
                onPublish: () => _act(
                  context,
                  ref,
                  c,
                  () =>
                      ref.read(challengeActionsProvider.notifier).publish(c.id),
                ),
                onCancel: () => _act(
                  context,
                  ref,
                  c,
                  () =>
                      ref.read(challengeActionsProvider.notifier).cancel(c.id),
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              Text(
                t.get('competition.leaderboard'),
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: AppSpacing.sm),
              _Leaderboard(challengeId: challengeId),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _act(
    BuildContext context,
    WidgetRef ref,
    Challenge c,
    Future<void> Function() run,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    final t = context.l10n;
    try {
      await run();
      // The provider invalidation re-reads the challenge and the board; the
      // server's answer IS the new screen, so success needs no toast.
    } catch (e) {
      if (context.mounted) {
        messenger.showSnackBar(
          SnackBar(
            content: Text(
              '$e'.isEmpty ? t.get('competition.errorAction') : '$e',
            ),
          ),
        );
      }
    }
  }
}

class _Header extends StatelessWidget {
  final Challenge challenge;
  const _Header({required this.challenge});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          challenge.title,
          style: Theme.of(
            context,
          ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
        ),
        if (challenge.description != null && challenge.description!.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: AppSpacing.xs),
            child: Text(challenge.description!),
          ),
        const SizedBox(height: AppSpacing.md),
        Wrap(
          spacing: AppSpacing.xs,
          runSpacing: AppSpacing.xs,
          children: [
            _Chip(text: t.get(challenge.state.labelKey)),
            _Chip(text: t.get(challenge.scope.labelKey)),
            _Chip(text: t.get(challenge.visibility.labelKey)),
            _Chip(
              text: t.getWith('competition.points', {
                'points': '${challenge.points}',
              }),
            ),
            if (challenge.participantCount != null)
              _Chip(
                text: t.getWith('competition.participants', {
                  'count': '${challenge.participantCount}',
                }),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          t.getWith('competition.window', {
            'start': _ymd(challenge.startAt),
            'end': _ymd(challenge.endAt),
          }),
          style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
        ),
      ],
    );
  }
}

class _ProgressCard extends StatelessWidget {
  final Challenge challenge;
  final bool busy;
  final VoidCallback onJoin;
  final VoidCallback onLeave;
  final VoidCallback onPublish;
  final VoidCallback onCancel;

  const _ProgressCard({
    required this.challenge,
    required this.busy,
    required this.onJoin,
    required this.onLeave,
    required this.onPublish,
    required this.onCancel,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final p = challenge.viewerProgress;
    final buttons = <Widget>[];
    if (challenge.canJoin) {
      buttons.add(
        FilledButton.icon(
          onPressed: busy ? null : onJoin,
          icon: const Icon(Icons.group_add_outlined),
          label: Text(t.get('competition.join')),
        ),
      );
    }
    if (challenge.canLeave) {
      buttons.add(
        OutlinedButton.icon(
          onPressed: busy ? null : onLeave,
          icon: const Icon(Icons.exit_to_app),
          label: Text(t.get('competition.leave')),
        ),
      );
    }
    if (challenge.isCreator && challenge.status == ChallengeStatus.draft) {
      buttons.add(
        FilledButton.icon(
          onPressed: busy ? null : onPublish,
          icon: const Icon(Icons.publish),
          label: Text(t.get('competition.publish')),
        ),
      );
    }
    if (challenge.isCreator &&
        (challenge.status == ChallengeStatus.scheduled ||
            challenge.status == ChallengeStatus.active)) {
      buttons.add(
        OutlinedButton.icon(
          onPressed: busy ? null : onCancel,
          icon: const Icon(Icons.close),
          label: Text(t.get('competition.cancel')),
        ),
      );
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  t.get('competition.myProgress'),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                if (challenge.viewerState != null)
                  Text(
                    t.get(challenge.viewerState!.labelKey),
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textMuted,
                    ),
                  ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            LinearProgressIndicator(
              value: (p.percent / 100).clamp(0.0, 1.0),
              minHeight: 8,
              borderRadius: BorderRadius.circular(4),
            ),
            const SizedBox(height: AppSpacing.sm),
            Text(
              t.getWith('competition.progress', {
                'value': _fmt(p.value),
                'target': _fmt(p.target),
                'percent': p.percent.toStringAsFixed(0),
              }),
            ),
            Text(
              t.getWith('competition.progressRides', {'count': '${p.rides}'}),
              style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
            ),
            if (p.completed) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                t.get('competition.completed'),
                style: const TextStyle(
                  color: AppColors.primary,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
            if (buttons.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: buttons,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _Leaderboard extends ConsumerWidget {
  final String challengeId;
  const _Leaderboard({required this.challengeId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final board = ref.watch(challengeLeaderboardProvider(challengeId));
    return board.when(
      loading: () => const Center(
        child: Padding(
          padding: EdgeInsets.all(AppSpacing.lg),
          child: CircularProgressIndicator(),
        ),
      ),
      error: (e, _) => _Message(
        text: '$e',
        action: FilledButton.tonal(
          onPressed: () =>
              ref.invalidate(challengeLeaderboardProvider(challengeId)),
          child: Text(t.get('competition.retry')),
        ),
      ),
      data: (page) {
        if (page.items.isEmpty) {
          return _Message(text: t.get('competition.leaderboardEmpty'));
        }
        return Column(
          children: [
            for (final entry in page.items)
              ListTile(
                dense: true,
                leading: CircleAvatar(radius: 14, child: Text('${entry.rank}')),
                title: Text(
                  entry.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: Text(
                  _fmt(entry.value),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _Chip extends StatelessWidget {
  final String text;
  const _Chip({required this.text});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: AppColors.primary.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Text(text, style: const TextStyle(fontSize: 12)),
    );
  }
}

class _Message extends StatelessWidget {
  final String text;
  final Widget? action;
  const _Message({required this.text, this.action});

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

String _fmt(double v) {
  final rounded = v.toStringAsFixed(1);
  return rounded.endsWith('.0') ? v.toStringAsFixed(0) : rounded;
}

String _ymd(DateTime d) {
  final m = d.month.toString().padLeft(2, '0');
  final day = d.day.toString().padLeft(2, '0');
  return '${d.year}-$m-$day';
}
