import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_providers.dart';
import '../../social/presentation/social_widgets.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// Invite a rider to a team.
///
/// The candidate list is the viewer's FRIENDS, read through the Phase 8.1
/// social API. Two consequences worth stating:
///
/// * Inviting is therefore an action on a real relationship, not an arbitrary
///   rider lookup — the server still re-resolves the target and re-checks the
///   block, but the UI never offers a stranger.
/// * Reading the friend list here does not *link* team membership to
///   friendship. Inviting someone does not make them a friend, and the
///   invitation can be declined with no effect on either graph (ADR-13 §5).
class TeamInvitePage extends ConsumerStatefulWidget {
  final String teamId;
  const TeamInvitePage({super.key, required this.teamId});

  @override
  ConsumerState<TeamInvitePage> createState() => _TeamInvitePageState();
}

class _TeamInvitePageState extends ConsumerState<TeamInvitePage> {
  final _message = TextEditingController();
  String? _sentTo;

  @override
  void dispose() {
    _message.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final friends = ref.watch(friendListProvider);
    final busy = ref.watch(teamActionsProvider);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.inviteMember'))),
      body: friends.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyError(t, e),
          onRetry: () => ref.read(friendListProvider.notifier).refresh(),
        ),
        data: (paged) {
          if (paged.items.isEmpty) {
            return SocialMessage(
              icon: Icons.people_outline_rounded,
              message: t.get('social.noFriends'),
            );
          }
          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: TextField(
                  key: const Key('team.invite.message'),
                  controller: _message,
                  maxLength: 280,
                  decoration: InputDecoration(
                    labelText: t.get('team.optionalMessage'),
                  ),
                ),
              ),
              Expanded(
                child: ListView.separated(
                  itemCount: paged.items.length,
                  separatorBuilder: (_, _) =>
                      const SizedBox(height: AppSpacing.xs),
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.md,
                  ),
                  itemBuilder: (context, i) {
                    final friend = paged.items[i];
                    final id = friend.userId;
                    final isBusy = busy.contains(
                      TeamActions.inviteKey(widget.teamId, id),
                    );
                    return Card(
                      child: ListTile(
                        key: Key('team.invite.$id'),
                        leading: SocialAvatar(
                          avatarUrl: friend.avatarUrl,
                          name: friend.bestName,
                        ),
                        title: Text(
                          friend.bestName ?? t.get('social.userProfile'),
                        ),
                        subtitle: SocialHandle(username: friend.username),
                        trailing: _sentTo == id
                            ? const Icon(
                                Icons.check_circle_rounded,
                                color: AppColors.primary,
                              )
                            : SocialActionButton(
                                buttonKey: Key('team.invite.send.$id'),
                                label: t.get('team.invite'),
                                busyLabel: t.get('social.loading'),
                                busy: isBusy,
                                primary: true,
                                onPressed: isBusy ? null : () => _send(id),
                              ),
                      ),
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _send(String userId) async {
    final t = context.l10n;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final text = _message.text.trim();
    try {
      await ref
          .read(teamActionsProvider.notifier)
          .invite(widget.teamId, userId, message: text);
      if (!mounted) return;
      setState(() => _sentTo = userId);
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(t.get('team.invitationSent'))),
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
