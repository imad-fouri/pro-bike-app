import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Pending requests, split into Incoming and Outgoing.
///
/// Incoming is the actionable surface: accept, reject, and block all live here
/// because this screen carries the request ids the API requires. Outgoing is
/// read-only apart from cancel.
///
/// Every button reads its busy flag from [SocialActions], the same state that
/// guards duplicate submissions, and every outcome message appears only after
/// the server responds.
class FriendRequestsPage extends ConsumerWidget {
  const FriendRequestsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.get('social.friendRequests')),
          bottom: TabBar(
            labelColor: AppColors.primary,
            unselectedLabelColor: AppColors.textMuted,
            indicatorColor: AppColors.primary,
            tabs: [
              Tab(
                key: const Key('social.tab.incoming'),
                text: t.get('social.incoming'),
              ),
              Tab(
                key: const Key('social.tab.outgoing'),
                text: t.get('social.outgoing'),
              ),
            ],
          ),
          actions: [
            IconButton(
              key: const Key('social.requests.blocks'),
              tooltip: t.get('social.blockedUsers'),
              onPressed: () => context.push('/friends/blocked'),
              icon: const Icon(Icons.block_rounded),
            ),
          ],
        ),
        body: TabBarView(
          children: [
            _RequestList(direction: RequestDirection.incoming),
            _RequestList(direction: RequestDirection.outgoing),
          ],
        ),
      ),
    );
  }
}

enum RequestDirection { incoming, outgoing }

class _RequestList extends ConsumerWidget {
  final RequestDirection direction;
  const _RequestList({required this.direction});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final isIncoming = direction == RequestDirection.incoming;
    final provider = isIncoming
        ? incomingRequestsProvider
        : outgoingRequestsProvider;
    final paged = ref.watch(provider);

    return paged.when(
      loading: () => Center(
        child: Text(
          t.get('social.loading'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      ),
      error: (e, _) => SocialErrorView(
        error: e,
        onRetry: () => ref.read(provider.notifier).refresh(),
      ),
      data: (page) {
        if (page.items.isEmpty) {
          return SocialMessage(
            icon: isIncoming ? Icons.inbox_rounded : Icons.outbox_rounded,
            message: t.get('social.noRequests'),
          );
        }
        return RefreshIndicator(
          onRefresh: () => ref.read(provider.notifier).refresh(),
          child: NotificationListener<ScrollNotification>(
            onNotification: (n) {
              if (n.metrics.extentAfter < 200) {
                ref.read(provider.notifier).loadMore();
              }
              return false;
            },
            child: ListView.separated(
              itemCount: page.items.length + 1,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
              padding: const EdgeInsets.all(AppSpacing.md),
              itemBuilder: (context, i) {
                if (i == page.items.length) {
                  return SocialLoadMoreIndicator(visible: page.loadingMore);
                }
                final request = page.items[i];
                return isIncoming
                    ? _IncomingTile(request: request)
                    : _OutgoingTile(request: request);
              },
            ),
          ),
        );
      },
    );
  }
}

class _IncomingTile extends ConsumerWidget {
  final FriendRequest request;
  const _IncomingTile({required this.request});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(socialActionsProvider);
    final actions = ref.read(socialActionsProvider.notifier);
    final id = request.id;
    final userId = request.userId;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            InkWell(
              key: Key('social.incoming.$id'),
              onTap: () => context.push('/users/$userId'),
              child: Row(
                children: [
                  SocialAvatar(
                    avatarUrl: request.avatarUrl,
                    name: request.bestName,
                  ),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          request.bestName ?? t.get('social.userProfile'),
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 2),
                        SocialHandle(username: request.username),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            Row(
              children: [
                Expanded(
                  child: SocialActionButton(
                    buttonKey: Key('social.incoming.accept.$id'),
                    label: t.get('social.accept'),
                    busyLabel: t.get('social.loading'),
                    busy: busy.contains(SocialActions.requestKey('accept', id)),
                    primary: true,
                    onPressed: () => _run(
                      context,
                      () => actions.acceptFriendRequest(id, userId),
                      t.get('social.requestAccepted'),
                    ),
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: SocialActionButton(
                    buttonKey: Key('social.incoming.reject.$id'),
                    label: t.get('social.reject'),
                    busyLabel: t.get('social.loading'),
                    busy: busy.contains(SocialActions.requestKey('reject', id)),
                    onPressed: () => _run(
                      context,
                      () => actions.rejectFriendRequest(id, userId),
                      t.get('social.requestRejected'),
                    ),
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                SocialActionButton(
                  buttonKey: Key('social.incoming.block.$id'),
                  label: t.get('social.block'),
                  busyLabel: t.get('social.loading'),
                  busy: busy.contains(SocialActions.userKey('block', userId)),
                  destructive: true,
                  onPressed: () => _run(
                    context,
                    () => actions.blockUser(userId),
                    t.get('social.userBlocked'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _OutgoingTile extends ConsumerWidget {
  final FriendRequest request;
  const _OutgoingTile({required this.request});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(socialActionsProvider);
    final actions = ref.read(socialActionsProvider.notifier);
    final id = request.id;
    final userId = request.userId;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Row(
          children: [
            SocialAvatar(avatarUrl: request.avatarUrl, name: request.bestName),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: InkWell(
                key: Key('social.outgoing.$id'),
                onTap: () => context.push('/users/$userId'),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      request.bestName ?? t.get('social.userProfile'),
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 2),
                    RelationshipBadge(
                      stateLabel: t.get('social.state.OUTGOING_PENDING'),
                      warn: true,
                    ),
                  ],
                ),
              ),
            ),
            SocialActionButton(
              buttonKey: Key('social.outgoing.cancel.$id'),
              label: t.get('social.cancelRequest'),
              busyLabel: t.get('social.loading'),
              busy: busy.contains(SocialActions.requestKey('cancel', id)),
              onPressed: () => _run(
                context,
                () => actions.cancelFriendRequest(id, userId),
                t.get('social.requestCancelled'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Await the server, then announce its result — or its error. Nothing is
/// announced before the response, and a failure never advances the UI.
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
      messenger.showSnackBar(SnackBar(content: Text(friendlyError(t, e))));
    }
  }
}
