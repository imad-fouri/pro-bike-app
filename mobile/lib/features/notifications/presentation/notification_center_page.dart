import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/notification.dart';
import '../domain/notification_links.dart';
import 'notification_providers.dart';
import 'notification_widgets.dart';

/// The in-app notification center: every notification this rider received,
/// newest first.
///
/// Loading, empty, error, and retry are all explicit states, matching the social
/// and team surfaces — a notification center that silently renders nothing reads
/// as "you have no notifications" whether or not that is true.
class NotificationCenterPage extends ConsumerStatefulWidget {
  const NotificationCenterPage({super.key});

  @override
  ConsumerState<NotificationCenterPage> createState() =>
      _NotificationCenterPageState();
}

class _NotificationCenterPageState
    extends ConsumerState<NotificationCenterPage> {
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    // 0.8 of the remaining extent: a page lands before the rider hits the end.
    if (_scroll.position.pixels >= _scroll.position.maxScrollExtent * 0.8) {
      ref.read(notificationListProvider.notifier).loadMore();
    }
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  /// Mark read, then navigate.
  ///
  /// In that order deliberately: a tap on a notification the rider cannot open
  /// still counts as having seen it, and a failed navigation must not leave an
  /// unread dot the rider can no longer clear.
  Future<void> _open(AppNotification n) async {
    await _markRead(n);
    if (!mounted) return;
    final link = parseNotificationLink(n.deepLink);
    if (link == null) return;
    // Hand the intent to the pending-link provider so a not-yet-ready router
    // still receives it, then navigate through go_router only. Navigation is
    // intent; the destination re-authorizes on its own.
    ref.read(pendingLinkProvider.notifier).open(n.deepLink);
    final consumed = ref.read(pendingLinkProvider.notifier).take();
    if (consumed == null) return;
    if (consumed.id == null) {
      context.go(consumed.route);
    } else {
      context.go('${consumed.route}/${consumed.id}');
    }
  }

  Future<void> _markRead(AppNotification n) async {
    if (!n.isUnread) return;
    try {
      await ref.read(notificationListProvider.notifier).markRead(n.id);
    } on Object catch (error) {
      if (!mounted) return;
      _report(error);
    }
  }

  void _report(Object error) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(friendlyNotificationError(context.l10n, error))),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final list = ref.watch(notificationListProvider);
    final unread = ref.watch(unreadCountProvider).value ?? 0;

    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('notifications.title')),
        actions: [
          if (unread > 0)
            TextButton(
              key: const Key('notification.readAll'),
              onPressed: () async {
                try {
                  await ref
                      .read(notificationListProvider.notifier)
                      .markAllRead();
                } on Object catch (error) {
                  if (mounted) _report(error);
                }
              },
              child: Text(t.get('notifications.markAllRead')),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          await ref.read(notificationListProvider.notifier).refresh();
          await ref.read(unreadCountProvider.notifier).refresh();
        },
        child: list.when(
          loading: () => ListView(
            children: [
              const SizedBox(height: AppSpacing.xl),
              Center(
                child: Text(
                  t.get('social.loading'),
                  style: const TextStyle(color: AppColors.textMuted),
                ),
              ),
            ],
          ),
          error: (e, _) => SocialErrorView(
            error: e,
            message: friendlyNotificationError(t, e),
            onRetry: () => ref.invalidate(notificationListProvider),
          ),
          data: (data) {
            if (data.isEmpty) {
              // A ListView, not a bare column: the empty state must still be
              // scrollable or pull-to-refresh has nothing to pull.
              return ListView(
                children: [
                  const SizedBox(height: AppSpacing.xl),
                  SocialMessage(
                    icon: Icons.notifications_none_rounded,
                    message: t.get('notifications.noneHint'),
                  ),
                ],
              );
            }
            final rows = <Widget>[
              for (final n in data.items)
                NotificationTile(
                  notification: n,
                  onTap: () => _open(n),
                  onMarkRead: () => _markRead(n),
                ),
            ];
            if (data.loadingMore) {
              rows.add(
                const Padding(
                  padding: EdgeInsets.all(AppSpacing.md),
                  child: Center(
                    child: SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  ),
                ),
              );
            }
            return ListView(
              controller: _scroll,
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: rows,
            );
          },
        ),
      ),
    );
  }
}
