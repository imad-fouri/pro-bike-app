import '../../../core/network/api_client.dart';
import '../domain/social_profile.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data, no local
/// social database.
///
/// Contract source of truth is `backend/app/api/v1/social.py`. Every call
/// below is `auth: true`: the viewer comes from the access token, never from a
/// client-supplied id. Ids in paths name only the *target* of an action, and
/// the server re-resolves them (ADR-12 §6).
///
/// Relationship state is never written by this layer. Every mutation returns
/// the server's answer and the UI re-reads the affected profile, so a stale
/// "Friends" badge cannot survive a removal.
class SocialRepository {
  final ApiClient api;
  static const prefix = '/api/v1/social';

  const SocialRepository(this.api);

  /// GET /social/profile/me
  Future<SocialProfile> myProfile() async {
    final body = await api.get('$prefix/profile/me', auth: true);
    return SocialProfile.fromJson(body);
  }

  /// PATCH /social/profile — only the identity fields the backend exposes.
  /// `null` means "leave unchanged"; the server distinguishes unset from null
  /// through `exclude_unset`, so passing an explicit null clears a field.
  Future<SocialProfile> updateProfile({
    String? username,
    bool clearUsername = false,
    String? displayName,
    String? bio,
    String? avatarUrl,
    String? cyclingCategory,
    String? countryCode,
    String? city,
  }) async {
    final body = await api.patch('$prefix/profile', {
      if (clearUsername) 'username': null,
      if (!clearUsername && username != null) 'username': username,
      'display_name': ?displayName,
      'bio': ?bio,
      'avatar_url': ?avatarUrl,
      'cycling_category': ?cyclingCategory,
      'country_code': ?countryCode,
      'city': ?city,
    }, auth: true);
    return SocialProfile.fromJson(body);
  }

  /// PATCH /social/profile/privacy
  Future<SocialProfile> updatePrivacy({
    ProfileVisibility? profileVisibility,
    FriendRequestsPolicy? allowFriendRequests,
    SearchVisibility? searchVisibility,
  }) async {
    final body = await api.patch('$prefix/profile/privacy', {
      if (profileVisibility != null)
        'profile_visibility': profileVisibility.wire,
      if (allowFriendRequests != null)
        'allow_friend_requests': allowFriendRequests.wire,
      if (searchVisibility != null) 'search_visibility': searchVisibility.wire,
    }, auth: true);
    return SocialProfile.fromJson(body);
  }

  /// GET /social/profile/{userId} — privacy-filtered by the server.
  Future<PublicProfile> userProfile(String userId) async {
    final body = await api.get('$prefix/profile/$userId', auth: true);
    return PublicProfile.fromJson(body);
  }

  /// GET /social/users/search — username / display name only. The backend
  /// never matches on email, and this client never adds a field to match on.
  Future<SocialPage<PublicProfile>> searchUsers(
    String query, {
    int page = 1,
    int pageSize = 20,
  }) async {
    // Encode the query: a handle or display name may legitimately contain
    // spaces or '&', and an unescaped value would split the query string.
    final q = {
      'q': Uri.encodeQueryComponent(query),
      'page': '$page',
      'page_size': '$pageSize',
    }.entries.map((e) => '${e.key}=${e.value}').join('&');
    final body = await api.get('$prefix/users/search?$q', auth: true);
    return SocialPage.fromJson(body, PublicProfile.fromJson);
  }

  /// POST /social/friend-requests -> 201, or 409 when a request/friendship
  /// already exists. The conflict is the server's answer, surfaced verbatim.
  Future<void> sendFriendRequest(String userId) async {
    await api.post('$prefix/friend-requests', {'user_id': userId}, auth: true);
  }

  /// GET /social/friend-requests?direction=incoming|outgoing
  Future<SocialPage<FriendRequest>> friendRequests({
    String direction = 'incoming',
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = {
      'direction': direction,
      'page': '$page',
      'page_size': '$pageSize',
    }.entries.map((e) => '${e.key}=${e.value}').join('&');
    final body = await api.get('$prefix/friend-requests?$q', auth: true);
    return SocialPage.fromJson(body, FriendRequest.fromJson);
  }

  /// POST /social/friend-requests/{id}/accept — idempotent server-side.
  Future<void> acceptFriendRequest(String requestId) async {
    await api.post('$prefix/friend-requests/$requestId/accept', {}, auth: true);
  }

  Future<void> rejectFriendRequest(String requestId) async {
    await api.post('$prefix/friend-requests/$requestId/reject', {}, auth: true);
  }

  /// DELETE /social/friend-requests/{id} — cancels an outgoing request.
  Future<void> cancelFriendRequest(String requestId) async {
    await api.delete('$prefix/friend-requests/$requestId', auth: true);
  }

  /// GET /social/friends
  Future<SocialPage<SocialFriend>> friends({
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = {
      'page': '$page',
      'page_size': '$pageSize',
    }.entries.map((e) => '${e.key}=${e.value}').join('&');
    final body = await api.get('$prefix/friends?$q', auth: true);
    return SocialPage.fromJson(body, SocialFriend.fromJson);
  }

  /// DELETE /social/friends/{userId} — removes the friendship only. It does
  /// not block, and a later request must go through the normal flow again.
  Future<void> removeFriend(String userId) async {
    await api.delete('$prefix/friends/$userId', auth: true);
  }

  /// POST /social/blocks -> destroys any friendship or pending request between
  /// the pair, server-side, in the same transaction.
  Future<void> blockUser(String userId) async {
    await api.post('$prefix/blocks', {'user_id': userId}, auth: true);
  }

  /// GET /social/blocks — only users the viewer blocked.
  Future<SocialPage<BlockedUser>> blocks({
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = {
      'page': '$page',
      'page_size': '$pageSize',
    }.entries.map((e) => '${e.key}=${e.value}').join('&');
    final body = await api.get('$prefix/blocks?$q', auth: true);
    return SocialPage.fromJson(body, BlockedUser.fromJson);
  }

  /// DELETE /social/blocks/{userId} — lifts the wall and nothing else. It
  /// never recreates a friendship (ADR-12 §3).
  Future<void> unblockUser(String userId) async {
    await api.delete('$prefix/blocks/$userId', auth: true);
  }
}
