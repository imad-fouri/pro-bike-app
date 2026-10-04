/// Chat-specific input validation, kept out of the widgets so the rules are
/// testable without a pump (mirrors `social_validators.dart` / `team_validators.dart`).
///
/// These rules deliberately mirror the server's, and the server is still the
/// authority: a client that relaxed them would only produce a 422. The reason to
/// keep them is that a message composer should not be able to send a request the
/// server is certain to reject.
library;

import 'dart:math';

/// Server limit on a single message body (`MessageCreate.body`).
const int kMessageBodyMaxLength = 4000;

/// Server ceiling on one page of history (`MAX_HISTORY`).
const int kMessagePageMax = 100;

/// Default page size for a first history load.
///
/// Generous enough that a rider opening a conversation almost never sees the
/// "load earlier messages" affordance on first paint, which is the moment where
/// a spinner would read as jank rather than as work.
const int kMessagePageDefault = 50;

/// Messages fetched on open and on pull-to-refresh.
const int kInitialHistoryPage = kMessagePageDefault;

/// Messages fetched per "load earlier messages" step. Smaller than the initial
/// page so scrolling back is responsive rather than pulling a full screen of
/// text the rider may never read.
const int kOlderPageSize = 30;

/// Why a body was rejected, so the composer can show a message and not just a
/// red border.
enum MessageBodyProblem {
  /// Empty or whitespace-only. Refused rather than trimmed to an empty message.
  empty,

  /// Longer than [kMessageBodyMaxLength].
  tooLong,
}

/// Returns the problem with [body], or null when it is sendable.
MessageBodyProblem? validateMessageBody(String body) {
  if (body.trim().isEmpty) return MessageBodyProblem.empty;
  if (body.length > kMessageBodyMaxLength) return MessageBodyProblem.tooLong;
  return null;
}

/// Whether the composer should offer a send button at all.
bool canSendMessage(String body) => validateMessageBody(body) == null;

/// Characters remaining before the body is rejected.
///
/// Negative once the rider is over the limit — the counter is a progress bar,
/// not a clamp, so silently hiding the overflow would leave them typing into a
/// message that cannot be sent.
int remainingMessageChars(String body) => kMessageBodyMaxLength - body.length;

/// A fresh client message id.
///
/// Generated once per send attempt and reused across retries of that same
/// attempt: the server's idempotency key is (conversation, sender, this value),
/// so a retry carrying a NEW id would store a second message. This is the whole
/// reason `client_message_id` exists (ADR-14 §7).
///
/// Returns a lowercase v4 UUID string without pulling in the `uuid` package.
String newClientMessageId() {
  final rng = Random.secure();
  final bytes = List<int>.generate(16, (_) => rng.nextInt(256));
  // Version 4, variant 1 — so a generated id can never collide with an id the
  // backend or another client produced under a different scheme.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}
