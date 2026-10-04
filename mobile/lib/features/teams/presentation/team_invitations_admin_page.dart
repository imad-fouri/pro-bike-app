import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// Manager's view of one team's invitations, with the ability to revoke an
/// offer the rider has not yet answered.
///
/// Owner and admin only. Revoking sets the invitation to `revoked`; it never
/// touches a membership, because none exists yet (ADR-13 §3).
class TeamInvitationsAdminPage extends ConsumerWidget {
  final String teamId;
  const TeamInvitationsAdminPage({super.key, required this.teamId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final page = ref.watch(teamInvitationsProvider(teamId));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.invitations'))),
      body: page.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyTeamError(t, e),
          onRetry: () => ref.invalidate(teamInvitationsProvider(teamId)),
        ),
        data: (result) {
          if (result.items.isEmpty) {
            return SocialMessage(
              icon: Icons.mail_outline_rounded,
              message: t.get('team.noInvitations'),
            );
          }
          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(teamInvitationsProvider(teamId)),
            child: ListView.separated(
              itemCount: result.items.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
              padding: const EdgeInsets.all(AppSpacing.md),
              itemBuilder: (context, i) => _AdminInvitationTile(
                teamId: teamId,
                invitation: result.items[i],
              ),
            ),
          );
        },
      ),
    );
  }
}

class _AdminInvitationTile extends ConsumerWidget {
  final String teamId;
  final TeamInvitation invitation;
  const _AdminInvitationTile({required this.teamId, required this.invitation});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(teamActionsProvider);
    final id = invitation.id;

    return Card(
      child: ListTile(
        key: Key('team.adminInvitation.$id'),
        title: Text('@${invitation.invitedByUsername ?? ''}'),
        subtitle: Row(
          children: [
            RelationshipBadge(stateLabel: t.get(invitation.status.labelKey)),
          ],
        ),
        trailing: invitation.isPending
            ? SocialActionButton(
                buttonKey: Key('team.invitation.revoke.$id'),
                label: t.get('team.revokeInvitation'),
                busyLabel: t.get('social.loading'),
                busy: busy.contains(
                  TeamActions.requestKey('revoke', teamId, id),
                ),
                destructive: true,
                onPressed: () => _run(
                  context,
                  () => ref
                      .read(teamActionsProvider.notifier)
                      .revokeInvitation(teamId, id),
                  t.get('team.invitationRevoked'),
                ),
              )
            : null,
      ),
    );
  }
}

Future<void> _run(
  BuildContext context,
  Future<void> Function() body,
  String successMessage,
) async {
  final messenger = ScaffoldMessenger.maybeOf(context);
  final t = context.l10n;
  try {
    await body();
    if (messenger != null && messenger.mounted) {
      messenger.showSnackBar(SnackBar(content: Text(successMessage)));
    }
  } on Object catch (e) {
    if (messenger != null && messenger.mounted) {
      messenger.showSnackBar(SnackBar(content: Text(friendlyTeamError(t, e))));
    }
  }
}
