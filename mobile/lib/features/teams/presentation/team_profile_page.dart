import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../chat/presentation/chat_providers.dart';
import '../../chat/presentation/chat_widgets.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// Another team (or one of yours), with exactly the actions the server's role
/// and state permit.
///
/// The action set is a pure function of `Team.state` and `Team.myRole`, both
/// server-provided. A stale screen therefore cannot offer a mutation the server
/// would refuse with 404, and a fresh one cannot hide one it would accept
/// (ADR-13 §4).
///
/// No location, ever: there is no field on a team to render one (ADR-13 §7).
class TeamProfilePage extends ConsumerWidget {
  final String teamId;
  const TeamProfilePage({super.key, required this.teamId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final detail = ref.watch(teamDetailProvider(teamId));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.myTeams'))),
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
        data: (team) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(teamDetailProvider(teamId)),
          child: ListView(
            padding: const EdgeInsets.all(AppSpacing.md),
            children: [
              _Header(team: team),
              const SizedBox(height: AppSpacing.md),
              TeamStateBadge(state: team.state),
              if (team.isArchived) ...[
                const SizedBox(height: AppSpacing.xs),
                RelationshipBadge(
                  stateLabel: t.get('team.archived'),
                  warn: true,
                ),
              ],
              const SizedBox(height: AppSpacing.sm),
              if (team.isPrivate) _PrivateNote(team: team),
              const SizedBox(height: AppSpacing.md),
              const TeamLocationNotice(),
              const SizedBox(height: AppSpacing.md),
              ...actionsFor(context, ref, team),
            ],
          ),
        ),
      ),
    );
  }

  /// The single mapping from server state + role to available actions.
  /// Extracted so the rule is testable on its own and has exactly one home.
  static List<Widget> actionsFor(
    BuildContext context,
    WidgetRef ref,
    Team team,
  ) {
    final t = context.l10n;
    final busy = ref.watch(teamActionsProvider);
    final id = team.id;

    // Every member gets the team channel. It is created on first open, so this
    // tile is also the entry point for a team that has never been used for chat
    // yet (ADR-14 §3).
    final chatTile = ListTile(
      key: const Key('team.action.chat'),
      leading: const Icon(Icons.forum_outlined),
      title: Text(t.get('chat.teamChannel')),
      onTap: () => _openTeamChat(context, ref, id, team.name),
    );

    // A manager always gets the management surfaces first.
    final managerWidgets = <Widget>[
      if (team.myRole?.canManage ?? false) ...[
        ListTile(
          key: const Key('team.action.members'),
          leading: const Icon(Icons.people_alt_rounded),
          title: Text(t.get('team.teamMembers')),
          trailing: Text('${team.memberCount}'),
          onTap: () => context.push('/teams/$id/members'),
        ),
        if (team.pendingRequestsCount > 0)
          ListTile(
            key: const Key('team.action.joinRequests'),
            leading: const Icon(Icons.how_to_reg_rounded),
            title: Text(t.get('team.joinRequests')),
            trailing: RelationshipBadge(
              stateLabel: '${team.pendingRequestsCount}',
              warn: true,
            ),
            onTap: () => context.push('/teams/$id/join-requests'),
          ),
        ListTile(
          key: const Key('team.action.invitations'),
          leading: const Icon(Icons.mail_outline_rounded),
          title: Text(t.get('team.invitations')),
          onTap: () => context.push('/teams/$id/invitations'),
        ),
        ListTile(
          key: const Key('team.action.settings'),
          leading: const Icon(Icons.settings_outlined),
          title: Text(t.get('team.teamSettings')),
          onTap: () => context.push('/teams/$id/settings'),
        ),
        if (team.isOwner)
          ListTile(
            key: const Key('team.action.archive'),
            leading: Icon(
              Icons.archive_outlined,
              color: Theme.of(context).colorScheme.error,
            ),
            title: Text(
              t.get('team.archiveTeam'),
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
            onTap: () => _confirmArchive(context, ref, id),
          ),
      ],
    ];

    switch (team.state) {
      case TeamState.owner:
      case TeamState.admin:
      case TeamState.member:
        // Members can always leave; the owner cannot (server 409, and the
        // button is withheld so the rider is not invited to fail).
        return [
          ...managerWidgets,
          chatTile,
          if (team.myRole != TeamRole.owner)
            SocialActionButton(
              buttonKey: const Key('team.action.leave'),
              label: t.get('team.leave'),
              busyLabel: t.get('social.loading'),
              busy: busy.contains(TeamActions.teamKey('leave', id)),
              destructive: true,
              onPressed: () => _confirmLeave(context, ref, id),
            ),
        ];
      case TeamState.joinRequestPending:
        return [
          ...managerWidgets,
          SocialActionButton(
            buttonKey: const Key('team.action.cancelRequest'),
            label: t.get('team.cancelRequest'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(TeamActions.teamKey('join', id)),
            primary: true,
            onPressed: () => _run(
              context,
              () => ref
                  .read(teamActionsProvider.notifier)
                  .cancelMyJoinRequest(id),
              t.get('team.leftTeam'),
            ),
          ),
        ];
      case TeamState.invited:
      case TeamState.notAffiliated:
        // One button either way: a public team joins immediately, a private one
        // opens a request. The server decides, and the response `status` is
        // what the confirmation says.
        return [
          ...managerWidgets,
          SocialActionButton(
            buttonKey: const Key('team.action.join'),
            label: t.get('team.join'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(TeamActions.teamKey('join', id)),
            primary: true,
            onPressed: () => _join(context, ref, id),
          ),
        ];
    }
  }

  /// Resolve the team's channel, then navigate to it.
  ///
  /// The channel is created on first open, so a 404 here means the rider is not
  /// a member — the same answer as a team that does not exist. The tile is only
  /// offered to members, so reaching the error is not an expected state; it is
  /// reported rather than swallowed because it would otherwise look like the tap
  /// did nothing.
  static Future<void> _openTeamChat(
    BuildContext context,
    WidgetRef ref,
    String teamId,
    String teamName,
  ) async {
    final t = context.l10n;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final router = GoRouter.of(context);
    try {
      final conversation = await ref
          .read(chatActionsProvider.notifier)
          .openTeamConversation(teamId);
      router.push(
        '/chat/${conversation.id}?name=${Uri.encodeComponent(teamName)}',
      );
    } on Object catch (error) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(friendlyChatError(t, error))),
        );
      }
    }
  }

  static Future<void> _join(
    BuildContext context,
    WidgetRef ref,
    String teamId,
  ) async {
    final t = context.l10n;
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      final status = await ref.read(teamActionsProvider.notifier).join(teamId);
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(
            content: Text(
              status == 'requested'
                  ? t.get('team.requestSent')
                  : t.get('team.joinedTeam'),
            ),
          ),
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

  static Future<void> _confirmLeave(
    BuildContext context,
    WidgetRef ref,
    String teamId,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(t.get('team.confirmLeave')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(t.get('team.reject')),
          ),
          FilledButton(
            key: const Key('team.confirm.leave'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(t.get('team.leave')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    await _run(
      context,
      () => ref.read(teamActionsProvider.notifier).leave(teamId),
      t.get('team.leftTeam'),
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
    await _run(
      context,
      () => ref.read(teamActionsProvider.notifier).archiveTeam(teamId),
      t.get('team.teamArchived'),
    );
  }

  /// Await the server, then announce its result — or its error.
  static Future<void> _run(
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
        messenger.showSnackBar(
          SnackBar(content: Text(friendlyTeamError(t, e))),
        );
      }
    }
  }
}

class _Header extends StatelessWidget {
  final Team team;
  const _Header({required this.team});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
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
                  radius: 32,
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        team.name,
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 2),
                      TeamHandle(handle: team.handle),
                    ],
                  ),
                ),
              ],
            ),
            if (team.description != null && team.description!.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              Text(team.description!),
            ],
            const SizedBox(height: AppSpacing.md),
            Wrap(
              spacing: AppSpacing.md,
              runSpacing: AppSpacing.sm,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                TeamMemberCount(count: team.memberCount),
                TeamRoleBadge(role: team.myRole),
                if (team.category != null && team.category!.isNotEmpty)
                  Text(teamCategoryLabel(t, team.category!)),
              ],
            ),
            if (team.hasPendingWork) ...[
              const SizedBox(height: AppSpacing.sm),
              TeamPendingBadge(
                requests: team.pendingRequestsCount,
                invitations: team.pendingInvitationsCount,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PrivateNote extends StatelessWidget {
  final Team team;
  const _PrivateNote({required this.team});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Row(
      children: [
        const Icon(
          Icons.lock_outline_rounded,
          size: 16,
          color: AppColors.textMuted,
        ),
        const SizedBox(width: AppSpacing.xs),
        Expanded(
          child: Text(
            t.get('team.private'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
      ],
    );
  }
}
