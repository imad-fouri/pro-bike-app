import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/chat_repository.dart';
import '../data/chat_transport.dart';
import '../domain/chat.dart';
import '../domain/chat_validators.dart';

final chatRepositoryProvider = Provider<ChatRepository>(
  (ref) => ChatRepository(ref.watch(apiClientProvider)),
);

/// The realtime transport.
///
/// One instance for the app, `autoDispose` so its poll timers exist only while a
/// conversation screen is actually open — a timer polling every few seconds in
/// the background is exactly the kind of thing that shows up as a battery
/// complaint (ADR-14 §11).
///
/// Overriding this provider is how tests replace polling with a scripted stream.
final chatTransportProvider = Provider<ChatTransport>((ref) {
  final transport = PollingChatTransport(
    repository: ref.watch(chatRepositoryProvider),
  );
  ref.onDispose(transport.dispose);
  return transport;
});

// ---------------------------------------------------------------------------
// Inbox
// ---------------------------------------------------------------------------

/// The viewer's inbox.
///
/// A `FutureProvider` rather than a paging notifier: a chat inbox is short and
/// the rider pulls to refresh. Paging it would add machinery for a list that is
/// routinely under a screenful.
final conversationListProvider = FutureProvider.autoDispose<ConversationPage>((
  ref,
) {
  return ref.watch(chatRepositoryProvider).conversations(pageSize: 100);
});

/// Re-read every inbox surface.
///
/// Called after anything that can change unread counts. Targeted rather than
/// global so opening a conversation does not nuke unrelated family providers.
void invalidateInbox(Ref ref) {
  ref.invalidate(conversationListProvider);
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

/// One conversation's message history, oldest-first for display.
///
/// The server returns newest-first pages; this holds them oldest-first because a
/// chat list is read bottom-up, and re-reversing on every merge is where
/// off-by-one ordering bugs come from.
class ConversationThread {
  final List<ChatMessage> messages;

  /// Cursor for the next older page, or null at the start of history.
  final int? oldestCursor;
  final bool hasMoreHistory;
  final bool loadingOlder;
  final bool sending;

  const ConversationThread({
    this.messages = const [],
    this.oldestCursor,
    this.hasMoreHistory = false,
    this.loadingOlder = false,
    this.sending = false,
  });

  /// Highest `seq` seen — what gets marked read.
  int get newestSeq => messages.isEmpty ? 0 : messages.last.seq;

  ConversationThread copyWith({
    List<ChatMessage>? messages,
    int? oldestCursor,
    bool clearCursor = false,
    bool? hasMoreHistory,
    bool? loadingOlder,
    bool? sending,
  }) => ConversationThread(
    messages: messages ?? this.messages,
    oldestCursor: clearCursor ? null : (oldestCursor ?? this.oldestCursor),
    hasMoreHistory: hasMoreHistory ?? this.hasMoreHistory,
    loadingOlder: loadingOlder ?? this.loadingOlder,
    sending: sending ?? this.sending,
  );
}

/// Merges pages into one ordered list.
///
/// Deduped by message id, not by `seq`: the id is what the server is
/// authoritative about, and a page fetched twice (a poll landing while the rider
/// also pulled to refresh) must not produce two bubbles. Sorting by `seq` is what
/// keeps a late page from prepending itself above newer messages.
List<ChatMessage> _merge(
  List<ChatMessage> existing,
  List<ChatMessage> incoming,
) {
  final byId = <String, ChatMessage>{for (final m in existing) m.id: m};
  for (final m in incoming) {
    byId[m.id] = m;
  }
  final merged = byId.values.toList()..sort((a, b) => a.seq.compareTo(b.seq));
  return merged;
}

/// Drives one conversation: initial load, polling subscription, older-history
/// paging, send, edit, delete, and read-state updates.
///
/// Three decisions worth stating, because each one is a bug the obvious
/// implementation has:
///
/// * MERGE BY ID. A poll and a pull-to-refresh can return the same message; the
///   obvious `addAll` renders it twice.
/// * RE-DERIVE `can_edit`. The server's 15-minute window is the authority, so an
///   edit is applied only when the server says it still owns the message.
/// * NEVER OPTIMISTICALLY APPEND A SEND. An optimistic bubble needs a
///   reconciliation path for three failure modes (network drop, block, archived
///   team), and the retry-id machinery already needed for idempotency makes the
///   honest version simpler. The composer clears on success, not on tap.
class ConversationNotifier extends AsyncNotifier<ConversationThread> {
  /// Held on the instance because Riverpod's family builder hands the argument
  /// to the *constructor*, not to `build` — so the id is available to every
  /// method below, not just during initialization.
  final String conversationId;

  StreamSubscription<MessagePage>? _subscription;

  ConversationNotifier(this.conversationId);

  ChatRepository get repo => ref.read(chatRepositoryProvider);
  ChatTransport get transport => ref.read(chatTransportProvider);

  @override
  Future<ConversationThread> build() async {
    final transport = ref.read(chatTransportProvider);
    final first = await repo.messages(
      conversationId,
      limit: kInitialHistoryPage,
    );
    // Subscribe AFTER the first page so a poll cannot land between the fetch and
    // the subscription and be dropped.
    _subscription = transport.watch(conversationId).listen(_onPoll);
    ref.onDispose(() => _subscription?.cancel());
    return _threadFrom(first);
  }

  ConversationThread _threadFrom(MessagePage page) => ConversationThread(
    // Reversed: the wire is newest-first, the list is oldest-first.
    messages: page.items.reversed.toList(),
    oldestCursor: page.nextBeforeSeq,
    hasMoreHistory: page.hasMore,
  );

  /// A poll (or an explicit refresh) landed. Merge rather than replace: the rider
  /// may have scrolled into older history that a newest-N page does not cover,
  /// and replacing would throw that away.
  void _onPoll(MessagePage page) {
    final current = state.value;
    if (current == null) return;
    state = AsyncData(
      current.copyWith(
        messages: _merge(current.messages, page.items.reversed.toList()),
      ),
    );
  }

  /// Re-read the newest page, e.g. pull-to-refresh.
  Future<void> refresh() async {
    try {
      final page = await repo.messages(
        conversationId,
        limit: kInitialHistoryPage,
      );
      final current = state.value;
      state = AsyncData(
        current == null
            ? _threadFrom(page)
            : current.copyWith(
                messages: _merge(
                  current.messages,
                  page.items.reversed.toList(),
                ),
              ),
      );
    } on Object catch (e, st) {
      // Keep whatever is on screen. Losing the conversation because a refresh
      // failed is worse than showing slightly stale text.
      if (state.value != null) return;
      state = AsyncError(e, st);
    }
  }

  /// Page backwards through history using the cursor.
  ///
  /// No-op while a page is in flight or at the start, so a scroll notification
  /// firing several times cannot queue several identical requests.
  Future<void> loadOlder() async {
    final current = state.value;
    if (current == null || current.loadingOlder || !current.hasMoreHistory) {
      return;
    }
    state = AsyncData(current.copyWith(loadingOlder: true));
    try {
      final page = await repo.messages(
        conversationId,
        beforeSeq: current.oldestCursor,
        limit: kOlderPageSize,
      );
      final latest = state.value ?? current;
      state = AsyncData(
        latest.copyWith(
          messages: _merge(page.items.reversed.toList(), latest.messages),
          oldestCursor: page.nextBeforeSeq,
          clearCursor: page.nextBeforeSeq == null,
          hasMoreHistory: page.hasMore,
          loadingOlder: false,
        ),
      );
    } on Object catch (e, st) {
      state = AsyncError(e, st);
    }
  }

  /// Send [body], reusing [clientMessageId] across retries.
  ///
  /// The id is the caller's, not generated here, because a retry of the SAME
  /// send must carry it. Generate one per attempt with
  /// [newClientMessageId] and keep it until the call resolves.
  Future<ChatMessage?> send(String body, String clientMessageId) async {
    final current = state.value;
    if (current == null || current.sending) return null;
    state = AsyncData(current.copyWith(sending: true));
    try {
      final result = await repo.send(
        conversationId,
        body,
        clientMessageId: clientMessageId,
      );
      final latest = state.value;
      if (latest != null) {
        state = AsyncData(
          latest.copyWith(
            messages: _merge(latest.messages, [result.message]),
            sending: false,
          ),
        );
      }
      invalidateInbox(ref);
      await markRead();
      return result.message;
    } on Object {
      // Clear the busy flag so the composer is usable again; the caller decides
      // what the rider is told. The thread keeps whatever it had.
      final latest = state.value;
      if (latest != null) state = AsyncData(latest.copyWith(sending: false));
      rethrow;
    }
  }

  /// Replace a message's body, using the server's answer.
  ///
  /// Not optimistic: `can_edit` is a server decision with a wall-clock boundary,
  /// and a local optimistic edit would keep showing an edit the server may
  /// refuse a second later.
  Future<ChatMessage?> edit(String messageId, String body) async {
    final updated = await repo.edit(messageId, body);
    _replace(updated);
    return updated;
  }

  /// Soft-delete. The server returns the placeholder row, so the UI renders what
  /// the server now believes rather than optimistically blanking the bubble.
  Future<ChatMessage?> delete(String messageId) async {
    final updated = await repo.delete(messageId);
    _replace(updated);
    return updated;
  }

  void _replace(ChatMessage updated) {
    final current = state.value;
    if (current == null) return;
    state = AsyncData(
      current.copyWith(messages: _merge(current.messages, [updated])),
    );
    invalidateInbox(ref);
  }

  /// Advance the read high-water mark to the newest message.
  ///
  /// Failure is ignored on purpose. An unread badge that lags is a cosmetic
  /// problem; interrupting a rider reading a conversation to report that their
  /// read receipt failed is not worth it, and the server clamps the value anyway.
  Future<void> markRead() async {
    final current = state.value;
    final seq = current?.newestSeq ?? 0;
    if (seq == 0) return;
    try {
      await repo.markRead(conversationId, seq);
      invalidateInbox(ref);
    } on Object {
      // Swallowed: see above.
    }
  }
}

final conversationThreadProvider = AsyncNotifierProvider.autoDispose
    .family<ConversationNotifier, ConversationThread, String>(
      ConversationNotifier.new,
    );

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/// Server-confirmed chat mutations with duplicate-submission protection.
///
/// Same two rules as the social and team action notifiers: an in-flight key
/// short-circuits, and nothing is applied before the server has answered.
class ChatActions extends Notifier<Set<String>> {
  @override
  Set<String> build() => const <String>{};

  ChatRepository get repo => ref.read(chatRepositoryProvider);

  bool isBusy(String key) => state.contains(key);

  static String conversationKey(String action, String id) => '$action:$id';

  /// Open (or re-open) a DM, then hand the conversation back for navigation.
  ///
  /// Errors propagate so the caller can explain the refusal — and the refusal
  /// matters: a blocked or unknown target both answer 404, so the rider is told
  /// "not available" rather than being told which.
  Future<Conversation> openDirect(String targetUserId) async {
    final conversation = await repo.openDirect(targetUserId);
    invalidateInbox(ref);
    return conversation;
  }

  /// The team's single channel, created on first open.
  Future<Conversation> openTeamConversation(String teamId) async {
    final conversation = await repo.teamConversation(teamId);
    invalidateInbox(ref);
    return conversation;
  }
}

final chatActionsProvider = NotifierProvider<ChatActions, Set<String>>(
  ChatActions.new,
);
