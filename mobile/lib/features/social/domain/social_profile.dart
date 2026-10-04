/// Phase 8.1 social domain models. Every enum value here is the literal the
/// backend returns or accepts (backend/app/schemas/social.py). Nothing is
/// renamed, mapped, or invented locally: the wire value IS the domain value,
/// so the UI can never display a state the server did not send.
///
/// No location, coordinates, tokens, or email exist in these types by
/// construction. A social profile is public identity, not telemetry
/// (ADR-12 §5).
library;

/// Server-authoritative relationship between the viewer and another rider.
/// Never derived locally (Phase 8.1 §5).
enum RelationshipState {
  self('SELF'),
  none('NONE'),
  outgoingPending('OUTGOING_PENDING'),
  incomingPending('INCOMING_PENDING'),
  friends('FRIENDS'),
  blocked('BLOCKED'),
  blockedByUser('BLOCKED_BY_USER');

  final String wire;
  const RelationshipState(this.wire);

  static RelationshipState parse(String? raw) => RelationshipState.values
      .firstWhere((e) => e.wire == raw, orElse: () => RelationshipState.none);

  /// L10n key for this state, rendered on the profile and friend surfaces.
  String get labelKey => 'social.state.$wire';

  /// Whether the viewer may act on the relationship at all. A block in either
  /// direction is a wall: no action shown that could route around it.
  bool get allowsSocialAction =>
      this != blocked && this != blockedByUser && this != self;
}

/// `social_profiles.profile_visibility`.
enum ProfileVisibility {
  public_('public'),
  friends('friends'),
  private_('private');

  final String wire;
  const ProfileVisibility(this.wire);

  static ProfileVisibility parse(String? raw) =>
      ProfileVisibility.values.firstWhere(
        (e) => e.wire == raw,
        orElse: () => ProfileVisibility.public_,
      );

  String get labelKey => 'social.visibility.$wire';
}

/// `social_profiles.allow_friend_requests`.
enum FriendRequestsPolicy {
  everyone('everyone'),
  nobody('nobody');

  final String wire;
  const FriendRequestsPolicy(this.wire);

  static FriendRequestsPolicy parse(String? raw) =>
      FriendRequestsPolicy.values.firstWhere(
        (e) => e.wire == raw,
        orElse: () => FriendRequestsPolicy.everyone,
      );

  String get labelKey => 'social.requests.$wire';
}

/// `social_profiles.search_visibility`.
enum SearchVisibility {
  discoverable('discoverable'),
  hidden('hidden');

  final String wire;
  const SearchVisibility(this.wire);

  static SearchVisibility parse(String? raw) =>
      SearchVisibility.values.firstWhere(
        (e) => e.wire == raw,
        orElse: () => SearchVisibility.discoverable,
      );

  String get labelKey => 'social.search.$wire';
}

/// The owner's own full profile (`SocialProfileOut`). Contains the privacy
/// settings; no relationship field, because viewing yourself is always SELF.
class SocialProfile {
  final String userId;
  final String? username;
  final String displayName;
  final String? bio;
  final String? avatarUrl;
  final String? cyclingCategory;
  final String? countryCode;
  final String? city;
  final ProfileVisibility profileVisibility;
  final FriendRequestsPolicy allowFriendRequests;
  final SearchVisibility searchVisibility;

  const SocialProfile({
    required this.userId,
    required this.username,
    required this.displayName,
    required this.bio,
    required this.avatarUrl,
    required this.cyclingCategory,
    required this.countryCode,
    required this.city,
    required this.profileVisibility,
    required this.allowFriendRequests,
    required this.searchVisibility,
  });

  factory SocialProfile.fromJson(Map<String, dynamic> json) => SocialProfile(
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    displayName: '${json['display_name'] ?? ''}',
    bio: json['bio'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    cyclingCategory: json['cycling_category'] as String?,
    countryCode: json['country_code'] as String?,
    city: json['city'] as String?,
    profileVisibility: ProfileVisibility.parse(
      json['profile_visibility'] as String?,
    ),
    allowFriendRequests: FriendRequestsPolicy.parse(
      json['allow_friend_requests'] as String?,
    ),
    searchVisibility: SearchVisibility.parse(
      json['search_visibility'] as String?,
    ),
  );

  SocialProfile copyWith({
    String? username,
    String? displayName,
    String? bio,
    String? avatarUrl,
    String? cyclingCategory,
    String? countryCode,
    String? city,
    ProfileVisibility? profileVisibility,
    FriendRequestsPolicy? allowFriendRequests,
    SearchVisibility? searchVisibility,
  }) => SocialProfile(
    userId: userId,
    username: username ?? this.username,
    displayName: displayName ?? this.displayName,
    bio: bio ?? this.bio,
    avatarUrl: avatarUrl ?? this.avatarUrl,
    cyclingCategory: cyclingCategory ?? this.cyclingCategory,
    countryCode: countryCode ?? this.countryCode,
    city: city ?? this.city,
    profileVisibility: profileVisibility ?? this.profileVisibility,
    allowFriendRequests: allowFriendRequests ?? this.allowFriendRequests,
    searchVisibility: searchVisibility ?? this.searchVisibility,
  );
}

/// Another rider's privacy-filtered profile (`PublicProfileOut`).
///
/// [limited] is load-bearing: it means the server redacted bio/category/place
/// under the viewer's visibility rules. Those fields are null because they are
/// withheld, NOT because the rider left them blank. The UI must show a locked
/// affordance rather than an empty section (ADR-12 §4).
class PublicProfile {
  final String userId;
  final String? username;
  final String? displayName;
  final String? bio;
  final String? avatarUrl;
  final String? cyclingCategory;
  final String? countryCode;
  final String? city;
  final RelationshipState relationship;
  final bool limited;

  const PublicProfile({
    required this.userId,
    required this.username,
    required this.displayName,
    required this.bio,
    required this.avatarUrl,
    required this.cyclingCategory,
    required this.countryCode,
    required this.city,
    required this.relationship,
    required this.limited,
  });

  factory PublicProfile.fromJson(Map<String, dynamic> json) => PublicProfile(
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    displayName: json['display_name'] as String?,
    bio: json['bio'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    cyclingCategory: json['cycling_category'] as String?,
    countryCode: json['country_code'] as String?,
    city: json['city'] as String?,
    relationship: RelationshipState.parse(json['relationship'] as String?),
    limited: json['limited'] == true,
  );

  /// Name to show: the server may withhold the display name entirely for a
  /// blocked viewer, so fall back to the handle, then to nothing.
  String? get bestName => displayName ?? username;

  /// City and country are one label, or null when withheld/unsupplied.
  String? get placeLabel {
    final parts = [
      city,
      countryCode,
    ].whereType<String>().where((s) => s.isNotEmpty);
    return parts.isEmpty ? null : parts.join(', ');
  }
}

/// A pending request row (`FriendRequestOut`).
class FriendRequest {
  final String id;
  final String userId;
  final String? username;
  final String? displayName;
  final String? avatarUrl;
  final String direction;
  final String status;
  final DateTime? createdAt;

  const FriendRequest({
    required this.id,
    required this.userId,
    required this.username,
    required this.displayName,
    required this.avatarUrl,
    required this.direction,
    required this.status,
    required this.createdAt,
  });

  factory FriendRequest.fromJson(Map<String, dynamic> json) => FriendRequest(
    id: '${json['id']}',
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    displayName: json['display_name'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    direction: '${json['direction'] ?? ''}',
    status: '${json['status'] ?? 'pending'}',
    createdAt: DateTime.tryParse('${json['created_at']}'),
  );

  bool get isIncoming => direction == 'incoming';

  String? get bestName => displayName ?? username;
}

/// An accepted friendship (`FriendOut`).
class SocialFriend {
  final String userId;
  final String? username;
  final String? displayName;
  final String? avatarUrl;
  final DateTime? friendsSince;

  const SocialFriend({
    required this.userId,
    required this.username,
    required this.displayName,
    required this.avatarUrl,
    required this.friendsSince,
  });

  factory SocialFriend.fromJson(Map<String, dynamic> json) => SocialFriend(
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    displayName: json['display_name'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    friendsSince: DateTime.tryParse('${json['friends_since']}'),
  );

  String? get bestName => displayName ?? username;
}

/// A rider the viewer blocked (`BlockOut`). Only the blocker's own list —
/// inbound blocks are invisible by design, so being blocked is never
/// confirmed to the blocked rider (ADR-12 §3).
class BlockedUser {
  final String userId;
  final String? username;
  final String? displayName;
  final DateTime? blockedAt;

  const BlockedUser({
    required this.userId,
    required this.username,
    required this.displayName,
    required this.blockedAt,
  });

  factory BlockedUser.fromJson(Map<String, dynamic> json) => BlockedUser(
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    displayName: json['display_name'] as String?,
    blockedAt: DateTime.tryParse('${json['blocked_at']}'),
  );

  String? get bestName => displayName ?? username;
}

/// One page of a paginated social listing.
class SocialPage<T> {
  final List<T> items;
  final int total;
  final int page;
  final int pageSize;

  const SocialPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  static SocialPage<T> fromJson<T>(
    Map<String, dynamic> json,
    T Function(Map<String, dynamic>) parse,
  ) => SocialPage<T>(
    items: (json['items'] as List? ?? const [])
        .map((e) => parse(e as Map<String, dynamic>))
        .toList(),
    total: (json['total'] as num? ?? 0).toInt(),
    page: (json['page'] as num? ?? 1).toInt(),
    pageSize: (json['page_size'] as num? ?? 20).toInt(),
  );
}
