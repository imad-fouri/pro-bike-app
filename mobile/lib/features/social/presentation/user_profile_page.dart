import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../chat/presentation/chat_providers.dart';
import '../../chat/presentation/chat_widgets.dart';
import '../domain/social_profile.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Another rider's profile, with exactly the actions their relationship state
/// permits.
///
/// The button set is a pure function of the server's `relationship` value —
/// see [actionsFor]. Nothing is inferred from "did I press add friend a moment
/// ago", so a stale screen cannot offer an action the server would reject, and
/// a fresh screen cannot hide one it would accept.
///
/// No location, ever. There is no live position, route position, or last-known
/// location on this screen because the API has no such field to render
/// (ADR-12 §5).
class UserProfilePage extends ConsumerWidget {
  final String userId;
  const UserProfilePage({super.key, required this.userId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final profile = ref.watch(userProfileProvider(userId));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('social.userProfile'))),
      body: profile.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          onRetry: () => ref.invalidate(userProfileProvider(userId)),
        ),
        data: (p) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(userProfileProvider(userId)),
          child: ListView(
            padding: const EdgeInsets.all(AppSpacing.md),
            children: [
              _Header(p),
              const SizedBox(height: AppSpacing.md),
              if (p.limited) const _LockedNotice(),
              const SizedBox(height: AppSpacing.md),
              RelationshipBadge(
                stateLabel: t.get(p.relationship.labelKey),
                warn: p.relationship != RelationshipState.none,
              ),
              const SizedBox(height: AppSpacing.lg),
              ...actionsFor(context, ref, p),
            ],
          ),
        ),
      ),
    );
  }

  /// The single mapping from server relationship state to available actions.
  /// Extracted so the rule is testable on its own and there is exactly one
  /// place where it can be wrong.
  static List<Widget> actionsFor(
    BuildContext context,
    WidgetRef ref,
    PublicProfile p,
  ) {
    final t = context.l10n;
    final busy = ref.watch(socialActionsProvider);
    final actions = ref.read(socialActionsProvider.notifier);
    final id = p.userId;

    // "Message" is independent of friendship — a DM is a personal-interaction
    // right, not a social status (ADR-14 §2) — so it is offered to every state
    // where the server will accept it. Withheld for the blocked pair and for
    // oneself, where it would be refused with 404 or be meaningless.
    final withMessage = <Widget>[
      if (p.relationship.allowsSocialAction)
        OutlinedButton.icon(
          key: const Key('social.action.message'),
          onPressed: () => _openDirect(context, ref, id, p.bestName ?? ''),
          icon: const Icon(Icons.chat_bubble_outline_rounded, size: 18),
          label: Text(t.get('chat.message')),
        ),
    ];

    switch (p.relationship) {
      case RelationshipState.none:
        return [
          ...withMessage,
          SocialActionButton(
            buttonKey: const Key('social.action.addFriend'),
            label: t.get('social.addFriend'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(SocialActions.userKey('request', id)),
            primary: true,
            onPressed: () => _guard(
              context,
              () => actions.sendFriendRequest(id),
              successKey: 'social.requestSent',
            ),
          ),
        ];
      case RelationshipState.outgoingPending:
        return [
          ...withMessage,
          SocialActionButton(
            buttonKey: const Key('social.action.cancelRequest'),
            label: t.get('social.cancelRequest'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(SocialActions.userKey('cancel', id)),
            primary: true,
            onPressed: () => _guard(
              context,
              () => actions.cancelOutgoingRequest(id),
              successKey: 'social.requestCancelled',
            ),
          ),
        ];
      case RelationshipState.incomingPending:
        // Accept/reject need the request id, which lives in the requests list.
        // The requests screen is the actionable surface for this state; here we
        // point at it rather than guessing an id.
        return [
          ...withMessage,
          OutlinedButton.icon(
            key: const Key('social.action.viewRequests'),
            onPressed: () => context.push('/friends/requests'),
            icon: const Icon(Icons.person_add_alt_rounded, size: 18),
            label: Text(t.get('social.friendRequests')),
          ),
        ];
      case RelationshipState.friends:
        return [
          ...withMessage,
          SocialActionButton(
            buttonKey: const Key('social.action.removeFriend'),
            label: t.get('social.removeFriend'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(SocialActions.userKey('remove', id)),
            destructive: true,
            onPressed: () => _confirmRemove(context, ref, id),
          ),
          SocialActionButton(
            buttonKey: const Key('social.action.block'),
            label: t.get('social.block'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(SocialActions.userKey('block', id)),
            onPressed: () => _confirmBlock(context, ref, id),
          ),
        ];
      case RelationshipState.blocked:
        return [
          SocialActionButton(
            buttonKey: const Key('social.action.unblock'),
            label: t.get('social.unblock'),
            busyLabel: t.get('social.loading'),
            busy: busy.contains(SocialActions.userKey('unblock', id)),
            primary: true,
            onPressed: () => _guard(
              context,
              () => actions.unblockUser(id),
              successKey: 'social.userUnblocked',
            ),
          ),
        ];
      case RelationshipState.blockedByUser:
        // A wall from the other side. Offering "add friend" here would be an
        // action the server refuses with 404 and a request that leaks intent.
        return [
          SocialMessage(
            icon: Icons.block_rounded,
            message: t.get('social.state.BLOCKED_BY_USER.hint'),
          ),
        ];
      case RelationshipState.self:
        return const [];
    }
  }

  /// Resolve the DM conversation with [userId], then navigate to it.
  ///
  /// The DM is created on first open and re-opened thereafter. A blocked or
  /// unknown rider answers 404, which is reported through the shared chat error
  /// map — the rider is told the rider is unavailable and is never told which of
  /// the two it was.
  static Future<void> _openDirect(
    BuildContext context,
    WidgetRef ref,
    String userId,
    String name,
  ) async {
    final t = context.l10n;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final router = GoRouter.of(context);
    try {
      final conversation = await ref
          .read(chatActionsProvider.notifier)
          .openDirect(userId);
      router.push('/chat/${conversation.id}?name=${Uri.encodeComponent(name)}');
    } on Object catch (error) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(friendlyChatError(t, error))),
        );
      }
    }
  }

  /// Run a mutation, then show the server's confirmation or its error. Nothing
  /// is announced before the response arrives.
  static Future<void> _guard(
    BuildContext context,
    Future<void> Function() body, {
    required String successKey,
  }) async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    final t = context.l10n;
    try {
      await body();
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(SnackBar(content: Text(t.get(successKey))));
      }
    } on Object catch (e) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(SnackBar(content: Text(friendlyError(t, e))));
      }
    }
  }

  static Future<void> _confirmRemove(
    BuildContext context,
    WidgetRef ref,
    String userId,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(t.get('social.confirmRemoveFriend')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(t.get('social.reject')),
          ),
          FilledButton(
            key: const Key('social.confirm.remove'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(t.get('social.removeFriend')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      await ref.read(socialActionsProvider.notifier).removeFriend(userId);
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(t.get('social.friendRemoved'))),
        );
      }
    } on Object catch (e) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(SnackBar(content: Text(friendlyError(t, e))));
      }
    }
  }

  static Future<void> _confirmBlock(
    BuildContext context,
    WidgetRef ref,
    String userId,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(t.get('social.confirmBlock')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(t.get('social.reject')),
          ),
          FilledButton(
            key: const Key('social.confirm.block'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(t.get('social.block')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      await ref.read(socialActionsProvider.notifier).blockUser(userId);
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(t.get('social.userBlocked'))),
        );
      }
    } on Object catch (e) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(SnackBar(content: Text(friendlyError(t, e))));
      }
    }
  }
}

class _Header extends StatelessWidget {
  final PublicProfile p;
  const _Header(this.p);

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
                SocialAvatar(
                  avatarUrl: p.avatarUrl,
                  name: p.bestName,
                  radius: 32,
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        p.bestName ?? t.get('social.userProfile'),
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 2),
                      SocialHandle(username: p.username),
                    ],
                  ),
                ),
              ],
            ),
            // Withheld fields are not rendered at all: a `limited` profile must
            // not look like a rider who simply wrote no bio.
            if (!p.limited && p.bio != null && p.bio!.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              Text(p.bio!),
            ],
            if (!p.limited) ...[
              if (p.cyclingCategory != null &&
                  p.cyclingCategory!.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.md),
                Text(categoryLabel(t, p.cyclingCategory!)),
              ],
              if (p.placeLabel != null) ...[
                const SizedBox(height: AppSpacing.sm),
                Row(
                  children: [
                    const Icon(
                      Icons.place_outlined,
                      size: 16,
                      color: AppColors.textMuted,
                    ),
                    const SizedBox(width: AppSpacing.xs),
                    Expanded(
                      child: Text(
                        p.placeLabel!,
                        style: const TextStyle(color: AppColors.textMuted),
                      ),
                    ),
                  ],
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

/// Explains that the blank sections are the rider's choice, not a bug or an
/// invitation to add them.
class _LockedNotice extends StatelessWidget {
  const _LockedNotice();

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Row(
        children: [
          const Icon(Icons.lock_outline_rounded, color: AppColors.textMuted),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              t.get('social.limited'),
              style: const TextStyle(color: AppColors.textMuted),
            ),
          ),
        ],
      ),
    );
  }
}
