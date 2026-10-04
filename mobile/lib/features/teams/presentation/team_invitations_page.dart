import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// The viewer's invitation inbox: offers addressed to them, and the asks they
/// have sent.
///
/// Accepting here creates a membership server-side; nothing is applied
/// optimistically, so the team list is not updated until the server confirms.
class TeamInvitationsPage extends ConsumerWidget {
  const TeamInvitationsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.get('team.invitations')),
          bottom: TabBar(
            labelColor: AppColors.primary,
            unselectedLabelColor: AppColors.textMuted,
            indicatorColor: AppColors.primary,
            tabs: [
              Tab(
                key: const Key('team.tab.invited'),
                text: t.get('team.invitations'),
              ),
              Tab(
                key: const Key('team.tab.myRequests'),
                text: t.get('team.myRequests'),
              ),
            ],
          ),
        ),
        body: const TabBarView(children: [_InvitedList(), _MyRequestsList()]),
      ),
    );
  }
}

class _InvitedList extends ConsumerWidget {
  const _InvitedList();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final page = ref.watch(myInvitationsProvider);
    return page.when(
      loading: () => Center(
        child: Text(
          t.get('social.loading'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      ),
      error: (e, _) => SocialErrorView(
        error: e,
        message: friendlyTeamError(t, e),
        onRetry: () => ref.invalidate(myInvitationsProvider),
      ),
      data: (result) {
        if (result.items.isEmpty) {
          return SocialMessage(
            icon: Icons.mail_outline_rounded,
            message: t.get('team.noInvitations'),
          );
        }
        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(myInvitationsProvider),
          child: ListView.separated(
            itemCount: result.items.length,
            separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
            padding: const EdgeInsets.all(AppSpacing.md),
            itemBuilder: (context, i) =>
                _InvitationTile(invitation: result.items[i]),
          ),
        );
      },
    );
  }
}

class _InvitationTile extends ConsumerWidget {
  final TeamInvitation invitation;
  const _InvitationTile({required this.invitation});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(teamActionsProvider);
    final id = invitation.id;
    final pending = invitation.isPending;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        invitation.teamName ?? t.get('social.userProfile'),
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      if (invitation.teamHandle != null)
                        TeamHandle(handle: invitation.teamHandle),
                      if (invitation.invitedByUsername != null)
                        Text(
                          '${invitation.invitedByUsername} '
                          '${t.get('team.invitedYou')}',
                          style: const TextStyle(
                            color: AppColors.textMuted,
                            fontSize: 12,
                          ),
                        ),
                    ],
                  ),
                ),
                if (!pending)
                  RelationshipBadge(
                    stateLabel: t.get(invitation.status.labelKey),
                  ),
              ],
            ),
            if (invitation.message != null &&
                invitation.message!.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(invitation.message!),
            ],
            if (pending) ...[
              const SizedBox(height: AppSpacing.md),
              Row(
                children: [
                  Expanded(
                    child: SocialActionButton(
                      buttonKey: Key('team.invitation.accept.$id'),
                      label: t.get('team.acceptInvitation'),
                      busyLabel: t.get('social.loading'),
                      busy: busy.contains(
                        TeamActions.requestKey('acceptinv', '', id),
                      ),
                      primary: true,
                      onPressed: () => _run(
                        context,
                        () => ref
                            .read(teamActionsProvider.notifier)
                            .acceptInvitation(id),
                        t.get('team.invitationAccepted'),
                      ),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: SocialActionButton(
                      buttonKey: Key('team.invitation.decline.$id'),
                      label: t.get('team.declineInvitation'),
                      busyLabel: t.get('social.loading'),
                      busy: busy.contains(
                        TeamActions.requestKey('declineinv', '', id),
                      ),
                      onPressed: () => _run(
                        context,
                        () => ref
                            .read(teamActionsProvider.notifier)
                            .declineInvitation(id),
                        t.get('team.invitationDeclined'),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _MyRequestsList extends ConsumerWidget {
  const _MyRequestsList();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final page = ref.watch(myJoinRequestsProvider);
    return page.when(
      loading: () => Center(
        child: Text(
          t.get('social.loading'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      ),
      error: (e, _) => SocialErrorView(
        error: e,
        message: friendlyTeamError(t, e),
        onRetry: () => ref.invalidate(myJoinRequestsProvider),
      ),
      data: (result) {
        if (result.items.isEmpty) {
          return SocialMessage(
            icon: Icons.outbox_rounded,
            message: t.get('team.noRequests'),
          );
        }
        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(myJoinRequestsProvider),
          child: ListView.separated(
            itemCount: result.items.length,
            separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
            padding: const EdgeInsets.all(AppSpacing.md),
            itemBuilder: (context, i) {
              final r = result.items[i];
              return Card(
                child: ListTile(
                  key: Key('team.myRequest.${r.id}'),
                  title: Text(r.displayName ?? t.get('team.myTeams')),
                  subtitle: const TeamStateBadge(
                    state: TeamState.joinRequestPending,
                  ),
                  trailing: SocialActionButton(
                    buttonKey: Key('team.myRequest.cancel.${r.id}'),
                    label: t.get('team.cancelRequest'),
                    busyLabel: t.get('social.loading'),
                    busy: busy(ref, r.id, r.teamId),
                    onPressed: () => _run(
                      context,
                      () => ref
                          .read(teamActionsProvider.notifier)
                          .cancelJoinRequest(r.teamId, r.id),
                      t.get('team.leftTeam'),
                    ),
                  ),
                  onTap: () => context.push('/teams/${r.teamId}'),
                ),
              );
            },
          ),
        );
      },
    );
  }

  static bool busy(WidgetRef ref, String requestId, String teamId) {
    final keys = ref.watch(teamActionsProvider);
    return keys.contains(TeamActions.requestKey('cancel', teamId, requestId));
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
