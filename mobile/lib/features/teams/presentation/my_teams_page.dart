import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// My Teams: every team the viewer belongs to, with its role and — for
/// managers — the size of the pending queue.
///
/// The role and counters come from the server on every read, so a badge here
/// is never a local guess about authority (ADR-13 §4).
class MyTeamsPage extends ConsumerWidget {
  const MyTeamsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final teams = ref.watch(myTeamsProvider(false));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.myTeams'))),
      body: teams.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyTeamError(t, e),
          onRetry: () => ref.invalidate(myTeamsProvider(false)),
        ),
        data: (page) {
          if (page.items.isEmpty) {
            return SocialMessage(
              icon: Icons.groups_rounded,
              message: t.get('team.noTeams'),
              actionLabel: t.get('team.createTeam'),
              onAction: () => context.push('/teams/new'),
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(myTeamsProvider(false)),
            child: ListView.separated(
              itemCount: page.items.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
              padding: const EdgeInsets.all(AppSpacing.md),
              itemBuilder: (context, i) => TeamTile(team: page.items[i]),
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        key: const Key('team.create.fab'),
        tooltip: t.get('team.createTeam'),
        onPressed: () => context.push('/teams/new'),
        child: const Icon(Icons.add),
      ),
    );
  }
}

/// A team row shared by the list and the search results.
class TeamTile extends StatelessWidget {
  final Team team;
  const TeamTile({super.key, required this.team});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: ListTile(
        key: Key('team.tile.${team.id}'),
        leading: TeamAvatar(
          avatarUrl: team.avatarUrl,
          name: team.name,
          radius: 24,
        ),
        title: Row(
          children: [
            Flexible(child: Text(team.name)),
            if (team.isPrivate) ...[
              const SizedBox(width: AppSpacing.xs),
              const Icon(
                Icons.lock_outline_rounded,
                size: 14,
                color: AppColors.textMuted,
              ),
            ],
            if (team.isArchived) ...[
              const SizedBox(width: AppSpacing.xs),
              RelationshipBadge(stateLabel: t.get('team.archived'), warn: true),
            ],
          ],
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TeamHandle(handle: team.handle),
            const SizedBox(height: AppSpacing.xs),
            Row(
              children: [
                TeamMemberCount(count: team.memberCount),
                const SizedBox(width: AppSpacing.md),
                TeamRoleBadge(role: team.myRole),
              ],
            ),
            if (team.hasPendingWork)
              TeamPendingBadge(
                requests: team.pendingRequestsCount,
                invitations: team.pendingInvitationsCount,
              ),
          ],
        ),
        isThreeLine: true,
        trailing: const Icon(Icons.chevron_right_rounded),
        onTap: () => context.push('/teams/${team.id}'),
      ),
    );
  }
}
