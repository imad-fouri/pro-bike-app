/// Phase 10 WS-RC competition domain models.
///
/// Every enum value is the literal the backend returns or accepts
/// (`backend/app/schemas/ranking.py`, `backend/app/schemas/challenge.py`).
/// The wire value IS the domain value: nothing is renamed, mapped, or invented
/// locally, so the UI can never display a state the server did not send.
///
/// Numbers are server outputs only. A [RankingRow.value] or a
/// [ChallengeViewerProgress.value] is recomputed server-side from the rides
/// table on every read; no client code here can post one, and none of these
/// types carries an input shape for progress, score, or rank.
library;

import '../../../core/units/api_number.dart';

/// `rankings` leaderboard scope.
enum RankingScope {
  global('global'),
  country('country'),
  city('city'),
  friends('friends'),
  team('team'),
  category('category');

  final String wire;
  const RankingScope(this.wire);

  static RankingScope parse(String? raw) => RankingScope.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => RankingScope.global,
  );

  String get labelKey => 'rankings.scope.$wire';
}

/// `rankings` query window. `all_time` has no bounds.
enum RankingPeriod {
  weekly('weekly'),
  monthly('monthly'),
  allTime('all_time');

  final String wire;
  const RankingPeriod(this.wire);

  static RankingPeriod parse(String? raw) => RankingPeriod.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => RankingPeriod.weekly,
  );

  String get labelKey => 'rankings.period.$wire';
}

/// A `rankings` metric. `points` is the challenge-completion score; the rest
/// come from qualifying rides.
enum RankingMetric {
  distance('distance'),
  elevation('elevation'),
  rides('rides'),
  training('training'),
  points('points');

  final String wire;
  const RankingMetric(this.wire);

  static RankingMetric parse(String? raw) => RankingMetric.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => RankingMetric.distance,
  );

  String get labelKey => 'rankings.metric.$wire';
}

/// A challenge's `metric`.
enum ChallengeMetric {
  distance('distance'),
  elevation('elevation'),
  rides('rides'),
  training('training'),
  streak('streak');

  final String wire;
  const ChallengeMetric(this.wire);

  static ChallengeMetric parse(String? raw) => ChallengeMetric.values
      .firstWhere((e) => e.wire == raw, orElse: () => ChallengeMetric.distance);

  String get labelKey => 'competition.metric.$wire';
}

/// A challenge's `scope` — who is eligible.
enum ChallengeScope {
  individual('individual'),
  friends('friends'),
  team('team'),
  global('global');

  final String wire;
  const ChallengeScope(this.wire);

  static ChallengeScope parse(String? raw) => ChallengeScope.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => ChallengeScope.global,
  );

  String get labelKey => 'competition.scope.$wire';
}

/// `challenges.visibility`. Private means unlisted, not invite-only.
enum ChallengeVisibility {
  public_('public'),
  private_('private');

  final String wire;
  const ChallengeVisibility(this.wire);

  static ChallengeVisibility parse(String? raw) =>
      ChallengeVisibility.values.firstWhere(
        (e) => e.wire == raw,
        orElse: () => ChallengeVisibility.public_,
      );

  String get labelKey => 'competition.visibility.$wire';
}

/// A challenge's derived clock truth. `expired` is not stored; the server
/// derives it when `end_at` passes without a completion.
enum ChallengeState {
  draft('draft'),
  scheduled('scheduled'),
  active('active'),
  completed('completed'),
  cancelled('cancelled'),
  expired('expired');

  final String wire;
  const ChallengeState(this.wire);

  static ChallengeState parse(String? raw) => ChallengeState.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => ChallengeState.draft,
  );

  String get labelKey => 'competition.state.$wire';
}

/// `challenges.status` — the stored lifecycle. Five values, no `expired`.
enum ChallengeStatus {
  draft('draft'),
  scheduled('scheduled'),
  active('active'),
  completed('completed'),
  cancelled('cancelled');

  final String wire;
  const ChallengeStatus(this.wire);

  static ChallengeStatus parse(String? raw) => ChallengeStatus.values
      .firstWhere((e) => e.wire == raw, orElse: () => ChallengeStatus.draft);

  String get labelKey => 'competition.status.$wire';
}

/// The viewer's own membership row in a challenge.
enum ViewerState {
  joined('joined'),
  active('active'),
  completed('completed'),
  left('left'),
  disqualified('disqualified');

  final String wire;
  const ViewerState(this.wire);

  static ViewerState parse(String? raw) => ViewerState.values.firstWhere(
    (e) => e.wire == raw,
    orElse: () => ViewerState.joined,
  );

  String get labelKey => 'competition.viewerState.$wire';
}

/// One ranked rider. `value` is a server aggregate, never locally computed.
class RankingRow {
  final int rank;
  final String userId;
  final String? username;
  final String? displayName;
  final String? avatarUrl;
  final double value;
  final int rides;

  const RankingRow({
    required this.rank,
    required this.userId,
    this.username,
    this.displayName,
    this.avatarUrl,
    required this.value,
    required this.rides,
  });

  factory RankingRow.fromJson(Map<String, dynamic> json) => RankingRow(
    rank: (json['rank'] as num?)?.toInt() ?? 0,
    userId: '${json['user_id'] ?? ''}',
    username: json['username'] as String?,
    displayName: json['display_name'] as String?,
    avatarUrl: json['avatar_url'] as String?,
    value: apiDoubleOr(json['value'], 0),
    rides: apiIntOr(json['rides'], 0),
  );

  /// Best available display label, never an empty string.
  String get label {
    if (displayName != null && displayName!.isNotEmpty) return displayName!;
    if (username != null && username!.isNotEmpty) return username!;
    return userId;
  }
}

/// `GET /api/v1/rankings`. `period.start`/`period.end` are the closed query
/// window the server actually used; a client should never guess "last week"
/// from a local clock.
class RankingPage {
  final List<RankingRow> items;
  final int total;
  final int page;
  final int pageSize;
  final DateTime? periodStart;
  final DateTime? periodEnd;
  final int? viewerRank;
  final double? viewerValue;

  const RankingPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
    this.periodStart,
    this.periodEnd,
    this.viewerRank,
    this.viewerValue,
  });

  factory RankingPage.fromJson(Map<String, dynamic> json) {
    final period = json['period'] as Map<String, dynamic>?;
    return RankingPage(
      items: [
        for (final r in (json['items'] as List<dynamic>? ?? const []))
          RankingRow.fromJson(r as Map<String, dynamic>),
      ],
      total: (json['total'] as num?)?.toInt() ?? 0,
      page: (json['page'] as num?)?.toInt() ?? 1,
      pageSize: (json['page_size'] as num?)?.toInt() ?? 0,
      periodStart: _date(period?['start']),
      periodEnd: _date(period?['end']),
      viewerRank: (json['viewer_rank'] as num?)?.toInt(),
      viewerValue: apiDouble(json['viewer_value']),
    );
  }
}

/// The viewer's progress in one challenge.
class ChallengeViewerProgress {
  final double value;
  final double target;
  final double percent;
  final int rides;
  final bool completed;
  final DateTime? joinedAt;
  final DateTime? completedAt;

  const ChallengeViewerProgress({
    required this.value,
    required this.target,
    required this.percent,
    required this.rides,
    required this.completed,
    this.joinedAt,
    this.completedAt,
  });

  factory ChallengeViewerProgress.fromJson(Map<String, dynamic>? json) {
    if (json == null) return ChallengeViewerProgress.empty;
    return ChallengeViewerProgress(
      value: apiDoubleOr(json['value'], 0),
      target: apiDoubleOr(json['target'], 0),
      percent: apiDoubleOr(json['percent'], 0),
      rides: apiIntOr(json['rides'], 0),
      completed: json['completed'] == true,
      joinedAt: _date(json['joined_at']),
      completedAt: _date(json['completed_at']),
    );
  }

  static const empty = ChallengeViewerProgress(
    value: 0,
    target: 0,
    percent: 0,
    rides: 0,
    completed: false,
  );
}

/// One challenge as the viewer sees it. All numbers are server outputs; the
/// only things a client may ever send are the *targets* in the create form.
class Challenge {
  final String id;
  final String title;
  final String? description;
  final ChallengeMetric metric;
  final double target;
  final int points;
  final ChallengeScope scope;
  final ChallengeVisibility visibility;
  final ChallengeState state;
  final ChallengeStatus status;
  final String? teamId;
  final DateTime startAt;
  final DateTime endAt;
  final DateTime createdAt;
  final bool isCreator;
  final int? participantCount;
  final ViewerState? viewerState;
  final ChallengeViewerProgress viewerProgress;
  final bool canJoin;
  final bool canLeave;

  const Challenge({
    required this.id,
    required this.title,
    this.description,
    required this.metric,
    required this.target,
    required this.points,
    required this.scope,
    required this.visibility,
    required this.state,
    required this.status,
    this.teamId,
    required this.startAt,
    required this.endAt,
    required this.createdAt,
    required this.isCreator,
    this.participantCount,
    this.viewerState,
    required this.viewerProgress,
    required this.canJoin,
    required this.canLeave,
  });

  factory Challenge.fromJson(Map<String, dynamic> json) => Challenge(
    id: '${json['id'] ?? ''}',
    title: '${json['title'] ?? ''}',
    description: json['description'] as String?,
    metric: ChallengeMetric.parse(json['metric'] as String?),
    target: apiDoubleOr(json['target'], 0),
    points: apiIntOr(json['points'], 0),
    scope: ChallengeScope.parse(json['scope'] as String?),
    visibility: ChallengeVisibility.parse(json['visibility'] as String?),
    state: ChallengeState.parse(json['state'] as String?),
    status: ChallengeStatus.parse(json['status'] as String?),
    teamId: json['team_id'] as String?,
    startAt: _date(json['start_at']) ?? DateTime(0),
    endAt: _date(json['end_at']) ?? DateTime(0),
    createdAt: _date(json['created_at']) ?? DateTime(0),
    isCreator: json['is_creator'] == true,
    // `participant_count` is null for a private challenge the viewer has not
    // joined — the absence itself is the privacy answer, so it is preserved.
    participantCount: (json['participant_count'] as num?)?.toInt(),
    viewerState: _stateOrNull(json['viewer_state']),
    viewerProgress: ChallengeViewerProgress.fromJson(
      json['viewer_progress'] as Map<String, dynamic>?,
    ),
    canJoin: json['can_join'] == true,
    canLeave: json['can_leave'] == true,
  );
}

/// `GET /api/v1/challenges`.
class ChallengePage {
  final List<Challenge> items;
  final int total;
  final int page;
  final int pageSize;

  const ChallengePage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  factory ChallengePage.fromJson(Map<String, dynamic> json) => ChallengePage(
    items: [
      for (final r in (json['items'] as List<dynamic>? ?? const []))
        Challenge.fromJson(r as Map<String, dynamic>),
    ],
    total: (json['total'] as num?)?.toInt() ?? 0,
    page: (json['page'] as num?)?.toInt() ?? 1,
    pageSize: (json['page_size'] as num?)?.toInt() ?? 0,
  );
}

/// One rider on a challenge leaderboard.
class LeaderboardEntry {
  final int rank;
  final String userId;
  final String? username;
  final String? displayName;
  final String? avatarUrl;
  final double value;
  final int rides;

  const LeaderboardEntry({
    required this.rank,
    required this.userId,
    this.username,
    this.displayName,
    this.avatarUrl,
    required this.value,
    required this.rides,
  });

  factory LeaderboardEntry.fromJson(Map<String, dynamic> json) =>
      LeaderboardEntry(
        rank: (json['rank'] as num?)?.toInt() ?? 0,
        userId: '${json['user_id'] ?? ''}',
        username: json['username'] as String?,
        displayName: json['display_name'] as String?,
        avatarUrl: json['avatar_url'] as String?,
        value: apiDoubleOr(json['value'], 0),
        rides: apiIntOr(json['rides'], 0),
      );

  String get label {
    if (displayName != null && displayName!.isNotEmpty) return displayName!;
    if (username != null && username!.isNotEmpty) return username!;
    return userId;
  }
}

/// `GET /api/v1/challenges/{id}/leaderboard`.
class LeaderboardPage {
  final List<LeaderboardEntry> items;
  final int total;
  final int page;
  final int pageSize;

  const LeaderboardPage({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
  });

  factory LeaderboardPage.fromJson(Map<String, dynamic> json) =>
      LeaderboardPage(
        items: [
          for (final r in (json['items'] as List<dynamic>? ?? const []))
            LeaderboardEntry.fromJson(r as Map<String, dynamic>),
        ],
        total: (json['total'] as num?)?.toInt() ?? 0,
        page: (json['page'] as num?)?.toInt() ?? 1,
        pageSize: (json['page_size'] as num?)?.toInt() ?? 0,
      );
}

ViewerState? _stateOrNull(Object? raw) {
  if (raw == null) return null;
  return ViewerState.parse('$raw');
}

/// Parse a timestamp defensively, like the other domain files.
DateTime? _date(Object? raw) {
  if (raw == null) return null;
  if (raw is DateTime) return raw;
  return DateTime.tryParse('$raw');
}
