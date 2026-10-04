/// Phase 8.3 chat domain models (ADR-14).
///
/// Every enum value is the literal the backend returns or accepts
/// (backend/app/schemas/chat.py). The wire value IS the domain value: nothing
/// is renamed, mapped, or invented locally, so the UI can never display a state
/// the server did not send.
///
/// Nothing in this file models a location, a ride attachment, media, or an
/// account-private field. A chat surface built from these types therefore cannot
/// render any of them, which is the point: ADR-12 §3 and ADR-14 §2 forbid
/// treating location sharing as an implicit capability of "messaging".
library;

/// `conversations.kind`.
enum ConversationKind {
  team('team'),
  direct('direct');

  final String wire;
  const ConversationKind(this.wire);

  static ConversationKind parse(String? raw) => ConversationKind.values
      .firstWhere((e) => e.wire == raw, orElse: () => ConversationKind.direct);
}

/// `messages.message_type`. Only `text` and `system` exist in Phase 8.3; no
/// `location`, `ride` or media variant is modelled, so none can be rendered.
enum MessageType {
  text('text'),
  system('system');

  final String wire;
  const MessageType(this.wire);

  static MessageType parse(String? raw) => MessageType.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => MessageType.text,
  );
}

/// A conversation as the viewer sees it (`ConversationOut`).
///
/// Exactly one of [teamId] / [peerUserId] is set, matching the server's
/// `ck_conversations_kind_team` check. A client that trusted both would render
/// a hybrid row the server can never produce.
class Conversation {
  final String id;
  final ConversationKind kind;

  // Team channel.
  final String? teamId;
  final String? teamName;
  final String? teamHandle;

  // Direct message.
  final String? peerUserId;
  final String? peerUsername;
  final String? peerDisplayName;

  final String? lastMessageId;
  final int? lastMessageSeq;
  final String? lastMessagePreview;
  final DateTime? lastMessageAt;
  final int unreadCount;
  final int lastReadSeq;
  final DateTime? createdAt;

  const Conversation({
    required this.id,
    required this.kind,
    this.teamId,
    this.teamName,
    this.teamHandle,
    this.peerUserId,
    this.peerUsername,
    this.peerDisplayName,
    this.lastMessageId,
    this.lastMessageSeq,
    this.lastMessagePreview,
    this.lastMessageAt,
    this.unreadCount = 0,
    this.lastReadSeq = 0,
    this.createdAt,
  });

  factory Conversation.fromJson(Map<String, dynamic> json) => Conversation(
    id: '${json['id']}',
    kind: ConversationKind.parse(json['kind'] as String?),
    teamId: json['team_id'] as String?,
    teamName: json['team_name'] as String?,
    teamHandle: json['team_handle'] as String?,
    peerUserId: json['peer_user_id'] as String?,
    peerUsername: json['peer_username'] as String?,
    peerDisplayName: json['peer_display_name'] as String?,
    lastMessageId: json['last_message_id'] as String?,
    lastMessageSeq: (json['last_message_seq'] as num?)?.toInt(),
    lastMessagePreview: json['last_message_preview'] as String?,
    lastMessageAt: DateTime.tryParse('${json['last_message_at']}'),
    unreadCount: (json['unread_count'] as num? ?? 0).toInt(),
    lastReadSeq: (json['last_read_seq'] as num? ?? 0).toInt(),
    createdAt: DateTime.tryParse('${json['created_at']}'),
  );

  bool get isTeam => kind == ConversationKind.team;

  /// Best available human label. Callers pass a fallback for the "no name yet"
  /// case rather than rendering a blank row.
  String? get bestName => isTeam ? teamName : (peerDisplayName ?? peerUsername);

  String? get bestHandle => isTeam ? teamHandle : peerUsername;
}

/// One message (`MessageOut`).
///
/// [body] is already the `[deleted]` placeholder when [isDeleted] is true — the
/// original body never reaches the client, so there is nothing to leak here.
///
/// [canEdit] is server-authoritative. The 15-minute window is computed on the
/// server precisely so a client with a skewed device clock cannot offer an edit
/// the server would refuse, or hide one it would allow (ADR-14 §9).
class ChatMessage {
  final String id;
  final String conversationId;
  final int seq;
  final String senderUserId;
  final String? senderUsername;
  final String? senderDisplayName;
  final MessageType messageType;
  final String body;
  final bool isDeleted;
  final bool isEdited;
  final bool isMine;
  final bool canEdit;
  final DateTime? createdAt;
  final DateTime? editedAt;

  const ChatMessage({
    required this.id,
    required this.conversationId,
    required this.seq,
    required this.senderUserId,
    required this.senderUsername,
    required this.senderDisplayName,
    required this.messageType,
    required this.body,
    required this.isDeleted,
    required this.isEdited,
    required this.isMine,
    required this.canEdit,
    required this.createdAt,
    required this.editedAt,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
    id: '${json['id']}',
    conversationId: '${json['conversation_id']}',
    seq: (json['seq'] as num? ?? 0).toInt(),
    senderUserId: '${json['sender_user_id']}',
    senderUsername: json['sender_username'] as String?,
    senderDisplayName: json['sender_display_name'] as String?,
    messageType: MessageType.parse(json['message_type'] as String?),
    body: '${json['body'] ?? ''}',
    isDeleted: json['is_deleted'] as bool? ?? false,
    isEdited: json['is_edited'] as bool? ?? false,
    isMine: json['is_mine'] as bool? ?? false,
    canEdit: json['can_edit'] as bool? ?? false,
    createdAt: DateTime.tryParse('${json['created_at']}'),
    editedAt: DateTime.tryParse('${json['edited_at']}'),
  );

  String? get bestName => senderDisplayName ?? senderUsername;
}

/// One cursor page of message history (`MessagePage`).
///
/// A CURSOR page, not an offset page. [items] is newest-first, and
/// [nextBeforeSeq] is what to pass back as `before_seq` — the contract that
/// keeps a rider scrolling through history from seeing a row twice or skipping
/// one when new messages land (ADR-14 §8).
///
/// There is deliberately no `total`: counting an append-only table on every
/// page is the wrong trade, and the UI never needs the count.
class MessagePage {
  final List<ChatMessage> items;
  final bool hasMore;
  final int? nextBeforeSeq;

  const MessagePage({
    required this.items,
    required this.hasMore,
    required this.nextBeforeSeq,
  });

  factory MessagePage.fromJson(Map<String, dynamic> json) => MessagePage(
    items: (json['items'] as List? ?? const [])
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList(),
    hasMore: json['has_more'] as bool? ?? false,
    nextBeforeSeq: (json['next_before_seq'] as num?)?.toInt(),
  );
}

/// Result of a send: the message plus whether the server had already stored it.
///
/// [duplicate] true means a retry of a message that DID land. The UI uses it to
/// reconcile an optimistic bubble instead of appending a second one.
class SendResult {
  final ChatMessage message;
  final bool duplicate;

  const SendResult({required this.message, required this.duplicate});

  factory SendResult.fromJson(Map<String, dynamic> json) => SendResult(
    message: ChatMessage.fromJson(json['message'] as Map<String, dynamic>),
    duplicate: json['duplicate'] as bool? ?? false,
  );
}

/// One page of the inbox (`ConversationPage`). Offset-paged: a conversation
/// list is bounded, changes rarely, and the rider pages through it deliberately
/// rather than scrolling while it shifts.
class ConversationPage {
  final List<Conversation> items;
  final int total;
  final int page;
  final int pageSize;

  const ConversationPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  factory ConversationPage.fromJson(Map<String, dynamic> json) =>
      ConversationPage(
        items: (json['items'] as List? ?? const [])
            .map((e) => Conversation.fromJson(e as Map<String, dynamic>))
            .toList(),
        total: (json['total'] as num? ?? 0).toInt(),
        page: (json['page'] as num? ?? 1).toInt(),
        pageSize: (json['page_size'] as num? ?? 20).toInt(),
      );
}
