import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Friend list — the real screen behind `/friends`, replacing the placeholder.
///
/// Accept/reject/remove/block all live here as well as on the profile screen,
/// because this is the surface a rider lands on and it carries the request ids
/// those operations need. Every mutation awaits the server, then the list is
/// refreshed; nothing is removed optimistically, so a failed call cannot leave
/// a friend row that does not exist.
class FriendsPage extends ConsumerWidget {
  const FriendsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final list = ref.watch(friendListProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('social.friends')),
        actions: [
          IconButton(
            key: const Key('social.friends.search'),
            tooltip: t.get('social.searchUsers'),
            onPressed: () => context.push('/friends/search'),
            icon: const Icon(Icons.search_rounded),
          ),
          IconButton(
            key: const Key('social.friends.requests'),
            tooltip: t.get('social.friendRequests'),
            onPressed: () => context.push('/friends/requests'),
            icon: const Icon(Icons.person_add_alt_rounded),
          ),
          IconButton(
            key: const Key('social.friends.blocks'),
            tooltip: t.get('social.blockedUsers'),
            onPressed: () => context.push('/friends/blocked'),
            icon: const Icon(Icons.block_rounded),
          ),
        ],
      ),
      body: list.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          onRetry: () => ref.read(friendListProvider.notifier).refresh(),
        ),
        data: (paged) {
          if (paged.items.isEmpty) {
            return SocialMessage(
              icon: Icons.people_alt_rounded,
              message: t.get('social.noFriends'),
              actionLabel: t.get('social.searchUsers'),
              onAction: () => context.push('/friends/search'),
            );
          }
          return RefreshIndicator(
            onRefresh: () => ref.read(friendListProvider.notifier).refresh(),
            child: NotificationListener<ScrollNotification>(
              // One page fetch when the tail comes into view. The notifier
              // ignores this while a page is already in flight, so a fling
              // cannot queue duplicate requests.
              onNotification: (n) {
                if (n.metrics.extentAfter < 200) {
                  ref.read(friendListProvider.notifier).loadMore();
                }
                return false;
              },
              child: ListView.separated(
                itemCount: paged.items.length + 1,
                separatorBuilder: (_, _) =>
                    const SizedBox(height: AppSpacing.xs),
                padding: const EdgeInsets.all(AppSpacing.md),
                itemBuilder: (context, i) {
                  if (i == paged.items.length) {
                    return SocialLoadMoreIndicator(visible: paged.loadingMore);
                  }
                  return _FriendTile(friend: paged.items[i]);
                },
              ),
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        key: const Key('social.friends.add'),
        onPressed: () => context.push('/friends/search'),
        child: const Icon(Icons.person_add_alt_rounded),
      ),
    );
  }
}

class _FriendTile extends ConsumerWidget {
  final SocialFriend friend;
  const _FriendTile({required this.friend});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(socialActionsProvider);
    final id = friend.userId;
    final removing = busy.contains(SocialActions.userKey('remove', id));
    final blocking = busy.contains(SocialActions.userKey('block', id));

    return Card(
      child: ListTile(
        key: Key('social.friend.$id'),
        leading: SocialAvatar(
          avatarUrl: friend.avatarUrl,
          name: friend.bestName,
        ),
        title: Text(friend.bestName ?? t.get('social.userProfile')),
        subtitle: SocialHandle(username: friend.username),
        trailing: PopupMenuButton<String>(
          key: Key('social.friend.menu.$id'),
          enabled: !removing && !blocking,
          onSelected: (value) => _onSelect(context, ref, value),
          itemBuilder: (context) => [
            PopupMenuItem(
              value: 'remove',
              child: Row(
                children: [
                  const Icon(Icons.person_remove_outlined, size: 18),
                  const SizedBox(width: AppSpacing.sm),
                  Flexible(child: Text(t.get('social.removeFriend'))),
                ],
              ),
            ),
            PopupMenuItem(
              value: 'block',
              child: Row(
                children: [
                  const Icon(Icons.block_rounded, size: 18),
                  const SizedBox(width: AppSpacing.sm),
                  Flexible(child: Text(t.get('social.block'))),
                ],
              ),
            ),
          ],
        ),
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
    final id = friend.userId;
    final isRemove = value == 'remove';
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(
          isRemove
              ? t.get('social.confirmRemoveFriend')
              : t.get('social.confirmBlock'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(t.get('social.reject')),
          ),
          FilledButton(
            key: Key(
              isRemove ? 'social.confirm.remove' : 'social.confirm.block',
            ),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(
              isRemove ? t.get('social.removeFriend') : t.get('social.block'),
            ),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final actions = ref.read(socialActionsProvider.notifier);
    try {
      if (isRemove) {
        await actions.removeFriend(id);
      } else {
        await actions.blockUser(id);
      }
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(
            content: Text(
              isRemove
                  ? t.get('social.friendRemoved')
                  : t.get('social.userBlocked'),
            ),
          ),
        );
      }
    } on Object catch (e) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(SnackBar(content: Text(friendlyError(t, e))));
      }
    }
  }
}
