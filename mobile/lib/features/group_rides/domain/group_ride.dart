/// Phase 9 group-ride domain models (ADR-16).
///
/// Every enum value is the literal the backend returns or accepts
/// (`backend/app/models/group_ride.py`, `backend/app/schemas/group_ride.py`).
/// The wire value IS the domain value: nothing is renamed, mapped, or invented
/// locally, so the UI can never display a state the server did not send.
///
/// Nothing in this file models GPS history. A [RiderLocation] is a *moment*:
/// the server holds positions in Redis with a TTL and never persists them, and
/// [RiderLocation.ageSeconds] exists precisely so a stale dot is recognisable as
/// stale rather than presented as live (ADR-16 §6).
///
/// Authority is server-authoritative. [GroupRide.viewer] is computed per request
/// by the backend and is what buttons are derived from; the client never derives
/// "am I the organizer" from a cached title or a locally remembered id
/// (ADR-16 §3).
library;

/// `group_rides.status`.
enum GroupRideStatus {
  open('open'),
  started('started'),
  completed('completed'),
  cancelled('cancelled');

  final String wire;
  const GroupRideStatus(this.wire);

  static GroupRideStatus parse(String? raw) => GroupRideStatus.values
      .firstWhere((e) => e.wire == raw, orElse: () => GroupRideStatus.open);

  String get labelKey => 'groupRide.status.$wire';

  /// Only `open` accepts new participants. The roster freezes at `started`
  /// (ADR-16 §3): you can always take yourself out, but nobody can be added late.
  bool get acceptsInvites => this == GroupRideStatus.open;

  /// A terminal state can never change again — there is no reversal.
  bool get isTerminal =>
      this == GroupRideStatus.completed || this == GroupRideStatus.cancelled;

  /// Sharing a live position is only meaningful while the ride is actually
  /// running. Offering it on an open or finished ride would be offering a
  /// feature the server will refuse.
  bool get allowsLiveLocation => this == GroupRideStatus.started;
}

/// `group_ride_participants.role`. Exactly one organizer; no admin, no
/// co-organizer (ADR-16 §3).
enum GroupRideRole {
  organizer('organizer'),
  participant('participant');

  final String wire;
  const GroupRideRole(this.wire);

  static GroupRideRole parse(String? raw) => GroupRideRole.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => GroupRideRole.participant,
  );

  String get labelKey => 'groupRide.role.$wire';
}

/// `group_ride_participants.status`.
///
/// Five distinct facts, not three: a rider who `declined` was never in, one who
/// `left` exercised consent, and one who was `removed` was acted upon.
enum GroupRideParticipantStatus {
  invited('invited'),
  joined('joined'),
  declined('declined'),
  left('left'),
  removed('removed');

  final String wire;
  const GroupRideParticipantStatus(this.wire);

  static GroupRideParticipantStatus parse(String? raw) =>
      GroupRideParticipantStatus.values.firstWhere(
        (e) => e.wire == raw,
        orElse: () => GroupRideParticipantStatus.invited,
      );

  String get labelKey => 'groupRide.participant.$wire';

  /// The ONLY status that grants ride, chat, or location visibility
  /// (ADR-16 §2).
  bool get isOnRide => this == GroupRideParticipantStatus.joined;

  /// Waiting for an answer. Only this state is an invitation.
  bool get isPending => this == GroupRideParticipantStatus.invited;
}

/// What THIS viewer may do on a ride, straight from `viewer` in the payload.
///
/// Both flags are the server's answer, refreshed on every read. They exist so a
/// stale screen cannot offer an action that would be refused — they are never a
/// substitute for the server's own check.
class GroupRideViewer {
  final bool isOrganizer;
  final bool isJoined;

  const GroupRideViewer({this.isOrganizer = false, this.isJoined = false});

  static const none = GroupRideViewer();

  /// Parse the `viewer` object. A missing or null `viewer` (an unauthenticated
  /// projection, if one ever exists) degrades to "no authority" rather than
  /// throwing.
  factory GroupRideViewer.fromJson(Map<String, dynamic>? json) {
    if (json == null) return none;
    return GroupRideViewer(
      isOrganizer: json['is_organizer'] == true,
      isJoined: json['is_joined'] == true,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is GroupRideViewer &&
      other.isOrganizer == isOrganizer &&
      other.isJoined == isJoined;

  @override
  int get hashCode => Object.hash(isOrganizer, isJoined);

  @override
  String toString() =>
      'GroupRideViewer(organizer: $isOrganizer, joined: $isJoined)';
}

/// One roster row, as shown to anyone on the ride.
///
/// Carries only the PUBLIC social projection (username, display name, avatar) —
/// never an email or any other account-private column, so a roster cannot become
/// a way to read one (ADR-16 §4).
class GroupRideParticipant {
  final String userId;
  final GroupRideRole role;
  final GroupRideParticipantStatus status;
  final String? invitedByUserId;
  final String? message;
  final DateTime? respondedAt;
  final DateTime createdAt;
  final String? username;
  final String? displayName;
  final String? avatarUrl;

  const GroupRideParticipant({
    required this.userId,
    required this.role,
    required this.status,
    this.invitedByUserId,
    this.message,
    this.respondedAt,
    required this.createdAt,
    this.username,
    this.displayName,
    this.avatarUrl,
  });

  factory GroupRideParticipant.fromJson(Map<String, dynamic> json) =>
      GroupRideParticipant(
        userId: '${json['user_id'] ?? ''}',
        role: GroupRideRole.parse(json['role'] as String?),
        status: GroupRideParticipantStatus.parse(json['status'] as String?),
        invitedByUserId: json['invited_by_user_id'] as String?,
        message: json['message'] as String?,
        respondedAt: _date(json['responded_at']),
        createdAt: _date(json['created_at']) ?? DateTime(0),
        username: json['username'] as String?,
        displayName: json['display_name'] as String?,
        avatarUrl: json['avatar_url'] as String?,
      );

  bool get isOrganizer => role == GroupRideRole.organizer;

  /// Best available display label. Falls back to the user id rather than an empty
  /// string so a row is never anonymous.
  String get label {
    if (displayName != null && displayName!.isNotEmpty) return displayName!;
    if (username != null && username!.isNotEmpty) return username!;
    return userId;
  }
}

/// A ride as the viewer sees it.
class GroupRide {
  final String id;
  final String organizerUserId;
  final String title;
  final String? description;
  final GroupRideStatus status;
  final DateTime? startsAt;
  final String? meetingPoint;
  final String? routeId;
  final int? routeVersion;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime? startedAt;
  final DateTime? completedAt;
  final DateTime? cancelledAt;
  final int participantCount;
  final List<GroupRideParticipant> roster;
  final GroupRideViewer viewer;

  const GroupRide({
    required this.id,
    required this.organizerUserId,
    required this.title,
    this.description,
    required this.status,
    this.startsAt,
    this.meetingPoint,
    this.routeId,
    this.routeVersion,
    required this.createdAt,
    required this.updatedAt,
    this.startedAt,
    this.completedAt,
    this.cancelledAt,
    required this.participantCount,
    required this.roster,
    this.viewer = GroupRideViewer.none,
  });

  factory GroupRide.fromJson(Map<String, dynamic> json) => GroupRide(
    id: '${json['id'] ?? ''}',
    organizerUserId: '${json['organizer_user_id'] ?? ''}',
    title: '${json['title'] ?? ''}',
    description: json['description'] as String?,
    status: GroupRideStatus.parse(json['status'] as String?),
    startsAt: _date(json['starts_at']),
    meetingPoint: json['meeting_point'] as String?,
    routeId: json['route_id'] as String?,
    routeVersion: (json['route_version'] as num?)?.toInt(),
    createdAt: _date(json['created_at']) ?? DateTime(0),
    updatedAt: _date(json['updated_at']) ?? DateTime(0),
    startedAt: _date(json['started_at']),
    completedAt: _date(json['completed_at']),
    cancelledAt: _date(json['cancelled_at']),
    participantCount: (json['participant_count'] as num?)?.toInt() ?? 0,
    roster: [
      for (final r in (json['roster'] as List<dynamic>? ?? const []))
        GroupRideParticipant.fromJson(r as Map<String, dynamic>),
    ],
    viewer: GroupRideViewer.fromJson(json['viewer'] as Map<String, dynamic>?),
  );

  /// This viewer's own roster row, or null when they hold none.
  ///
  /// [userId] must come from the session, not from a remembered id: the roster
  /// does not label which row is the viewer's, and guessing would put the wrong
  /// rider's row on screen.
  GroupRideParticipant? participantById(String userId) {
    for (final p in roster) {
      if (p.userId == userId) return p;
    }
    return null;
  }

  bool get hasRoute => routeId != null;

  /// `started_at` is the fact; the ride is running whenever it is set and the
  /// status agrees.
  bool get isRunning => status == GroupRideStatus.started;

  /// Roster rows waiting for an answer.
  List<GroupRideParticipant> get pendingInvites =>
      roster.where((p) => p.status.isPending).toList(growable: false);

  /// Roster rows that are on the ride right now.
  List<GroupRideParticipant> get onRoster =>
      roster.where((p) => p.status.isOnRide).toList(growable: false);

  /// The viewer's own roster row, or null when they hold none.
  ///
  /// [GroupRideViewer] deliberately carries only two booleans — `is_organizer` and
  /// `is_joined` — because the server sends only those two. That is enough to
  /// decide authority but NOT enough to tell "invited, awaiting my answer" apart
  /// from "no roster row at all", which matters: the first must offer Accept and
  /// Decline, the second must offer nothing.
  ///
  /// Rather than have the server echo a third flag, the answer is read from the
  /// roster, which is the authoritative list of rows and is already in the same
  /// payload. [myUserId] is nullable because the signed-in id arrives
  /// asynchronously; a null means "not known yet", and every caller treats that as
  /// "offer nothing" rather than guessing.
  GroupRideParticipant? viewerRow(String? myUserId) =>
      myUserId == null ? null : participantById(myUserId);

  /// The viewer's roster status, or null when they hold no row or their id is not
  /// known yet.
  GroupRideParticipantStatus? viewerStatus(String? myUserId) =>
      viewerRow(myUserId)?.status;
}

/// `GET /group-rides` and `GET /group-rides/{id}` both return `{items: [...]}`.
class GroupRideList {
  final List<GroupRide> items;

  const GroupRideList(this.items);

  factory GroupRideList.fromJson(Map<String, dynamic> json) => GroupRideList([
    for (final r in (json['items'] as List<dynamic>? ?? const []))
      GroupRide.fromJson(r as Map<String, dynamic>),
  ]);
}

/// A pending invitation addressed to the viewer.
///
/// Returned by `GET /group-rides/invitations` — the ONE read a rider with no
/// roster row on the ride is allowed to make, because that row IS the
/// invitation (ADR-16 §7).
class GroupRideInvitation {
  final String participantId;
  final String groupRideId;
  final String title;
  final DateTime? startsAt;
  final String? meetingPoint;
  final String? message;
  final DateTime createdAt;
  final String organizerUserId;
  final String? organizerUsername;
  final String? organizerDisplayName;
  final String? organizerAvatarUrl;

  const GroupRideInvitation({
    required this.participantId,
    required this.groupRideId,
    required this.title,
    this.startsAt,
    this.meetingPoint,
    this.message,
    required this.createdAt,
    required this.organizerUserId,
    this.organizerUsername,
    this.organizerDisplayName,
    this.organizerAvatarUrl,
  });

  factory GroupRideInvitation.fromJson(Map<String, dynamic> json) =>
      GroupRideInvitation(
        participantId: '${json['participant_id'] ?? ''}',
        groupRideId: '${json['group_ride_id'] ?? ''}',
        title: '${json['title'] ?? ''}',
        startsAt: _date(json['starts_at']),
        meetingPoint: json['meeting_point'] as String?,
        message: json['message'] as String?,
        createdAt: _date(json['created_at']) ?? DateTime(0),
        organizerUserId: '${json['organizer_user_id'] ?? ''}',
        organizerUsername: json['username'] as String?,
        organizerDisplayName: json['display_name'] as String?,
        organizerAvatarUrl: json['avatar_url'] as String?,
      );

  String get organizerLabel {
    if (organizerDisplayName != null && organizerDisplayName!.isNotEmpty) {
      return organizerDisplayName!;
    }
    if (organizerUsername != null && organizerUsername!.isNotEmpty) {
      return organizerUsername!;
    }
    return organizerUserId;
  }
}

class GroupRideInvitationList {
  final List<GroupRideInvitation> items;

  const GroupRideInvitationList(this.items);

  factory GroupRideInvitationList.fromJson(Map<String, dynamic> json) =>
      GroupRideInvitationList([
        for (final r in (json['items'] as List<dynamic>? ?? const []))
          GroupRideInvitation.fromJson(r as Map<String, dynamic>),
      ]);
}

/// Per-target outcomes of a batch invite.
///
/// The batch reports partial progress rather than failing as a whole: an
/// organizer inviting ten riders should learn that nine were invited and one has
/// blocked them, not get one opaque error.
class InviteBatchResult {
  final List<String> invited;
  final List<InviteRejection> rejected;

  const InviteBatchResult({required this.invited, required this.rejected});

  factory InviteBatchResult.fromJson(Map<String, dynamic> json) =>
      InviteBatchResult(
        invited: [
          for (final u in (json['invited'] as List<dynamic>? ?? const [])) '$u',
        ],
        rejected: [
          for (final r in (json['rejected'] as List<dynamic>? ?? const []))
            InviteRejection.fromJson(r as Map<String, dynamic>),
        ],
      );

  bool get allAccepted => rejected.isEmpty;
}

/// Why one target of a batch invite was refused. Carries the server's `code`, not
/// rendered copy, so the UI maps it through localizations.
class InviteRejection {
  final String userId;
  final String code;

  const InviteRejection({required this.userId, required this.code});

  factory InviteRejection.fromJson(Map<String, dynamic> json) =>
      InviteRejection(
        userId: '${json['user_id'] ?? ''}',
        code: '${json['code'] ?? ''}',
      );
}

/// One visible rider's position, from `GET /group-rides/{id}/location`.
///
/// Carries [ageSeconds] so the client decides staleness itself rather than
/// trusting a server-rendered "last seen 2 minutes ago" string that goes stale in
/// the widget. [isSelf] lets a rider find their own dot without a second lookup.
class RiderLocation {
  final String userId;
  final String username;
  final String? displayName;
  final String? avatarUrl;
  final double latitude;
  final double longitude;
  final double? accuracyM;
  final int ageSeconds;
  final bool isSelf;

  const RiderLocation({
    required this.userId,
    required this.username,
    this.displayName,
    this.avatarUrl,
    required this.latitude,
    required this.longitude,
    this.accuracyM,
    required this.ageSeconds,
    required this.isSelf,
  });

  factory RiderLocation.fromJson(Map<String, dynamic> json) => RiderLocation(
    userId: '${json['user_id'] ?? ''}',
    username: '${json['username'] ?? ''}',
    displayName: json['display_name'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    latitude: (json['latitude'] as num?)?.toDouble() ?? 0,
    longitude: (json['longitude'] as num?)?.toDouble() ?? 0,
    accuracyM: (json['accuracy_m'] as num?)?.toDouble(),
    ageSeconds: (json['age_seconds'] as num?)?.toInt() ?? 0,
    isSelf: json['is_self'] == true,
  );

  String get label {
    if (displayName != null && displayName!.isNotEmpty) return displayName!;
    return username.isNotEmpty ? username : userId;
  }

  /// A dot whose accuracy is unknown must not be drawn as precisely as one that
  /// knows its own error radius. `is_imprecise` is a presentation decision, not a
  /// privacy control.
  bool get isPrecise => accuracyM != null && accuracyM! <= 50;
}

/// `GET /group-rides/{id}/location`.
///
/// The service returns 503 — never an empty list — when Redis is unreachable, so
/// "nobody is sharing" and "we could not ask" stay distinguishable. A 503 is
/// surfaced as an error state, not as zero riders.
class RideLocationSnapshot {
  final List<RiderLocation> riders;
  final int staleAfterSeconds;
  final int expiresInSeconds;

  const RideLocationSnapshot({
    required this.riders,
    required this.staleAfterSeconds,
    required this.expiresInSeconds,
  });

  factory RideLocationSnapshot.fromJson(Map<String, dynamic> json) =>
      RideLocationSnapshot(
        riders: [
          for (final r in (json['items'] as List<dynamic>? ?? const []))
            RiderLocation.fromJson(r as Map<String, dynamic>),
        ],
        staleAfterSeconds: (json['stale_after_seconds'] as num?)?.toInt() ?? 60,
        expiresInSeconds: (json['expires_in_seconds'] as num?)?.toInt() ?? 300,
      );

  /// This rider's own dot, when they are sharing.
  RiderLocation? get self {
    for (final r in riders) {
      if (r.isSelf) return r;
    }
    return null;
  }
}

/// Parse a timestamp defensively.
///
/// A malformed timestamp must not crash a list: it degrades to null, and the
/// caller renders what it can. Silently substituting `DateTime.now()` would be
/// worse — it would invent a "just now" that never happened.
DateTime? _date(Object? raw) {
  if (raw == null) return null;
  if (raw is DateTime) return raw;
  return DateTime.tryParse('$raw');
}
