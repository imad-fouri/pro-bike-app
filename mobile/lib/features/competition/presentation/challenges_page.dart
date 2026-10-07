import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/competition_models.dart';
import 'competition_providers.dart';

/// Discover and manage challenges. The list is the server's answer to a read,
/// not a local filter: "Discover" is `GET /challenges`, "Mine" is
/// `GET /challenges?mine=true` (drafts included). Nothing is combined or
/// re-sorted client-side.
class ChallengesPage extends ConsumerStatefulWidget {
  const ChallengesPage({super.key});

  @override
  ConsumerState<ChallengesPage> createState() => _ChallengesPageState();
}

class _ChallengesPageState extends ConsumerState<ChallengesPage> {
  bool _mine = false;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final page = ref.watch(_mine ? myChallengesProvider : challengesProvider);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('competition.challenges'))),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => context.go('/challenges/new'),
        icon: const Icon(Icons.add),
        label: Text(t.get('competition.create')),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: SegmentedButton<bool>(
              segments: [
                ButtonSegment(
                  value: false,
                  label: Text(t.get('competition.discover')),
                ),
                ButtonSegment(
                  value: true,
                  label: Text(t.get('competition.mine')),
                ),
              ],
              selected: {_mine},
              onSelectionChanged: (s) => setState(() => _mine = s.first),
            ),
          ),
          Expanded(
            child: page.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => _Message(
                text: '$e',
                action: FilledButton.tonal(
                  onPressed: () => ref.invalidate(
                    _mine ? myChallengesProvider : challengesProvider,
                  ),
                  child: Text(t.get('competition.retry')),
                ),
              ),
              data: (result) {
                if (result.items.isEmpty) {
                  return _Message(text: t.get('competition.none'));
                }
                return ListView.builder(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.md,
                    0,
                    AppSpacing.md,
                    88,
                  ),
                  itemCount: result.items.length,
                  itemBuilder: (context, i) {
                    final challenge = result.items[i];
                    return _ChallengeTile(
                      challenge: challenge,
                      mine: _mine,
                      onTap: () => context.go('/challenges/${challenge.id}'),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _ChallengeTile extends StatelessWidget {
  final Challenge challenge;
  final bool mine;
  final VoidCallback onTap;

  const _ChallengeTile({
    required this.challenge,
    required this.mine,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final progress = challenge.viewerProgress;
    final subtitleParts = [
      t.get(challenge.metric.labelKey),
      _formatTarget(challenge.target),
      t.get(challenge.scope.labelKey),
    ];
    return Card(
      child: ListTile(
        onTap: onTap,
        title: Text(
          challenge.isCreator ? '★ ${challenge.title}' : challenge.title,
        ),
        subtitle: Text(
          subtitleParts.join(' · '),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        isThreeLine: false,
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              t.getWith('competition.points', {
                'points': '${challenge.points}',
              }),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            if (challenge.participantCount != null)
              Text(
                t.getWith('competition.participants', {
                  'count': '${challenge.participantCount}',
                }),
                style: const TextStyle(
                  fontSize: 11,
                  color: AppColors.textMuted,
                ),
              ),
            if (progress.completed)
              Text(
                t.get('competition.completed'),
                style: const TextStyle(fontSize: 11, color: AppColors.primary),
              ),
          ],
        ),
      ),
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

String _formatTarget(double target) {
  final rounded = target.toStringAsFixed(1);
  return rounded.endsWith('.0') ? target.toStringAsFixed(0) : rounded;
}
