import '../../../core/network/api_client.dart';
import '../domain/competition_models.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// Contract source of truth is `backend/app/api/v1/rankings.py` and
/// `backend/app/api/v1/challenges.py`. Every call is `auth: true`; the viewer
/// comes from the access token, never from a body.
///
/// Server-authority law (WS-RC): a client may name facts (a scope, a period,
/// a challenge, a target, a team) but never tell the server a score, a
/// progress value, or a rank. Progress is recomputed server-side from the
/// rides table on every read; the only numbers a request may carry are the
/// *targets* a challenge creator chooses.
class CompetitionRepository {
  final ApiClient api;
  static const _base = '/api/v1';

  const CompetitionRepository(this.api);

  // --- rankings -------------------------------------------------------------

  /// One page of a leaderboard. `scope` is required by the server; `country`,
  /// `city`, `team_id` and `category` are the facts the selected scope names.
  /// The server decides who belongs on the board and how high they are.
  Future<RankingPage> rankings({
    required RankingScope scope,
    RankingPeriod period = RankingPeriod.weekly,
    RankingMetric metric = RankingMetric.distance,
    String? country,
    String? city,
    String? teamId,
    String? category,
    int page = 1,
    int pageSize = 20,
  }) async {
    final params = <String, String>{
      'scope': scope.wire,
      'period': period.wire,
      'metric': metric.wire,
      'page': '$page',
      'page_size': '$pageSize',
    };
    if (country != null && country.isNotEmpty) params['country'] = country;
    if (city != null && city.isNotEmpty) params['city'] = city;
    if (teamId != null && teamId.isNotEmpty) params['team_id'] = teamId;
    if (category != null && category.isNotEmpty) params['category'] = category;
    final query = Uri(queryParameters: params).query;
    final body = await api.get('$_base/rankings?$query', auth: true);
    return RankingPage.fromJson(body);
  }

  // --- challenges -----------------------------------------------------------

  /// Challenges visible to the viewer, newest first. `scope` narrows the list;
  /// `mine` lists only challenges the viewer created.
  Future<ChallengePage> challenges({
    ChallengeScope? scope,
    bool mine = false,
    int page = 1,
    int pageSize = 20,
  }) async {
    final params = <String, String>{'page': '$page', 'page_size': '$pageSize'};
    if (scope != null) params['scope'] = scope.wire;
    if (mine) params['mine'] = 'true';
    final query = Uri(queryParameters: params).query;
    final body = await api.get('$_base/challenges?$query', auth: true);
    return ChallengePage.fromJson(body);
  }

  /// Create a challenge. Only targets are sent: the metric, the target value,
  /// and the points on offer. Progress, completion and rank come back in the
  /// server's [Challenge] and are never in the request.
  Future<Challenge> create({
    required String title,
    String? description,
    required ChallengeMetric metric,
    required double target,
    int? points,
    ChallengeScope scope = ChallengeScope.global,
    ChallengeVisibility visibility = ChallengeVisibility.public_,
    String? teamId,
    required DateTime startAt,
    required DateTime endAt,
    bool publish = true,
  }) async {
    final body = await api.post('$_base/challenges', {
      'title': title,
      'description': ?description,
      'metric': metric.wire,
      'target': target,
      'points': ?points,
      'scope': scope.wire,
      'visibility': visibility.wire,
      'team_id': ?teamId,
      // Sent as explicit UTC ISO strings; an offset-less local time would be
      // re-read in the server's timezone, which is not the rider's.
      'start_at': startAt.toUtc().toIso8601String(),
      'end_at': endAt.toUtc().toIso8601String(),
      'publish': publish,
    }, auth: true);
    return Challenge.fromJson(body);
  }

  Future<Challenge> challenge(String challengeId) async {
    final body = await api.get('$_base/challenges/$challengeId', auth: true);
    return Challenge.fromJson(body);
  }

  Future<Challenge> publish(String challengeId) async {
    final body = await api.post(
      '$_base/challenges/$challengeId/publish',
      {},
      auth: true,
    );
    return Challenge.fromJson(body);
  }

  Future<Challenge> cancel(String challengeId) async {
    final body = await api.post(
      '$_base/challenges/$challengeId/cancel',
      {},
      auth: true,
    );
    return Challenge.fromJson(body);
  }

  /// Join a challenge. Idempotent server-side, so a double tap is not an error.
  Future<void> join(String challengeId) async {
    await api.post('$_base/challenges/$challengeId/join', {}, auth: true);
  }

  /// Leave a challenge. Idempotent server-side.
  Future<void> leave(String challengeId) async {
    await api.post('$_base/challenges/$challengeId/leave', {}, auth: true);
  }

  /// One page of a challenge leaderboard. Reads the same cached progress the
  /// ride-completion hook maintains server-side.
  Future<LeaderboardPage> leaderboard(
    String challengeId, {
    int page = 1,
    int pageSize = 20,
  }) async {
    final query = Uri(
      queryParameters: {'page': '$page', 'page_size': '$pageSize'},
    ).query;
    final body = await api.get(
      '$_base/challenges/$challengeId/leaderboard?$query',
      auth: true,
    );
    return LeaderboardPage.fromJson(body);
  }
}
