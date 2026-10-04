import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/chat.dart';
import '../domain/chat_validators.dart';
import 'chat_providers.dart';
import 'chat_widgets.dart';

/// One conversation: history, a composer, and per-message edit/delete.
///
/// Three behaviours worth naming, because each is easy to get wrong:
///
/// * NEWEST AT THE BOTTOM. The list is not reversed; the scroll starts at the end
///   and stays there while the rider is already at the bottom, and does NOT
///   yank them down when they are reading history. Being yanked mid-read is the
///   classic chat-scroll bug.
/// * `can_edit` IS SERVER-AUTHORITATIVE. The edit affordance appears only when
///   the server said so on this read, so a device with a skewed clock cannot
///   offer an edit that would be refused with 409.
/// * THE COMPOSER CLEARS ON SUCCESS. Not on tap: an optimistic clear followed by
///   a failed send loses the rider's text, and the retry would need a second
///   client id to stay idempotent.
class ConversationScreen extends ConsumerStatefulWidget {
  final String conversationId;
  final String title;

  const ConversationScreen({
    super.key,
    required this.conversationId,
    required this.title,
  });

  @override
  ConsumerState<ConversationScreen> createState() => _ConversationScreenState();
}

class _ConversationScreenState extends ConsumerState<ConversationScreen> {
  final _controller = TextEditingController();
  final _scroll = ScrollController();

  /// Set while a send is in flight, so the button shows progress and the text
  /// field stays put.
  bool _sending = false;
  bool _atBottom = true;

  /// The idempotency key for the send currently in flight.
  ///
  /// Held rather than generated per call so a retry after a network error reuses
  /// the same id — a fresh id would store the message twice, which is the exact
  /// failure the key exists to prevent.
  String? _pendingClientMessageId;

  /// Message currently being edited, or null when composing a new one.
  ChatMessage? _editing;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final position = _scroll.position;
    final nearBottom = position.pixels >= position.maxScrollExtent - 80;
    if (nearBottom != _atBottom) setState(() => _atBottom = nearBottom);
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    _controller.dispose();
    super.dispose();
  }

  /// Scroll to the newest message, unless the rider is reading history.
  void _scrollToBottom({bool force = false}) {
    if (!force && !_atBottom) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  Future<void> _send() async {
    final text = _controller.text;
    if (!canSendMessage(text) || _sending) return;

    final conversationId = widget.conversationId;
    // One id per attempt, reused across any retry of this attempt.
    final clientId = _pendingClientMessageId ??= newClientMessageId();

    setState(() => _sending = true);
    final notifier = ref.read(
      conversationThreadProvider(conversationId).notifier,
    );
    try {
      await notifier.send(text.trim(), clientId);
      if (!mounted) return;
      _controller.clear();
      // Only now: the message is stored, so clearing cannot lose it.
      setState(() {
        _sending = false;
        _pendingClientMessageId = null;
        _atBottom = true;
      });
      _scrollToBottom(force: true);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _sending = false);
      _report(context, error);
    }
  }

  void _startEdit(ChatMessage message) {
    setState(() {
      _editing = message;
      _controller.text = message.body;
    });
  }

  void _cancelEdit() {
    setState(() {
      _editing = null;
      _controller.clear();
    });
  }

  Future<void> _submitEdit() async {
    final target = _editing;
    final text = _controller.text;
    if (target == null || !canSendMessage(text)) return;
    final notifier = ref.read(
      conversationThreadProvider(widget.conversationId).notifier,
    );
    try {
      await notifier.edit(target.id, text.trim());
      if (!mounted) return;
      _cancelEdit();
    } on Object catch (error) {
      if (!mounted) return;
      _report(context, error);
    }
  }

  Future<void> _delete(ChatMessage message) async {
    final notifier = ref.read(
      conversationThreadProvider(widget.conversationId).notifier,
    );
    try {
      await notifier.delete(message.id);
    } on Object catch (error) {
      if (!mounted) return;
      _report(context, error);
    }
  }

  /// Errors are surfaced as a snackbar rather than a dialog: a dialog would
  /// block the conversation the rider is trying to read.
  void _report(BuildContext context, Object error) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(friendlyChatError(context.l10n, error))),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final thread = ref.watch(conversationThreadProvider(widget.conversationId));

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title, overflow: TextOverflow.ellipsis),
        actions: [
          if (!_atBottom)
            IconButton(
              key: const Key('chat.jump.latest'),
              tooltip: t.get('chat.jumpToLatest'),
              icon: const Icon(Icons.arrow_downward_rounded),
              // Explicit, because a rider who scrolled up needs a way back that
              // does not depend on new messages arriving to pull them down.
              onPressed: () => _scrollToBottom(force: true),
            ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: thread.when(
              loading: () => Center(
                child: Text(
                  t.get('social.loading'),
                  style: const TextStyle(color: AppColors.textMuted),
                ),
              ),
              error: (e, _) => SocialErrorView(
                error: e,
                message: friendlyChatError(t, e),
                onRetry: () => ref.invalidate(
                  conversationThreadProvider(widget.conversationId),
                ),
              ),
              data: (data) => _history(data),
            ),
          ),
          _composer(),
        ],
      ),
    );
  }

  Widget _history(ConversationThread data) {
    if (data.messages.isEmpty) {
      return SocialMessage(
        icon: Icons.chat_bubble_outline_rounded,
        message: context.l10n.get('chat.noMessagesYet'),
      );
    }
    // One extra row at the head holds the "load earlier messages" affordance.
    //
    // It is an explicit button rather than an automatic prefetch on scroll: a
    // rider who never scrolls back should not pay for another page, and an
    // auto-prefetch would fire again after every merge, quietly walking the
    // whole history over several screens of scrolling (ADR-14 §8).
    final rows = data.messages.length + 1;
    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.md),
      itemCount: rows,
      itemBuilder: (context, index) {
        if (index == 0) return _loadEarlier(data);
        final message = data.messages[index - 1];
        return MessageBubble(
          message: message,
          // Delete is offered whenever the server considers it the viewer's own
          // message; the edit affordance follows `can_edit` exactly, so the
          // 15-minute window is never re-derived on the device.
          onDelete: message.isMine && !message.isDeleted
              ? () => _delete(message)
              : null,
          onEdit: message.canEdit ? () => _startEdit(message) : null,
        );
      },
    );
  }

  Widget _loadEarlier(ConversationThread data) {
    if (!data.hasMoreHistory) return const SizedBox.shrink();
    return Center(
      child: data.loadingOlder
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : TextButton(
              key: const Key('chat.load.older'),
              onPressed: () => ref
                  .read(
                    conversationThreadProvider(widget.conversationId).notifier,
                  )
                  .loadOlder(),
              child: Text(context.l10n.get('chat.loadEarlier')),
            ),
    );
  }

  Widget _composer() {
    final t = context.l10n;
    final body = _controller.text;
    final problem = validateMessageBody(body);
    final remaining = remainingMessageChars(body);
    final editing = _editing != null;

    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.sm),
        decoration: const BoxDecoration(
          color: AppColors.surface,
          border: Border(top: BorderSide(color: AppColors.surface2)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (editing)
              Row(
                children: [
                  Expanded(
                    child: Text(
                      t.get('chat.editingMessage'),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.accentLime,
                      ),
                    ),
                  ),
                  IconButton(
                    key: const Key('chat.edit.cancel'),
                    tooltip: t.get('chat.cancelEdit'),
                    icon: const Icon(Icons.close_rounded, size: 18),
                    onPressed: _cancelEdit,
                  ),
                ],
              ),
            TextField(
              key: const Key('chat.composer'),
              controller: _controller,
              minLines: 1,
              maxLines: 5,
              maxLength: kMessageBodyMaxLength,
              enabled: !_sending,
              textInputAction: TextInputAction.newline,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                hintText: t.get('chat.hint'),
                counterText: '',
                border: const OutlineInputBorder(),
                errorText: problem == MessageBodyProblem.tooLong
                    ? t.get('chat.error.tooLong')
                    : null,
              ),
            ),
            const SizedBox(height: AppSpacing.xs),
            Row(
              children: [
                // A progress bar rather than a hidden clamp: over the limit the
                // rider needs to see how far over they are.
                if (remaining < 500)
                  Expanded(
                    child: Text(
                      '$remaining',
                      style: TextStyle(
                        fontSize: 12,
                        color: remaining < 0
                            ? Theme.of(context).colorScheme.error
                            : AppColors.textMuted,
                      ),
                    ),
                  )
                else
                  const Spacer(),
                if (problem == MessageBodyProblem.empty)
                  Padding(
                    padding: const EdgeInsets.only(right: AppSpacing.sm),
                    child: Text(
                      t.get('chat.error.empty'),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textMuted,
                      ),
                    ),
                  ),
                SocialActionButton(
                  buttonKey: Key(editing ? 'chat.edit.submit' : 'chat.send'),
                  primary: true,
                  label: editing ? t.get('chat.save') : t.get('chat.send'),
                  busyLabel: editing
                      ? t.get('chat.saving')
                      : t.get('chat.sending'),
                  busy: _sending,
                  onPressed: problem != null
                      ? null
                      : (editing ? _submitEdit : _send),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
