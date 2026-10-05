import 'package:flutter/material.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/notification.dart';

/// Shared presentation atoms for the notification surfaces.
///
/// Reuses the Phase 8.1 social atoms rather than duplicating them, so a rider
/// cannot tell that notifications shipped in a later phase.
///
/// The one genuinely new thing here is [NotificationText]: a notification
/// arrives as a localization KEY, and this build may not know it — a newer
/// server can add a type before the app ships support for it. Rendering an
/// unknown key as the raw key string would read as a bug to the rider, so the
/// unknown case degrades to a real, generic sentence.
class NotificationText {
  const NotificationText._();

  /// Render a notification's title, falling back when the key is unknown.
  static String title(AppLocalizations t, AppNotification n) =>
      _render(t, n, 'title', 'notifications.generic.title');

  /// Render a notification's body, with `params` substituted.
  static String body(AppLocalizations t, AppNotification n) =>
      _render(t, n, 'body', 'notifications.generic.body');

  static String _render(
    AppLocalizations t,
    AppNotification n,
    String suffix,
    String fallbackKey,
  ) {
    final key = '${n.l10nKey}.$suffix';
    // `has` rather than `get`: an unknown key must not be rendered as itself.
    if (!t.has(key)) return t.get(fallbackKey);
    return t.getWith(key, n.params);
  }
}

/// Map a notification API error onto a localized sentence.
///
/// The not-found family collapses to one message, matching the backend's
/// deliberate refusal to distinguish "missing" from "not yours" — a client that
/// tried to distinguish would re-introduce the oracle.
String friendlyNotificationError(AppLocalizations t, Object error) {
  if (error is! ApiException) return t.get('social.error.generic');
  switch (error.code) {
    case 'NOTIFICATION_NOT_FOUND':
    case 'PUSH_DEVICE_NOT_FOUND':
      return t.get('notifications.error.read');
    case 'PUSH_DEVICE_CONFLICT':
      return t.get('social.error.conflict');
    case 'PUSH_DEVICE_INVALID':
      return t.get('social.error.validation');
    case 'PUSH_DEVICE_PLATFORM_UNSUPPORTED':
      return t.get('notifications.settings.unavailable');
    default:
      return friendlyError(t, error);
  }
}

/// Icon for a notification type. Decorative only — the title carries meaning.
IconData notificationIcon(NotificationType type) => switch (type) {
  NotificationType.friendRequest ||
  NotificationType.friendRequestAccepted => Icons.person_add_alt_rounded,
  NotificationType.teamInvitation => Icons.groups_rounded,
  NotificationType.teamJoinRequest => Icons.how_to_reg_rounded,
  NotificationType.teamMemberRemoved => Icons.group_remove_rounded,
  NotificationType.teamArchived => Icons.archive_rounded,
  NotificationType.chatMessage => Icons.chat_bubble_rounded,
  NotificationType.chatMessageTeam => Icons.forum_rounded,

  // Phase 9. The three RIDE events share one icon on purpose: they are three
  // states of a single thing, and giving each a distinct glyph would imply they
  // are three different things to act on. `directions_bike` is also the icon the
  // ride list and chat inbox use, so the row reads as "ride" before the title.
  NotificationType.groupRideInvitation ||
  NotificationType.groupRideAccepted ||
  NotificationType.groupRideStarted => Icons.directions_bike_rounded,
  NotificationType.chatMessageGroupRide => Icons.forum_rounded,

  NotificationType.system => Icons.info_outline_rounded,
};

/// One notification row.
///
/// Unread is signalled by a leading dot AND by weight, so the state does not
/// depend on colour alone — a rider with a colour-vision deficiency still sees
/// it. Tapping marks read and then navigates; it never the other way round, so a
/// failed navigation cannot silently swallow a message.
class NotificationTile extends StatelessWidget {
  final AppNotification notification;
  final VoidCallback? onTap;
  final VoidCallback? onMarkRead;

  const NotificationTile({
    super.key,
    required this.notification,
    this.onTap,
    this.onMarkRead,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final unread = notification.isUnread;
    final title = NotificationText.title(t, notification);
    final body = NotificationText.body(t, notification);

    return Card(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      child: InkWell(
        key: Key('notification.tile.${notification.id}'),
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadius.card),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (unread)
                Container(
                  key: Key('notification.unread.${notification.id}'),
                  margin: const EdgeInsets.only(top: 6, right: AppSpacing.sm),
                  width: 8,
                  height: 8,
                  decoration: const BoxDecoration(
                    color: AppColors.primary,
                    shape: BoxShape.circle,
                  ),
                )
              else
                const SizedBox(width: AppSpacing.sm),
              Icon(
                notificationIcon(notification.type),
                color: unread ? AppColors.accentLime : AppColors.textMuted,
                size: 22,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: TextStyle(
                        fontWeight: unread ? FontWeight.w800 : FontWeight.w600,
                        color: unread
                            ? AppColors.textOnDark
                            : AppColors.textMuted,
                      ),
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      body,
                      style: const TextStyle(color: AppColors.textMuted),
                    ),
                    if (notification.createdAt != null) ...[
                      const SizedBox(height: AppSpacing.xs),
                      Text(
                        _timeLabel(context, notification.createdAt!),
                        style: const TextStyle(
                          fontSize: 11,
                          color: AppColors.textMuted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (unread && onMarkRead != null)
                IconButton(
                  key: Key('notification.markRead.${notification.id}'),
                  tooltip: t.get('notifications.action.read'),
                  icon: const Icon(Icons.mark_email_read_outlined, size: 18),
                  onPressed: onMarkRead,
                ),
            ],
          ),
        ),
      ),
    );
  }

  /// Localized relative time for today, absolute otherwise.
  ///
  /// Client-side from the server's UTC timestamp: "is this today?" is a
  /// presentation question, and answering it on the server would put the
  /// rider's timezone into the API. The relative phrases are localized rather
  /// than assembled from a number, because "5 min" is not a phrase that
  /// translates — and in Arabic it is not even word order.
  String _timeLabel(BuildContext context, DateTime utc) {
    final t = context.l10n;
    final local = utc.toLocal();
    final now = DateTime.now();
    final minutes = now.difference(local).inMinutes;
    if (minutes < 1) return t.get('notifications.time.justNow');
    if (minutes < 60) {
      return t.getWith('notifications.time.minutes', {'count': '$minutes'});
    }
    final hours = now.difference(local).inHours;
    if (hours < 24) {
      return t.getWith('notifications.time.hours', {'count': '$hours'});
    }
    final sameDay =
        local.year == now.year &&
        local.month == now.month &&
        local.day == now.day;
    final hh = local.hour.toString().padLeft(2, '0');
    final mm = local.minute.toString().padLeft(2, '0');
    if (sameDay) return '$hh:$mm';
    final dd = local.day.toString().padLeft(2, '0');
    final mo = local.month.toString().padLeft(2, '0');
    return '$dd/$mo $hh:$mm';
  }
}

/// Unread badge for the home app bar.
///
/// Renders nothing at zero so the bell is unadorned when there is nothing to
/// say. `99+` rather than the true count: a badge is a glanceable signal, and an
/// unbounded number would widen the layout for no extra information.
class NotificationBadge extends StatelessWidget {
  final int count;
  const NotificationBadge({super.key, required this.count});

  @override
  Widget build(BuildContext context) {
    if (count <= 0) return const SizedBox.shrink();
    return Container(
      key: const Key('notification.badge'),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      constraints: const BoxConstraints(minWidth: 18),
      decoration: BoxDecoration(
        color: AppColors.primary,
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Text(
        count > 99 ? '99+' : '$count',
        textAlign: TextAlign.center,
        style: const TextStyle(
          color: AppColors.textOnDark,
          fontWeight: FontWeight.w800,
          fontSize: 11,
        ),
      ),
    );
  }
}

/// The bell, with its unread badge, as the home app bar presents it.
class NotificationBell extends StatelessWidget {
  final int unreadCount;
  final VoidCallback onTap;

  const NotificationBell({
    super.key,
    required this.unreadCount,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Stack(
      alignment: Alignment.topRight,
      children: [
        IconButton(
          key: const Key('notification.bell'),
          icon: const Icon(Icons.notifications_none_rounded),
          onPressed: onTap,
        ),
        if (unreadCount > 0)
          Positioned(
            right: 6,
            top: 6,
            child: NotificationBadge(count: unreadCount),
          ),
      ],
    );
  }
}
