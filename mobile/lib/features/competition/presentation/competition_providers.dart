import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/competition_repository.dart';
import '../domain/competition_models.dart';

final competitionRepositoryProvider = Provider<CompetitionRepository>(
  (ref) => CompetitionRepository(ref.watch(apiClientProvider)),
);

/// The exact facts one leaderboard read names. Value (==) semantics let the UI
/// select a scope/period/metric and keep a single live provider for the
/// current selection.
class RankingQuery {
  final RankingScope scope;
  final RankingPeriod period;
  final RankingMetric metric;
  final String? country;
  final String? city;
  final String? teamId;
  final String? category;

  const RankingQuery({
    required this.scope,
    this.period = RankingPeriod.weekly,
    this.metric = RankingMetric.distance,
    this.country,
    this.city,
    this.teamId,
    this.category,
  });

  @override
  bool operator ==(Object other) =>
      other is RankingQuery &&
      other.scope == scope &&
      other.period == period &&
      other.metric == metric &&
      other.country == country &&
      other.city == city &&
      other.teamId == teamId &&
      other.category == category;

  @override
  int get hashCode =>
      Object.hash(scope, period, metric, country, city, teamId, category);
}

/// One leaderboard page, re-read whenever the chosen facts change.
/// `autoDispose` so leaving the screen drops a board that may have moved on.
final rankingsProvider = FutureProvider.autoDispose
    .family<RankingPage, RankingQuery>((ref, query) {
      return ref
          .watch(competitionRepositoryProvider)
          .rankings(
            scope: query.scope,
            period: query.period,
            metric: query.metric,
            country: query.country,
            city: query.city,
            teamId: query.teamId,
            category: query.category,
          );
    });

/// Every challenge visible to the viewer, newest first — the list screen's only
/// source. Filtering is done server-side (`scope`, `mine`); there is no
/// client-side "filter" that could disagree with the server's view.
final challengesProvider = FutureProvider.autoDispose<ChallengePage>((ref) {
  return ref.watch(competitionRepositoryProvider).challenges();
});

/// The challenges the viewer created. Separate from the discover list because
/// "mine" is a server-side filter with different semantics (drafts included).
final myChallengesProvider = FutureProvider.autoDispose<ChallengePage>((ref) {
  return ref.watch(competitionRepositoryProvider).challenges(mine: true);
});

/// One challenge, re-read on demand.
final challengeDetailProvider = FutureProvider.autoDispose
    .family<Challenge, String>((ref, challengeId) {
      return ref.watch(competitionRepositoryProvider).challenge(challengeId);
    });

/// One challenge leaderboard page. Reads the server's cached progress, which
/// the ride-completion hook maintains; the page does not recompute anything.
final challengeLeaderboardProvider = FutureProvider.autoDispose
    .family<LeaderboardPage, String>((ref, challengeId) {
      return ref.watch(competitionRepositoryProvider).leaderboard(challengeId);
    });

/// Server-confirmed challenge mutations with duplicate-submission protection.
///
/// Mirrors the group-ride rules exactly: a key already in flight short-circuits
/// (each button reads its busy flag from this same set), and nothing is applied
/// optimistically — every mutation awaits the server, then invalidates every
/// provider whose contents the server may have changed.
///
/// Challenge progress is never computed locally. After a join/leave the detail,
/// the leaderboard, and every list are re-read, so a stale "Join" button cannot
/// outlive the join that removed it.
class ChallengeActions extends Notifier<Set<String>> {
  @override
  Set<String> build() => const <String>{};

  CompetitionRepository get repo => ref.read(competitionRepositoryProvider);

  bool isBusy(String key) => state.contains(key);

  static String key(String action, String challengeId) =>
      '$action:$challengeId';

  void _afterMutation({String? challengeId}) {
    ref.invalidate(challengesProvider);
    ref.invalidate(myChallengesProvider);
    if (challengeId != null) {
      ref.invalidate(challengeDetailProvider(challengeId));
      ref.invalidate(challengeLeaderboardProvider(challengeId));
    }
  }

  Future<Challenge?> create({
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
    Challenge? created;
    await _run('create:${title.length}:$startAt', () async {
      created = await repo.create(
        title: title,
        description: description,
        metric: metric,
        target: target,
        points: points,
        scope: scope,
        visibility: visibility,
        teamId: teamId,
        startAt: startAt,
        endAt: endAt,
        publish: publish,
      );
      _afterMutation(challengeId: created!.id);
    });
    return created;
  }

  Future<void> join(String challengeId) async {
    await _run(key('join', challengeId), () async {
      await repo.join(challengeId);
      _afterMutation(challengeId: challengeId);
    });
  }

  Future<void> leave(String challengeId) async {
    await _run(key('leave', challengeId), () async {
      await repo.leave(challengeId);
      _afterMutation(challengeId: challengeId);
    });
  }

  Future<Challenge?> publish(String challengeId) async {
    return _action(
      key('publish', challengeId),
      challengeId,
      () => repo.publish(challengeId),
    );
  }

  Future<Challenge?> cancel(String challengeId) async {
    return _action(
      key('cancel', challengeId),
      challengeId,
      () => repo.cancel(challengeId),
    );
  }

  Future<Challenge?> _action(
    String busyKey,
    String challengeId,
    Future<Challenge> Function() call,
  ) async {
    Challenge? updated;
    await _run(busyKey, () async {
      updated = await call();
      _afterMutation(challengeId: challengeId);
    });
    return updated;
  }

  Future<void> _run(String key, Future<void> Function() body) async {
    if (state.contains(key)) return;
    state = {...state, key};
    try {
      await body();
    } finally {
      final next = {...state};
      next.remove(key);
      state = next;
    }
  }
}

final challengeActionsProvider =
    NotifierProvider<ChallengeActions, Set<String>>(ChallengeActions.new);
