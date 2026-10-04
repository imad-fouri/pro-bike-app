/// Phase 8.4 notification domain models (ADR-15).
///
/// Every enum value is the literal the backend returns or accepts
/// (`backend/app/schemas/notifications.py`). The wire value IS the domain value:
/// nothing is renamed, mapped, or invented locally, so the UI can never display
/// a state the server did not send.
///
/// [AppNotification] carries a localization KEY plus substitution params rather
/// than rendered copy. That is what lets the server stay language-neutral: a
/// server-rendered string can only ever be correct in one of EN/FR/AR. The key is
/// a storage contract — renaming one would break rows already in a rider's
/// history — so `type` and its key must stay stable forever.
library;

/// `notifications.type`.
enum NotificationType {
  friendRequest('friend_request'),
  friendRequestAccepted('friend_request_accepted'),
  teamInvitation('team_invitation'),
  teamJoinRequest('team_join_request'),
  teamMemberRemoved('team_member_removed'),
  teamArchived('team_archived'),
  chatMessage('chat_message'),
  chatMessageTeam('chat_message_team'),
  system('system');

  final String wire;
  const NotificationType(this.wire);

  static NotificationType parse(
    String? raw,
  ) => NotificationType.values.firstWhere(
    (e) => e.wire == raw,
    // An unrecognised type degrades to `system` rather than throwing: a newer
    // server must not be able to crash an older client by adding a type.
    orElse: () => NotificationType.system,
  );

  /// Where tapping this notification navigates to.
  ///
  /// Declared here rather than read off the server's `deep_link` so the client
  /// can validate a route against an allowlist. A link is navigation intent, not
  /// proof of access — the destination re-authorizes regardless.
  bool get hasDeepLink => switch (this) {
    NotificationType.teamArchived => false,
    _ => true,
  };

  /// Whether an unread count of this type should render as a dot rather than a
  /// number. Team-wide events are low-value per item.
  bool get isBulk => switch (this) {
    NotificationType.teamArchived || NotificationType.chatMessageTeam => true,
    _ => false,
  };
}

/// `push_devices.platform`.
enum PushPlatform {
  android('android'),
  ios('ios');

  final String wire;
  const PushPlatform(this.wire);

  static PushPlatform parse(String? raw) => PushPlatform.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => PushPlatform.android,
  );
}

/// `push_devices.provider`.
///
/// Both values are FUTURE in Phase 8.4: the seam exists and a registration can be
/// stored, but no real provider is contacted. The enum records which transport
/// will eventually issue the token.
enum PushProvider {
  fcm('fcm'),
  apns('apns');

  final String wire;
  const PushProvider(this.wire);

  static PushProvider parse(String? raw) => PushProvider.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => PushProvider.fcm,
  );
}

/// One registered push device.
///
/// There is deliberately **no token field**. The API never returns one, so a
/// model that could hold it would be a model that could leak it — and the only
/// reliable way to keep a credential out of a response is for the type that
/// models the response to have nowhere to put it.
class PushDevice {
  final String id;
  final String deviceId;
  final PushPlatform platform;
  final PushProvider provider;
  final String? appVersion;
  final String? locale;
  final bool enabled;
  final DateTime? lastSeenAt;
  final DateTime? createdAt;

  const PushDevice({
    required this.id,
    required this.deviceId,
    required this.platform,
    required this.provider,
    this.appVersion,
    this.locale,
    required this.enabled,
    this.lastSeenAt,
    this.createdAt,
  });

  factory PushDevice.fromJson(Map<String, dynamic> json) => PushDevice(
    id: '${json['id']}',
    deviceId: '${json['device_id'] ?? ''}',
    platform: PushPlatform.parse(json['platform'] as String?),
    provider: PushProvider.parse(json['provider'] as String?),
    appVersion: json['app_version'] as String?,
    locale: json['locale'] as String?,
    enabled: json['enabled'] as bool? ?? false,
    lastSeenAt: DateTime.tryParse('${json['last_seen_at']}'),
    createdAt: DateTime.tryParse('${json['created_at']}'),
  );

  PushDevice copyWith({bool? enabled}) => PushDevice(
    id: id,
    deviceId: deviceId,
    platform: platform,
    provider: provider,
    appVersion: appVersion,
    locale: locale,
    enabled: enabled ?? this.enabled,
    lastSeenAt: lastSeenAt,
    createdAt: createdAt,
  );
}

/// One notification, rendered from a localization key.
class AppNotification {
  final String id;
  final NotificationType type;
  final String? entityType;
  final String? entityId;

  /// The server's key. Prefixed with `notifications.type.` by convention; the
  /// exact string is stored rather than derived so a server-side key change is
  /// visible to the client instead of silently mismatched.
  final String l10nKey;

  /// Substitution arguments — a display name, a team name. Never message text.
  final Map<String, String> params;

  /// Navigation intent. Never proof of access.
  final String? deepLink;

  final bool isRead;
  final bool isUnread;
  final DateTime? createdAt;
  final DateTime? readAt;
  final String? actorUserId;
  final String? actorUsername;
  final String? actorDisplayName;
  final String? entityName;

  const AppNotification({
    required this.id,
    required this.type,
    this.entityType,
    this.entityId,
    required this.l10nKey,
    required this.params,
    this.deepLink,
    required this.isRead,
    required this.isUnread,
    this.createdAt,
    this.readAt,
    this.actorUserId,
    this.actorUsername,
    this.actorDisplayName,
    this.entityName,
  });

  factory AppNotification.fromJson(Map<String, dynamic> json) {
    final rawParams = json['params'];
    return AppNotification(
      id: '${json['id']}',
      type: NotificationType.parse(json['type'] as String?),
      entityType: json['entity_type'] as String?,
      entityId: json['entity_id'] as String?,
      l10nKey: '${json['l10n_key'] ?? ''}',
      params: _stringParams(rawParams),
      deepLink: json['deep_link'] as String?,
      isRead: json['is_read'] as bool? ?? false,
      isUnread: json['is_unread'] as bool? ?? false,
      createdAt: DateTime.tryParse('${json['created_at']}'),
      readAt: DateTime.tryParse('${json['read_at']}'),
      actorUserId: json['actor_user_id'] as String?,
      actorUsername: json['actor_username'] as String?,
      actorDisplayName: json['actor_display_name'] as String?,
      entityName: json['entity_name'] as String?,
    );
  }

  /// Coerce every param to a string.
  ///
  /// The server sends JSONB, so a value could arrive as a number or a bool.
  /// Interpolating a non-string into a localized sentence is how a French or
  /// Arabic message ends up with a stray `.0`, so everything is stringified once,
  /// here, rather than at each call site.
  static Map<String, String> _stringParams(Object? raw) {
    if (raw is! Map) return const {};
    final out = <String, String>{};
    raw.forEach((key, value) {
      if (value is String && value.isNotEmpty) out['$key'] = value;
    });
    // Only strings are kept: a null or numeric param would render as the literal
    // text "null" or "3.0" in the rider's sentence.
    return out;
  }

  AppNotification markRead() => AppNotification(
    id: id,
    type: type,
    entityType: entityType,
    entityId: entityId,
    l10nKey: l10nKey,
    params: params,
    deepLink: deepLink,
    isRead: true,
    isUnread: false,
    createdAt: createdAt,
    readAt: readAt ?? DateTime.now().toUtc(),
    actorUserId: actorUserId,
    actorUsername: actorUsername,
    actorDisplayName: actorDisplayName,
    entityName: entityName,
  );

  /// Best available name for the actor, for a subtitle or accessibility label.
  String? get bestActor => actorDisplayName ?? actorUsername;
}

/// One page of the notification center.
///
/// Offset-paged to match the project standard: a notification center is bounded
/// and read deliberately, so it does not shift under the reader the way an
/// append-only message history does.
class NotificationPage {
  final List<AppNotification> items;
  final int total;
  final int page;
  final int pageSize;

  const NotificationPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  factory NotificationPage.fromJson(Map<String, dynamic> json) =>
      NotificationPage(
        items: (json['items'] as List? ?? const [])
            .map((e) => AppNotification.fromJson(e as Map<String, dynamic>))
            .toList(),
        total: (json['total'] as num? ?? 0).toInt(),
        page: (json['page'] as num? ?? 1).toInt(),
        pageSize: (json['page_size'] as num? ?? 20).toInt(),
      );
}
