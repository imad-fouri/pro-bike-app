import '../../../core/network/api_client.dart';
import '../domain/team.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// Contract source of truth is `backend/app/api/v1/teams.py`. Every call is
/// `auth: true`; the viewer comes from the access token, never from a body.
/// Ids in paths name only a *target* (a member to remove, a rider to invite),
/// and the server re-resolves them against the caller's own membership.
///
/// Nothing here reads or writes friendships: team state and friend state are
/// independent, and no method in this class couples them (ADR-13 §5).
class TeamRepository {
  final ApiClient api;
  static const prefix = '/api/v1/teams';

  const TeamRepository(this.api);

  String _query(Map<String, String> q) =>
      q.entries.map((e) => '${e.key}=${e.value}').join('&');

  // --- teams ----------------------------------------------------------------

  Future<Team> create({
    required String name,
    String? handle,
    String? description,
    String? avatarUrl,
    String? category,
    TeamVisibility visibility = TeamVisibility.public_,
  }) async {
    final body = await api.post(prefix, {
      'name': name,
      if (handle != null && handle.isNotEmpty) 'handle': handle,
      if (description != null && description.isNotEmpty)
        'description': description,
      if (avatarUrl != null && avatarUrl.isNotEmpty) 'avatar_url': avatarUrl,
      if (category != null && category.isNotEmpty) 'category': category,
      'visibility': visibility.wire,
    }, auth: true);
    return Team.fromJson(body);
  }

  Future<TeamPage> myTeams({
    int page = 1,
    int pageSize = 20,
    bool includeArchived = false,
  }) async {
    final q = _query({
      'page': '$page',
      'page_size': '$pageSize',
      if (includeArchived) 'include_archived': 'true',
    });
    final body = await api.get('$prefix?$q', auth: true);
    return TeamPage.fromJson(body);
  }

  Future<Team> team(String id) async {
    final body = await api.get('$prefix/$id', auth: true);
    return Team.fromJson(body);
  }

  Future<Team> update(
    String id, {
    String? name,
    String? handle,
    bool clearHandle = false,
    String? description,
    String? avatarUrl,
    String? category,
    TeamVisibility? visibility,
  }) async {
    final body = await api.patch('$prefix/$id', {
      'name': ?name,
      if (clearHandle) 'handle': null else 'handle': ?handle,
      'description': ?description,
      'avatar_url': ?avatarUrl,
      'category': ?category,
      'visibility': ?visibility?.wire,
    }, auth: true);
    return Team.fromJson(body);
  }

  Future<void> archive(String id) async {
    await api.delete('$prefix/$id', auth: true);
  }

  /// Search matches name and handle only — never a member name, so the
  /// endpoint cannot be used to enumerate a roster.
  Future<TeamPage> search(
    String query, {
    int page = 1,
    int pageSize = 20,
  }) async {
    // Encode: a team name may contain a space or '&'.
    final q = _query({
      'q': Uri.encodeQueryComponent(query),
      'page': '$page',
      'page_size': '$pageSize',
    });
    final body = await api.get('$prefix/search?$q', auth: true);
    return TeamPage.fromJson(body);
  }

  // --- members --------------------------------------------------------------

  Future<TeamMemberPage> members(
    String teamId, {
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = _query({'page': '$page', 'page_size': '$pageSize'});
    final body = await api.get('$prefix/$teamId/members?$q', auth: true);
    return TeamMemberPage.fromJson(body);
  }

  Future<void> removeMember(String teamId, String userId) async {
    await api.delete('$prefix/$teamId/members/$userId', auth: true);
  }

  /// Owner-only on the server. [role] must be admin or member: ownership is
  /// not transferable in Phase 8.2.
  Future<void> setRole(String teamId, String userId, TeamRole role) async {
    await api.patch(
      '$prefix/$teamId/members/$userId/role?role=${role.wire}',
      {},
      auth: true,
    );
  }

  Future<void> leave(String teamId) async {
    await api.delete('$prefix/$teamId/membership', auth: true);
  }

  // --- joining --------------------------------------------------------------

  /// Public team → immediate membership. Private team → join request. The
  /// server decides; the client sends the same call either way and renders the
  /// `status` it gets back (`joined` or `requested`).
  Future<({String status, String? requestId})> join(String teamId) async {
    final body = await api.post('$prefix/$teamId/join', {}, auth: true);
    return (
      status: '${body['status']}',
      requestId: body['request_id'] as String?,
    );
  }

  Future<TeamJoinRequestPage> joinRequests(
    String teamId, {
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = _query({'page': '$page', 'page_size': '$pageSize'});
    final body = await api.get('$prefix/$teamId/join-requests?$q', auth: true);
    return TeamJoinRequestPage.fromJson(body);
  }

  Future<TeamJoinRequestPage> myJoinRequests({
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = _query({'page': '$page', 'page_size': '$pageSize'});
    final body = await api.get('$prefix/my/join-requests?$q', auth: true);
    return TeamJoinRequestPage.fromJson(body);
  }

  Future<void> acceptJoinRequest(String teamId, String requestId) async {
    await api.post(
      '$prefix/$teamId/join-requests/$requestId/accept',
      {},
      auth: true,
    );
  }

  Future<void> rejectJoinRequest(String teamId, String requestId) async {
    await api.post(
      '$prefix/$teamId/join-requests/$requestId/reject',
      {},
      auth: true,
    );
  }

  Future<void> cancelJoinRequest(String teamId, String requestId) async {
    await api.delete('$prefix/$teamId/join-requests/$requestId', auth: true);
  }

  // --- invitations ----------------------------------------------------------

  Future<TeamInvitation> invite(
    String teamId,
    String userId, {
    String? message,
  }) async {
    final body = await api.post('$prefix/$teamId/invitations', {
      'user_id': userId,
      if (message != null && message.isNotEmpty) 'message': message,
    }, auth: true);
    return TeamInvitation.fromJson(body);
  }

  Future<TeamInvitationPage> teamInvitations(
    String teamId, {
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = _query({'page': '$page', 'page_size': '$pageSize'});
    final body = await api.get('$prefix/$teamId/invitations?$q', auth: true);
    return TeamInvitationPage.fromJson(body);
  }

  Future<TeamInvitationPage> myInvitations({
    String? status,
    int page = 1,
    int pageSize = 20,
  }) async {
    final q = _query({
      'status': ?status,
      'page': '$page',
      'page_size': '$pageSize',
    });
    final body = await api.get('$prefix/my/invitations?$q', auth: true);
    return TeamInvitationPage.fromJson(body);
  }

  Future<void> acceptInvitation(String invitationId) async {
    await api.post('$prefix/invitations/$invitationId/accept', {}, auth: true);
  }

  Future<void> declineInvitation(String invitationId) async {
    await api.post('$prefix/invitations/$invitationId/reject', {}, auth: true);
  }

  Future<void> revokeInvitation(String teamId, String invitationId) async {
    await api.delete('$prefix/$teamId/invitations/$invitationId', auth: true);
  }
}
