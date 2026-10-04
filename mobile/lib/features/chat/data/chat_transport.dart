import 'dart:async';

import '../domain/chat.dart';
import 'chat_repository.dart';

/// How new messages reach the UI.
///
/// The abstraction exists so that "polling every few seconds" is a decision
/// recorded in one file rather than smeared through the widget tree. Swapping in
/// WebSockets, SSE, or push later means writing a new implementation of this
/// interface — not rewriting the conversation screen.
///
/// The contract is intentionally minimal: a stream of *snapshots*, not of
/// deltas. A delta protocol would need ordering and gap repair; the server
/// already exposes "newest N messages" as one call, and re-reading a page is
/// both cheaper to implement and impossible to get out of order.
abstract class ChatTransport {
  /// Latest message snapshot for [conversationId], newest first.
  ///
  /// Re-emits on every successful poll. May re-emit identical data — consumers
  /// must be idempotent, because a poll that finds nothing new is cheaper than
  /// tracking what the previous poll contained.
  Stream<MessagePage> watch(String conversationId);

  /// Emit immediately rather than waiting for the next interval.
  ///
  /// Called when the rider returns to the foreground: after a backgrounded app
  /// the next scheduled poll could be up to [pollInterval] away, which reads as
  /// the app being broken.
  Future<void> refresh(String conversationId) async {}

  /// Stop all subscriptions. Must be idempotent.
  Future<void> dispose();
}

/// The Phase 8.3 transport: HTTP polling.
///
/// Deliberately simple, and honest about its cost. A poll is a cheap indexed
/// read of the newest page, so a modest interval costs little; the interval is
/// configurable because battery life on a phone is a real constraint and this
/// is the one thing standing between a conversation screen and a flat battery.
///
/// Errors are NOT pushed into the stream. A failed poll ends that cycle and the
/// next one retries, so a rider in a lift or a tunnel sees a stall, not an error
/// dialog every five seconds. The last good snapshot stays on screen — losing the
/// conversation because the network blinked would be worse than showing stale
/// text.
class PollingChatTransport implements ChatTransport {
  final ChatRepository repository;
  final Duration pollInterval;

  /// Page size per poll. One page is enough to catch anything new between polls
  /// unless the rider was away for the entire interval, and the screen tops up
  /// from history for anything older.
  final int pageSize;

  bool _disposed = false;
  final Map<String, StreamController<MessagePage>> _controllers = {};
  final Map<String, Timer> _timers = {};

  PollingChatTransport({
    required this.repository,
    this.pollInterval = const Duration(seconds: 7),
    this.pageSize = 50,
  });

  @override
  Stream<MessagePage> watch(String conversationId) {
    if (_disposed) {
      return const Stream<MessagePage>.empty();
    }
    final existing = _controllers[conversationId];
    if (existing != null) return existing.stream;

    // Broadcast: the screen may listen more than once (a rebuild re-subscribes),
    // and every listener needs the same polls, not its own timer.
    final controller = StreamController<MessagePage>.broadcast();
    _controllers[conversationId] = controller;

    unawaited(_poll(conversationId));
    _timers[conversationId] = Timer.periodic(pollInterval, (_) {
      unawaited(_poll(conversationId));
    });

    return controller.stream;
  }

  @override
  Future<void> refresh(String conversationId) async {
    if (_disposed) return;
    await _poll(conversationId);
  }

  Future<void> _poll(String conversationId) async {
    if (_disposed) return;
    final controller = _controllers[conversationId];
    if (controller == null || controller.isClosed) return;
    try {
      final page = await repository.messages(conversationId, limit: pageSize);
      // A disposed controller between the await and here must not throw into
      // the caller: the rider backing out mid-poll is normal, not an error.
      if (!controller.isClosed) controller.add(page);
    } on Object {
      // Swallowed on purpose — see the class docstring.
    }
  }

  @override
  Future<void> dispose() async {
    _disposed = true;
    for (final timer in _timers.values) {
      timer.cancel();
    }
    _timers.clear();
    for (final controller in _controllers.values) {
      if (!controller.isClosed) await controller.close();
    }
    _controllers.clear();
  }
}
