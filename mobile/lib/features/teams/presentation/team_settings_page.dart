import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// Team settings: identity, privacy, and the management entry points.
///
/// Owner sees everything. Admin sees the limited settings the server permits
/// (ADR-13 §4). Member sees a read-only summary — and is told plainly that
/// leaving is their own decision, not something an admin does to them.
class TeamSettingsPage extends ConsumerWidget {
  final String teamId;
  const TeamSettingsPage({super.key, required this.teamId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final detail = ref.watch(teamDetailProvider(teamId));

    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.teamSettings'))),
      body: detail.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyTeamError(t, e),
          onRetry: () => ref.invalidate(teamDetailProvider(teamId)),
        ),
        data: (team) {
          final isManager = team.myRole?.canManage ?? false;
          final isOwner = team.myRole?.isOwner ?? false;
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(teamDetailProvider(teamId)),
            child: ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            TeamAvatar(
                              avatarUrl: team.avatarUrl,
                              name: team.name,
                            ),
                            const SizedBox(width: AppSpacing.md),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    team.name,
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                                  TeamHandle(handle: team.handle),
                                ],
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: AppSpacing.md),
                        Row(
                          children: [
                            Text(
                              t.get('team.visibility'),
                              style: const TextStyle(
                                color: AppColors.textMuted,
                              ),
                            ),
                            const Spacer(),
                            Text(
                              t.get(team.visibility.labelKey),
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: AppSpacing.xs),
                        Row(
                          children: [
                            Text(
                              t.get('team.teamMembers'),
                              style: const TextStyle(
                                color: AppColors.textMuted,
                              ),
                            ),
                            const Spacer(),
                            Text(
                              '${team.memberCount}',
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                        if (team.isArchived) ...[
                          const SizedBox(height: AppSpacing.sm),
                          RelationshipBadge(
                            stateLabel: t.get('team.archived'),
                            warn: true,
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
                if (isManager) ...[
                  SocialActionButton(
                    buttonKey: const Key('team.settings.edit'),
                    label: t.get('team.editTeam'),
                    busyLabel: t.get('social.loading'),
                    busy: false,
                    primary: true,
                    onPressed: () => context.push('/teams/$teamId/edit'),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  ListTile(
                    key: const Key('team.settings.members'),
                    leading: const Icon(Icons.people_alt_rounded),
                    title: Text(t.get('team.teamMembers')),
                    trailing: const Icon(Icons.chevron_right_rounded),
                    onTap: () => context.push('/teams/$teamId/members'),
                  ),
                  ListTile(
                    key: const Key('team.settings.requests'),
                    leading: const Icon(Icons.how_to_reg_rounded),
                    title: Text(t.get('team.joinRequests')),
                    trailing: team.pendingRequestsCount > 0
                        ? RelationshipBadge(
                            stateLabel: '${team.pendingRequestsCount}',
                            warn: true,
                          )
                        : null,
                    onTap: () => context.push('/teams/$teamId/join-requests'),
                  ),
                  ListTile(
                    key: const Key('team.settings.invitations'),
                    leading: const Icon(Icons.mail_outline_rounded),
                    title: Text(t.get('team.invitations')),
                    trailing: const Icon(Icons.chevron_right_rounded),
                    onTap: () => context.push('/teams/$teamId/invitations'),
                  ),
                ] else
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.info_outline_rounded),
                      title: Text(t.get('team.state.${team.state.wire}')),
                      subtitle: Text(t.get('team.confirmLeave')),
                    ),
                  ),
                const SizedBox(height: AppSpacing.md),
                const TeamLocationNotice(),
                if (isOwner && !team.isArchived) ...[
                  const SizedBox(height: AppSpacing.md),
                  SocialActionButton(
                    buttonKey: const Key('team.settings.archive'),
                    label: t.get('team.archiveTeam'),
                    busyLabel: t.get('social.loading'),
                    busy: ref
                        .watch(teamActionsProvider)
                        .contains(TeamActions.teamKey('archive', teamId)),
                    destructive: true,
                    onPressed: () => _confirmArchive(context, ref, teamId),
                  ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  static Future<void> _confirmArchive(
    BuildContext context,
    WidgetRef ref,
    String teamId,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(t.get('team.confirmArchiveHint')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(t.get('team.reject')),
          ),
          FilledButton(
            key: const Key('team.confirm.archive'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(t.get('team.archiveTeam')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      await ref.read(teamActionsProvider.notifier).archiveTeam(teamId);
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(t.get('team.teamArchived'))),
        );
      }
    } on Object catch (e) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(friendlyTeamError(t, e))),
        );
      }
    }
  }
}
