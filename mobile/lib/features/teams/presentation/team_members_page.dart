import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// A team's roster, with the management actions the viewer is entitled to.
///
/// Owner may change roles; owner and admin may remove. A plain member sees the
/// roster read-only. The buttons are rendered from the server's role, and the
/// server re-checks it — the two must never disagree, but only the server
/// decides (ADR-13 §4).
class TeamMembersPage extends ConsumerWidget {
  final String teamId;
  const TeamMembersPage({super.key, required this.teamId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final detail = ref.watch(teamDetailProvider(teamId));
    final members = ref.watch(teamMembersProvider(teamId));

    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('team.teamMembers')),
        actions: [
          if (detail.value?.isManager ?? false)
            IconButton(
              key: const Key('team.members.invite'),
              tooltip: t.get('team.invite'),
              onPressed: () => context.push('/teams/$teamId/invite'),
              icon: const Icon(Icons.person_add_alt_rounded),
            ),
        ],
      ),
      body: members.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyTeamError(t, e),
          onRetry: () => ref.invalidate(teamMembersProvider(teamId)),
        ),
        data: (page) {
          if (page.items.isEmpty) {
            return SocialMessage(
              icon: Icons.people_outline_rounded,
              message: t.get('team.noMembers'),
            );
          }
          final myRole = detail.value?.myRole;
          final canManage = myRole?.canManage ?? false;
          final isOwner = myRole?.isOwner ?? false;
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(teamMembersProvider(teamId)),
            child: ListView.separated(
              itemCount: page.items.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
              padding: const EdgeInsets.all(AppSpacing.md),
              itemBuilder: (context, i) {
                final m = page.items[i];
                return _MemberTile(
                  teamId: teamId,
                  member: m,
                  canManage: canManage,
                  isOwner: isOwner,
                );
              },
            ),
          );
        },
      ),
    );
  }
}

class _MemberTile extends ConsumerWidget {
  final String teamId;
  final TeamMember member;
  final bool canManage;
  final bool isOwner;

  const _MemberTile({
    required this.teamId,
    required this.member,
    required this.canManage,
    required this.isOwner,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(teamActionsProvider);
    final id = member.userId;
    // The owner row is immutable in Phase 8.2 (no ownership transfer), so it
    // offers no role or remove control at all.
    final isOwnerRow = member.role == TeamRole.owner;
    final showActions = canManage && !isOwnerRow;

    return Card(
      child: ListTile(
        key: Key('team.member.$id'),
        leading: SocialAvatar(
          avatarUrl: member.avatarUrl,
          name: member.bestName,
        ),
        title: Text(member.bestName ?? t.get('social.userProfile')),
        subtitle: TeamRoleBadge(role: member.role),
        trailing: showActions
            ? PopupMenuButton<String>(
                key: Key('team.member.menu.$id'),
                onSelected: (value) => _onSelect(context, ref, value),
                itemBuilder: (context) => [
                  if (isOwner && member.role == TeamRole.member)
                    PopupMenuItem(
                      value: 'promote',
                      child: Row(
                        children: [
                          const Icon(Icons.shield_outlined, size: 18),
                          const SizedBox(width: AppSpacing.sm),
                          Flexible(child: Text(t.get('team.makeAdmin'))),
                        ],
                      ),
                    ),
                  if (isOwner && member.role == TeamRole.admin)
                    PopupMenuItem(
                      value: 'demote',
                      child: Row(
                        children: [
                          const Icon(Icons.person_outline_rounded, size: 18),
                          const SizedBox(width: AppSpacing.sm),
                          Flexible(child: Text(t.get('team.makeMember'))),
                        ],
                      ),
                    ),
                  PopupMenuItem(
                    value: 'remove',
                    child: Row(
                      children: [
                        Icon(
                          Icons.person_remove_outlined,
                          size: 18,
                          color: Theme.of(context).colorScheme.error,
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Flexible(child: Text(t.get('team.remove'))),
                      ],
                    ),
                  ),
                ],
              )
            : (busy.contains(TeamActions.memberKey('remove', '', id))
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : null),
        onTap: () => context.push('/users/$id'),
      ),
    );
  }

  Future<void> _onSelect(
    BuildContext context,
    WidgetRef ref,
    String value,
  ) async {
    final t = context.l10n;
    if (value == 'remove') {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          content: Text(t.get('team.confirmRemoveMember')),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: Text(t.get('team.reject')),
            ),
            FilledButton(
              key: const Key('team.confirm.removeMember'),
              onPressed: () => Navigator.of(ctx).pop(true),
              child: Text(t.get('team.remove')),
            ),
          ],
        ),
      );
      if (ok != true || !context.mounted) return;
      await _run(
        context,
        () => ref
            .read(teamActionsProvider.notifier)
            .removeMember(teamId, member.userId),
        t.get('team.memberRemoved'),
      );
      return;
    }

    final role = value == 'promote' ? TeamRole.admin : TeamRole.member;
    await _run(
      context,
      () => ref
          .read(teamActionsProvider.notifier)
          .setRole(teamId, member.userId, role),
      t.get('team.roleChanged'),
    );
  }

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
