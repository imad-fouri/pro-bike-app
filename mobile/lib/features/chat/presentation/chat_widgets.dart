import 'package:flutter/material.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../../teams/presentation/team_widgets.dart';
import '../domain/chat.dart';

/// Shared presentation atoms for the chat surfaces.
///
/// Reuses the social and team atoms rather than duplicating them, so a rider
/// cannot tell that chat shipped in a later phase. What is new here is small and
/// specific to messages: a bubble, a day separator, and an error map that knows
/// the `CHAT_*` codes.
///
/// Nothing in this file renders a location, a ride attachment, a media preview,
/// or an account-private field. There is no widget to render one, which is the
/// cheapest possible enforcement of ADR-12 §3.

/// Map a chat API error onto a localized sentence.
///
/// The not-found family collapses to one sentence. That is not laziness: the
/// backend deliberately answers identically for "missing" and "not yours", and
/// a client that tried to distinguish them would be re-introducing the oracle the
/// server was built to remove.
String friendlyChatError(AppLocalizations t, Object error) {
  if (error is! ApiException) return t.get('social.error.generic');
  switch (error.code) {
    case 'CHAT_BLOCKED':
      return t.get('chat.error.blocked');
    case 'CHAT_TEAM_ARCHIVED':
      return t.get('chat.error.teamArchived');
    case 'CHAT_TEAM_UNAVAILABLE':
      return t.get('chat.error.teamUnavailable');
    case 'CHAT_FORBIDDEN':
      return t.get('chat.error.forbidden');
    case 'CHAT_CANNOT_TARGET_SELF':
      return t.get('chat.error.cannotTargetSelf');
    case 'CHAT_EDIT_WINDOW_CLOSED':
      return t.get('chat.error.editWindowClosed');
    case 'CHAT_MESSAGE_DELETED':
      return t.get('chat.error.messageDeleted');
    case 'CHAT_CONVERSATION_NOT_FOUND':
    case 'CHAT_USER_NOT_FOUND':
    case 'CHAT_MESSAGE_NOT_FOUND':
      return t.get('chat.error.notFound');
    default:
      return friendlyError(t, error);
  }
}

/// The `[deleted]` placeholder the server substitutes for a removed body.
///
/// Matched as a flag (`isDeleted`) everywhere it is displayed, so the styling
/// never depends on recognizing a magic string — a future placeholder wording
/// would otherwise silently lose its "this was removed" styling.
class ChatDeletedLabel extends StatelessWidget {
  const ChatDeletedLabel({super.key});

  @override
  Widget build(BuildContext context) => Text(
    context.l10n.get('chat.deleted'),
    style: const TextStyle(
      fontStyle: FontStyle.italic,
      color: AppColors.textMuted,
    ),
  );
}

/// One message bubble.
///
/// Alignment and colour come from `isMine`, which is the server's per-viewer
/// flag — not a local comparison of sender id against "my" user id, which would
/// break the moment the identity provider disagreed about who is logged in.
class MessageBubble extends StatelessWidget {
  final ChatMessage message;
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;

  const MessageBubble({
    super.key,
    required this.message,
    this.onEdit,
    this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final mine = message.isMine;
    final radius = BorderRadius.only(
      topLeft: const Radius.circular(AppRadius.card),
      topRight: const Radius.circular(AppRadius.card),
      bottomLeft: Radius.circular(mine ? AppRadius.card : 4),
      bottomRight: Radius.circular(mine ? 4 : AppRadius.card),
    );

    final showActions = onEdit != null || onDelete != null;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        key: Key('chat.bubble.${message.id}'),
        constraints: const BoxConstraints(maxWidth: 320),
        margin: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.xs,
        ),
        padding: const EdgeInsets.all(AppSpacing.sm),
        decoration: BoxDecoration(
          color: mine ? AppColors.primary : AppColors.surface2,
          borderRadius: radius,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!mine)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                child: Text(
                  message.bestName ?? '',
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: AppColors.accentLime,
                  ),
                ),
              ),
            if (message.isDeleted)
              ChatDeletedLabel()
            else
              // Selectable: riders paste ride links and route references out of a
              // conversation, and a non-selectable bubble makes that impossible.
              SelectableText(
                message.body,
                // Both bubbles sit on the app's dark surface, so both use the
                // light text colour; only the background distinguishes them.
                style: const TextStyle(color: AppColors.textOnDark),
              ),
            const SizedBox(height: AppSpacing.xs),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  _timeLabel(context, message.createdAt),
                  style: TextStyle(
                    fontSize: 11,
                    color: mine
                        ? AppColors.textOnDark.withValues(alpha: 0.7)
                        : AppColors.textMuted,
                  ),
                ),
                if (message.isEdited) ...[
                  const SizedBox(width: AppSpacing.xs),
                  Text(
                    t.get('chat.edited'),
                    style: TextStyle(
                      fontSize: 11,
                      color: mine
                          ? AppColors.textOnDark.withValues(alpha: 0.7)
                          : AppColors.textMuted,
                    ),
                  ),
                ],
                if (showActions) ...[
                  const SizedBox(width: AppSpacing.sm),
                  if (onEdit != null)
                    _BubbleAction(
                      key: Key('chat.edit.${message.id}'),
                      icon: Icons.edit_outlined,
                      tooltip: t.get('chat.edit'),
                      onPressed: onEdit,
                    ),
                  if (onDelete != null)
                    _BubbleAction(
                      key: Key('chat.delete.${message.id}'),
                      icon: Icons.delete_outline_rounded,
                      tooltip: t.get('chat.delete'),
                      onPressed: onDelete,
                      destructive: true,
                    ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }

  /// Time-only for today's messages, date+time otherwise.
  ///
  /// Done client-side from the server's UTC timestamp rather than by asking the
  /// server which — a "is this today?" question is a presentation concern, and
  /// answering it on the server would put the rider's timezone into the API.
  String _timeLabel(BuildContext context, DateTime? utc) {
    if (utc == null) return '';
    final local = utc.toLocal();
    final now = DateTime.now();
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

class _BubbleAction extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool destructive;

  const _BubbleAction({
    super.key,
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.destructive = false,
  });

  @override
  Widget build(BuildContext context) => Tooltip(
    message: tooltip,
    child: InkWell(
      onTap: onPressed,
      child: Padding(
        padding: const EdgeInsets.all(2),
        child: Icon(
          icon,
          size: 16,
          color: destructive
              ? Theme.of(context).colorScheme.error
              : AppColors.textMuted,
        ),
      ),
    ),
  );
}

/// An inbox row.
///
/// A conversation the viewer cannot send in — an archived team, say — is still
/// listed: history stays readable, and hiding the row would make the rider think
/// the conversation never existed.
class ConversationTile extends StatelessWidget {
  final Conversation conversation;
  final VoidCallback onTap;

  const ConversationTile({
    super.key,
    required this.conversation,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final unread = conversation.unreadCount;
    final name = conversation.bestName ?? t.get('chat.unnamedConversation');
    // No archived badge here on purpose: archived status is a TEAM field this
    // inbox does not carry, and the server already refuses sends for an archived
    // team — a badge would be a claim the screen cannot verify.

    return Card(
      child: ListTile(
        key: Key('chat.tile.${conversation.id}'),
        onTap: onTap,
        leading: conversation.isTeam
            ? TeamAvatar(avatarUrl: null, name: name, radius: 24)
            : SocialAvatar(avatarUrl: null, name: name, radius: 24),
        title: Row(
          children: [
            Flexible(
              child: Text(
                name,
                style: TextStyle(
                  fontWeight: unread > 0 ? FontWeight.w800 : FontWeight.w600,
                ),
              ),
            ),
            if (conversation.isTeam) ...[
              const SizedBox(width: AppSpacing.xs),
              const Icon(
                Icons.groups_rounded,
                size: 14,
                color: AppColors.textMuted,
              ),
            ],
          ],
        ),
        subtitle: Text(
          conversation.lastMessagePreview ?? t.get('chat.noMessages'),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: unread > 0 ? AppColors.textOnDark : AppColors.textMuted,
          ),
        ),
        // `99+` rather than the true count: a badge is a glanceable signal, and
        // an unbounded number would widen the row for no extra information.
        trailing: unread > 0
            ? Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: AppColors.primary,
                  borderRadius: BorderRadius.circular(AppRadius.pill),
                ),
                child: Text(
                  unread > 99 ? '99+' : '$unread',
                  style: const TextStyle(
                    color: AppColors.textOnDark,
                    fontWeight: FontWeight.w800,
                    fontSize: 12,
                  ),
                ),
              )
            : null,
      ),
    );
  }
}
