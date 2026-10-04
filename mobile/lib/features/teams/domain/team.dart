/// Phase 8.2 team domain models. Every enum value is the literal the backend
/// returns or accepts (backend/app/schemas/team.py). The wire value IS the
/// domain value: nothing is renamed, mapped, or invented locally, so the UI can
/// never display a state the server did not send.
///
/// A TEAM is a separate entity from a personal friendship (ADR-13 §5). Nothing
/// in this file models a friendship, a location, or a private account field, so
/// a team surface cannot render any of them (ADR-13 §7).
library;

/// `teams.visibility`.
enum TeamVisibility {
  public_('public'),
  private_('private');

  final String wire;
  const TeamVisibility(this.wire);

  static TeamVisibility parse(String? raw) => TeamVisibility.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => TeamVisibility.public_,
  );

  String get labelKey => 'team.visibility.$wire';
}

/// `teams.status`.
enum TeamStatus {
  active('active'),
  archived('archived');

  final String wire;
  const TeamStatus(this.wire);

  static TeamStatus parse(String? raw) => TeamStatus.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => TeamStatus.active,
  );

  String get labelKey => 'team.status.$wire';
}

/// `team_memberships.role`. Owner transfer is out of scope (Phase 8.3).
enum TeamRole {
  owner('owner'),
  admin('admin'),
  member('member');

  final String wire;
  const TeamRole(this.wire);

  static TeamRole parse(String? raw) => TeamRole.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => TeamRole.member,
  );

  String get labelKey => 'team.role.$wire';

  /// Managers may invite, accept requests, remove members, and edit limited
  /// team settings. The server enforces this; the flag only decides which
  /// buttons are offered, so a stale screen cannot show an action that would be
  /// refused (ADR-13 §4).
  bool get canManage => this == TeamRole.owner || this == TeamRole.admin;

  /// Only the owner may change identity, privacy, roles, or archive the team.
  bool get isOwner => this == TeamRole.owner;
}

/// Server-authoritative viewer↔team state. Never derived client-side.
enum TeamState {
  owner('OWNER'),
  admin('ADMIN'),
  member('MEMBER'),
  joinRequestPending('JOIN_REQUEST_PENDING'),
  invited('INVITED'),
  notAffiliated('NOT_AFFILIATED');

  final String wire;
  const TeamState(this.wire);

  static TeamState parse(String? raw) => TeamState.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => TeamState.notAffiliated,
  );

  String get labelKey => 'team.state.$wire';

  /// Whether the viewer already has standing in the team.
  bool get isMember =>
      this == TeamState.owner ||
      this == TeamState.admin ||
      this == TeamState.member;

  /// The single join action to offer. Public teams join immediately; private
  /// teams go through a request. Both are the same button here — the server
  /// decides which one happens — so the client cannot offer an action the
  /// server would refuse.
  bool get canJoin => this == TeamState.notAffiliated;
}

/// `team_invitations.status`.
enum InvitationStatus {
  pending('pending'),
  accepted('accepted'),
  declined('declined'),
  revoked('revoked');

  final String wire;
  const InvitationStatus(this.wire);

  static InvitationStatus parse(String? raw) => InvitationStatus.values
      .firstWhere((e) => e.wire == raw, orElse: () => InvitationStatus.pending);

  String get labelKey => 'team.invitation.$wire';
}

/// A team as the viewer sees it (`PublicTeamOut` / `TeamOut`).
///
/// [myRole] is null for a non-member, and [state] is always present. A client
/// that renders an action from [myRole] without [state] would be deriving
/// authority locally, which ADR-13 §4 forbids.
class Team {
  final String id;
  final String name;
  final String? handle;
  final String? description;
  final String? avatarUrl;
  final String? category;
  final TeamVisibility visibility;
  final TeamStatus status;
  final int memberCount;
  final DateTime? createdAt;
  final TeamRole? myRole;
  final TeamState state;
  final int pendingRequestsCount;
  final int pendingInvitationsCount;

  const Team({
    required this.id,
    required this.name,
    required this.handle,
    required this.description,
    required this.avatarUrl,
    required this.category,
    required this.visibility,
    required this.status,
    required this.memberCount,
    required this.createdAt,
    required this.myRole,
    required this.state,
    this.pendingRequestsCount = 0,
    this.pendingInvitationsCount = 0,
  });

  factory Team.fromJson(Map<String, dynamic> json) => Team(
    id: '${json['id']}',
    name: '${json['name'] ?? ''}',
    handle: json['handle'] as String?,
    description: json['description'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    category: json['category'] as String?,
    visibility: TeamVisibility.parse(json['visibility'] as String?),
    status: TeamStatus.parse(json['status'] as String?),
    memberCount: (json['member_count'] as num? ?? 0).toInt(),
    createdAt: DateTime.tryParse('${json['created_at']}'),
    myRole: json['my_role'] == null
        ? null
        : TeamRole.parse(json['my_role'] as String?),
    state: TeamState.parse(json['state'] as String?),
    pendingRequestsCount: (json['pending_requests_count'] as num? ?? 0).toInt(),
    pendingInvitationsCount: (json['pending_invitations_count'] as num? ?? 0)
        .toInt(),
  );

  bool get isArchived => status == TeamStatus.archived;
  bool get isPrivate => visibility == TeamVisibility.private_;
  bool get isManager => myRole?.canManage ?? false;
  bool get isOwner => myRole?.isOwner ?? false;

  /// The handle as displayed, or a localized "not claimed" marker handled by
  /// the widget layer.
  String? get handleLabel {
    final h = handle;
    return (h == null || h.isEmpty) ? null : '@$h';
  }

  /// True when a manager has actionable work waiting.
  bool get hasPendingWork =>
      pendingRequestsCount > 0 || pendingInvitationsCount > 0;
}

/// One member of a team (`TeamMemberOut`). Public social identity + role.
class TeamMember {
  final String userId;
  final String? username;
  final String? displayName;
  final String? avatarUrl;
  final TeamRole role;
  final DateTime? joinedAt;

  const TeamMember({
    required this.userId,
    required this.username,
    required this.displayName,
    required this.avatarUrl,
    required this.role,
    required this.joinedAt,
  });

  factory TeamMember.fromJson(Map<String, dynamic> json) => TeamMember(
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    displayName: json['display_name'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    role: TeamRole.parse(json['role'] as String?),
    joinedAt: DateTime.tryParse('${json['joined_at']}'),
  );

  String? get bestName => displayName ?? username;
}

/// A pending request to join (`JoinRequestOut`).
class TeamJoinRequest {
  final String id;
  final String teamId;
  final String userId;
  final String? username;
  final String? displayName;
  final String? message;
  final DateTime? createdAt;

  const TeamJoinRequest({
    required this.id,
    required this.teamId,
    required this.userId,
    required this.username,
    required this.displayName,
    required this.message,
    required this.createdAt,
  });

  factory TeamJoinRequest.fromJson(
    Map<String, dynamic> json,
  ) => TeamJoinRequest(
    id: '${json['id']}',
    teamId: '${json['team_id']}',
    userId: '${json['user_id']}',
    username: json['username'] as String?,
    // The viewer's own inbox names the team; the manager view names the rider.
    displayName: json['display_name'] as String?,
    message: json['message'] as String?,
    createdAt: DateTime.tryParse('${json['created_at']}'),
  );

  String? get bestName => displayName ?? username;
}

/// An invitation (`InvitationOut`).
class TeamInvitation {
  final String id;
  final String teamId;
  final String? teamName;
  final String? teamHandle;
  final String invitedUserId;
  final String invitedByUserId;
  final String? invitedByUsername;
  final InvitationStatus status;
  final String? message;
  final DateTime? createdAt;
  final DateTime? respondedAt;

  const TeamInvitation({
    required this.id,
    required this.teamId,
    required this.teamName,
    required this.teamHandle,
    required this.invitedUserId,
    required this.invitedByUserId,
    required this.invitedByUsername,
    required this.status,
    required this.message,
    required this.createdAt,
    required this.respondedAt,
  });

  factory TeamInvitation.fromJson(Map<String, dynamic> json) => TeamInvitation(
    id: '${json['id']}',
    teamId: '${json['team_id']}',
    teamName: json['team_name'] as String?,
    teamHandle: json['team_handle'] as String?,
    invitedUserId: '${json['invited_user_id']}',
    invitedByUserId: '${json['invited_by_user_id']}',
    invitedByUsername: json['invited_by_username'] as String?,
    status: InvitationStatus.parse(json['status'] as String?),
    message: json['message'] as String?,
    createdAt: DateTime.tryParse('${json['created_at']}'),
    respondedAt: DateTime.tryParse('${json['responded_at']}'),
  );

  bool get isPending => status == InvitationStatus.pending;
}

/// One page of a paginated team listing.
class TeamPage {
  final List<Team> items;
  final int total;
  final int page;
  final int pageSize;

  const TeamPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  factory TeamPage.fromJson(Map<String, dynamic> json) => TeamPage(
    items: (json['items'] as List? ?? const [])
        .map((e) => Team.fromJson(e as Map<String, dynamic>))
        .toList(),
    total: (json['total'] as num? ?? 0).toInt(),
    page: (json['page'] as num? ?? 1).toInt(),
    pageSize: (json['page_size'] as num? ?? 20).toInt(),
  );
}

/// One page of team members.
class TeamMemberPage {
  final List<TeamMember> items;
  final int total;
  final int page;
  final int pageSize;

  const TeamMemberPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  factory TeamMemberPage.fromJson(Map<String, dynamic> json) => TeamMemberPage(
    items: (json['items'] as List? ?? const [])
        .map((e) => TeamMember.fromJson(e as Map<String, dynamic>))
        .toList(),
    total: (json['total'] as num? ?? 0).toInt(),
    page: (json['page'] as num? ?? 1).toInt(),
    pageSize: (json['page_size'] as num? ?? 20).toInt(),
  );
}

/// One page of join requests.
class TeamJoinRequestPage {
  final List<TeamJoinRequest> items;
  final int total;
  final int page;
  final int pageSize;

  const TeamJoinRequestPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  factory TeamJoinRequestPage.fromJson(Map<String, dynamic> json) =>
      TeamJoinRequestPage(
        items: (json['items'] as List? ?? const [])
            .map((e) => TeamJoinRequest.fromJson(e as Map<String, dynamic>))
            .toList(),
        total: (json['total'] as num? ?? 0).toInt(),
        page: (json['page'] as num? ?? 1).toInt(),
        pageSize: (json['page_size'] as num? ?? 20).toInt(),
      );
}

/// One page of invitations.
class TeamInvitationPage {
  final List<TeamInvitation> items;
  final int total;
  final int page;
  final int pageSize;

  const TeamInvitationPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  bool get hasMore => page * pageSize < total;

  factory TeamInvitationPage.fromJson(Map<String, dynamic> json) =>
      TeamInvitationPage(
        items: (json['items'] as List? ?? const [])
            .map((e) => TeamInvitation.fromJson(e as Map<String, dynamic>))
            .toList(),
        total: (json['total'] as num? ?? 0).toInt(),
        page: (json['page'] as num? ?? 1).toInt(),
        pageSize: (json['page_size'] as num? ?? 20).toInt(),
      );
}
