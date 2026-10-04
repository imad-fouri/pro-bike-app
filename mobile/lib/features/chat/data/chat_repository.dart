import '../../../core/network/api_client.dart';
import '../domain/chat.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// Contract source of truth is `backend/app/api/v1/chat.py`. Every call is
/// `auth: true`; the viewer comes from the access token, never from a body.
/// The only client-supplied identity is `clientMessageId`, which is an
/// idempotency key rather than an authorization input.
///
/// Two rules this class exists to keep:
///
/// * A send ALWAYS carries a caller-supplied `client_message_id`. The server
///   deduplicates on (conversation, sender, client id), so a retry after a
///   dropped response is safe — but only if the SAME id is reused. Generating
///   one here per call would defeat the whole mechanism, so the id is a required
///   parameter rather than an optional convenience.
/// * History is paged by `before_seq`, never by page number. See
///   [MessagePage].
class ChatRepository {
  final ApiClient api;
  static const prefix = '/api/v1/chat';

  const ChatRepository(this.api);

  String _query(Map<String, String> q) =>
      q.entries.map((e) => '${e.key}=${e.value}').join('&');

  // --- conversations ---------------------------------------------------------

  Future<ConversationPage> conversations({
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = _query({'page': '$page', 'page_size': '$pageSize'});
    final body = await api.get('$prefix/conversations?$q', auth: true);
    return ConversationPage.fromJson(body);
  }

  /// The team's single channel, created on first open.
  ///
  /// A non-member gets the same 404 as a missing team, so this call cannot be
  /// used to discover whether a private team exists.
  Future<Conversation> teamConversation(String teamId) async {
    final body = await api.get(
      '$prefix/teams/$teamId/conversation',
      auth: true,
    );
    return Conversation.fromJson(body);
  }

  /// Find-or-create the DM with [targetUserId]. Idempotent server-side.
  Future<Conversation> openDirect(String targetUserId) async {
    final body = await api.post(
      '$prefix/direct?target_user_id=$targetUserId',
      {},
      auth: true,
    );
    return Conversation.fromJson(body);
  }

  Future<Conversation> conversation(String conversationId) async {
    final body = await api.get(
      '$prefix/conversations/$conversationId',
      auth: true,
    );
    return Conversation.fromJson(body);
  }

  // --- messages --------------------------------------------------------------

  /// Newest-first history page.
  ///
  /// [beforeSeq] is the cursor: pass the previous page's `nextBeforeSeq` to walk
  /// backwards. Omitting it returns the newest page, which is also what the
  /// polling transport uses.
  Future<MessagePage> messages(
    String conversationId, {
    int? beforeSeq,
    int limit = 50,
  }) async {
    final q = _query({
      if (beforeSeq != null) 'before_seq': '$beforeSeq',
      'limit': '$limit',
    });
    final body = await api.get(
      '$prefix/conversations/$conversationId/messages?$q',
      auth: true,
    );
    return MessagePage.fromJson(body);
  }

  /// Send a message. [clientMessageId] must be reused across retries of the
  /// SAME send; a fresh id would store a second message.
  ///
  /// Returns [SendResult.duplicate] true when the server already had this
  /// message, which is how the UI reconciles an optimistic bubble instead of
  /// appending a duplicate.
  Future<SendResult> send(
    String conversationId,
    String body, {
    required String clientMessageId,
  }) async {
    final res = await api.post(
      '$prefix/conversations/$conversationId/messages',
      {'body': body, 'client_message_id': clientMessageId},
      auth: true,
    );
    return SendResult.fromJson(res);
  }

  /// Edit own message. Fails with `CHAT_EDIT_WINDOW_CLOSED` after 15 minutes —
  /// a server-side decision, deliberately not re-derived from the device clock.
  Future<ChatMessage> edit(String messageId, String body) async {
    final res = await api.patch('$prefix/messages/$messageId', {
      'body': body,
    }, auth: true);
    return ChatMessage.fromJson(res);
  }

  /// Soft-delete own message. The row survives; the body becomes `[deleted]`.
  Future<ChatMessage> delete(String messageId) async {
    final res = await api.delete('$prefix/messages/$messageId', auth: true);
    return ChatMessage.fromJson(res);
  }

  // --- read state ------------------------------------------------------------

  /// Advance the read high-water mark to [seq]. Never moves backwards.
  Future<int> markRead(String conversationId, int seq) async {
    final res = await api.post(
      '$prefix/conversations/$conversationId/read?seq=$seq',
      {},
      auth: true,
    );
    return (res['last_read_seq'] as num? ?? 0).toInt();
  }
}
