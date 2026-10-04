import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Riders this account has blocked.
///
/// Only the blocker's own list is visible. A rider who blocked *you* is never
/// listed here, and the profile screen shows an unavailable state instead of
/// confirming it — otherwise the block list becomes an oracle for probing who
/// blocked you (ADR-12 §3).
///
/// Unblocking lifts the wall and nothing more: the server deleted the
/// friendship row when the block went up, so nothing is silently restored and
/// the rider must start from a fresh request.
class BlockedUsersPage extends ConsumerWidget {
  const BlockedUsersPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final list = ref.watch(blockListProvider);
    return Scaffold(
      appBar: AppBar(title: Text(t.get('social.blockedUsers'))),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.md,
              AppSpacing.md,
              0,
            ),
            child: Container(
              padding: const EdgeInsets.all(AppSpacing.md),
              decoration: BoxDecoration(
                color: AppColors.surface2,
                borderRadius: BorderRadius.circular(AppRadius.card),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(
                    Icons.info_outline_rounded,
                    size: 20,
                    color: AppColors.textMuted,
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(
                      t.get('social.blocked.explainer'),
                      style: const TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 13,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          Expanded(
            child: list.when(
              loading: () => Center(
                child: Text(
                  t.get('social.loading'),
                  style: const TextStyle(color: AppColors.textMuted),
                ),
              ),
              error: (e, _) => SocialErrorView(
                error: e,
                onRetry: () => ref.read(blockListProvider.notifier).refresh(),
              ),
              data: (paged) {
                if (paged.items.isEmpty) {
                  return SocialMessage(
                    icon: Icons.block_rounded,
                    message: t.get('social.noBlocked'),
                  );
                }
                return RefreshIndicator(
                  onRefresh: () =>
                      ref.read(blockListProvider.notifier).refresh(),
                  child: NotificationListener<ScrollNotification>(
                    onNotification: (n) {
                      if (n.metrics.extentAfter < 200) {
                        ref.read(blockListProvider.notifier).loadMore();
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
                          return SocialLoadMoreIndicator(
                            visible: paged.loadingMore,
                          );
                        }
                        return _BlockedTile(blocked: paged.items[i]);
                      },
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _BlockedTile extends ConsumerWidget {
  final BlockedUser blocked;
  const _BlockedTile({required this.blocked});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(socialActionsProvider);
    final id = blocked.userId;

    return Card(
      child: ListTile(
        key: Key('social.blocked.$id'),
        leading: SocialAvatar(
          // A blocked rider's avatar is not fetched: their profile is walled
          // off, so the initial is all this screen needs.
          avatarUrl: null,
          name: blocked.bestName,
        ),
        title: Text(blocked.bestName ?? t.get('social.userProfile')),
        subtitle: SocialHandle(username: blocked.username),
        trailing: SocialActionButton(
          buttonKey: Key('social.blocked.unblock.$id'),
          label: t.get('social.unblock'),
          busyLabel: t.get('social.loading'),
          busy: busy.contains(SocialActions.userKey('unblock', id)),
          primary: true,
          onPressed: () => _unblock(context, ref, id),
        ),
      ),
    );
  }

  Future<void> _unblock(
    BuildContext context,
    WidgetRef ref,
    String userId,
  ) async {
    final t = context.l10n;
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      await ref.read(socialActionsProvider.notifier).unblockUser(userId);
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(t.get('social.userUnblocked'))),
        );
      }
    } on Object catch (e) {
      if (messenger != null && messenger.mounted) {
        messenger.showSnackBar(SnackBar(content: Text(friendlyError(t, e))));
      }
    }
  }
}
