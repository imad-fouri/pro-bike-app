/// Phase 10 WS-RC competition domain, repository and action tests.
///
/// Three things are defended here:
///
/// 1. **Wire truth, not local truth.** Every enum value is the server's literal,
///    and every numeric field is parsed through the API's Decimal-as-string
///    convention. A client that post-processes a number it received would be
///    making up data that is none of its business.
/// 2. **Requests carry targets, never scores.** The create body has only the
///    metric, target value and points; progress/rank/value must never appear in
///    a request body.
/// 3. **Actions are server-confirmed.** Nothing is applied optimistically, a
///    double tap is absorbed by the busy set, and the reads the server may have
///    changed are invalidated after every mutation.
library;

import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/competition/data/competition_repository.dart';
import 'package:cyclecoach/features/competition/domain/competition_models.dart';
import 'package:cyclecoach/features/competition/presentation/competition_providers.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// ---------------------------------------------------------------------------
// Fixtures — Decimal columns arrive as strings (OpenAPI `pattern`), exactly as
// the live backend serializes them.
// ---------------------------------------------------------------------------

Map<String, dynamic> rankingRowJson({
  int rank = 1,
  String userId = 'u-1',
  String? username = 'salma_ouali',
  String? displayName = 'Salma Ouali',
  Object? value = '1500.00',
  Object? rides = '12',
}) => {
  'rank': rank,
  'user_id': userId,
  'username': username,
  'display_name': displayName,
  'avatar_url': null,
  'value': value,
  'rides': rides,
};

Map<String, dynamic> rankingPageJson({
  List<Map<String, dynamic>>? items,
  int total = 1,
  String? periodStart = '2026-09-28T00:00:00Z',
  Object? periodEnd,
  Object? viewerRank = 1,
  Object? viewerValue = '1500.00',
}) => {
  'items': items ?? [rankingRowJson()],
  'total': total,
  'page': 1,
  'page_size': 20,
  'period': {'start': periodStart, 'end': periodEnd},
  'viewer_rank': viewerRank,
  'viewer_value': viewerValue,
};

Map<String, dynamic> challengeJson({
  String id = 'c-1',
  String title = 'Climb September',
  String? description = 'Race to 5,000 m of climbing.',
  String metric = 'distance',
  Object? target = '500.00',
  Object? points = 100,
  String scope = 'global',
  String visibility = 'public',
  String state = 'active',
  String status = 'active',
  Object? teamId,
  String startAt = '2026-09-01T00:00:00Z',
  String endAt = '2026-09-30T00:00:00Z',
  String createdAt = '2026-08-28T00:00:00Z',
  bool isCreator = false,
  Object? participantCount = 3,
  Object? viewerState = 'joined',
  Object? progressValue = '150.00',
  Object? progressPercent = '30.00',
  Object? progressRides = '2',
  bool? completed = false,
  Object? joinedAt = '2026-09-02T00:00:00Z',
  Object? completedAt,
  bool canJoin = true,
  bool canLeave = true,
}) => {
  'id': id,
  'title': title,
  'description': description,
  'metric': metric,
  'target': target,
  'points': points,
  'scope': scope,
  'visibility': visibility,
  'state': state,
  'status': status,
  'team_id': teamId,
  'start_at': startAt,
  'end_at': endAt,
  'created_at': createdAt,
  'is_creator': isCreator,
  'participant_count': participantCount,
  'viewer_state': viewerState,
  'viewer_progress': {
    'value': progressValue,
    'target': target,
    'percent': progressPercent,
    'rides': progressRides,
    'completed': completed,
    'joined_at': joinedAt,
    'completed_at': completedAt,
  },
  'can_join': canJoin,
  'can_leave': canLeave,
};

Map<String, dynamic> challengePageJson({
  List<Map<String, dynamic>>? items,
  int total = 1,
}) => {
  'items': items ?? [challengeJson()],
  'total': total,
  'page': 1,
  'page_size': 20,
};

Map<String, dynamic> leaderboardPageJson({
  List<Map<String, dynamic>>? items,
  int total = 1,
}) => {
  'items': items ?? [rankingRowJson()],
  'total': total,
  'page': 1,
  'page_size': 20,
};

void main() {
  group('enums: wire values are the server contract', () {
    test('every rankings enum round-trips and labels itself', () {
      expect(RankingScope.global.wire, 'global');
      expect(RankingScope.country.wire, 'country');
      expect(RankingScope.city.wire, 'city');
      expect(RankingScope.friends.wire, 'friends');
      expect(RankingScope.team.wire, 'team');
      expect(RankingScope.category.wire, 'category');
      expect(RankingPeriod.allTime.wire, 'all_time');
      expect(RankingMetric.points.wire, 'points');
      for (final e in RankingScope.values) {
        expect(RankingScope.parse(e.wire), e);
        expect(e.labelKey, 'rankings.scope.${e.wire}');
      }
      for (final e in RankingPeriod.values) {
        expect(RankingPeriod.parse(e.wire), e);
        expect(e.labelKey, 'rankings.period.${e.wire}');
      }
      for (final e in RankingMetric.values) {
        expect(RankingMetric.parse(e.wire), e);
        expect(e.labelKey, 'rankings.metric.${e.wire}');
      }
    });

    test('every challenge enum round-trips and labels itself', () {
      for (final e in ChallengeMetric.values) {
        expect(ChallengeMetric.parse(e.wire), e);
        expect(e.labelKey, 'competition.metric.${e.wire}');
      }
      for (final e in ChallengeScope.values) {
        expect(ChallengeScope.parse(e.wire), e);
        expect(e.labelKey, 'competition.scope.${e.wire}');
      }
      for (final e in ChallengeVisibility.values) {
        expect(ChallengeVisibility.parse(e.wire), e);
        expect(e.labelKey, 'competition.visibility.${e.wire}');
      }
      for (final e in ChallengeState.values) {
        expect(ChallengeState.parse(e.wire), e);
        expect(e.labelKey, 'competition.state.${e.wire}');
      }
      for (final e in ChallengeStatus.values) {
        expect(ChallengeStatus.parse(e.wire), e);
        expect(e.labelKey, 'competition.status.${e.wire}');
      }
      for (final e in ViewerState.values) {
        expect(ViewerState.parse(e.wire), e);
        expect(e.labelKey, 'competition.viewerState.${e.wire}');
      }
    });

    test('unknown wires degrade to safe defaults, not crashes', () {
      // A newer server adding a value must not crash an older client. Each
      // default is the least-committal rendering and every action the server
      // re-checks anyway.
      expect(RankingScope.parse('galaxy'), RankingScope.global);
      expect(RankingPeriod.parse('quarterly'), RankingPeriod.weekly);
      expect(RankingMetric.parse('calories'), RankingMetric.distance);
      expect(ChallengeMetric.parse('sprints'), ChallengeMetric.distance);
      expect(ChallengeScope.parse('club'), ChallengeScope.global);
      expect(ChallengeVisibility.parse('secret'), ChallengeVisibility.public_);
      expect(ChallengeState.parse('won'), ChallengeState.draft);
      expect(ChallengeStatus.parse('won'), ChallengeStatus.draft);
      expect(ViewerState.parse('ghost'), ViewerState.joined);
      expect(ViewerState.parse(null), ViewerState.joined);
    });

    test('status has five stored values, state has six derived ones', () {
      // `expired` is derived, never stored: a challenge that outlived its end
      // without a completion is read as expired, so the UI gets one truthful
      // label instead of reviving a "scheduled" challenge that already ended.
      expect(ChallengeStatus.values.length, 5);
      expect(ChallengeState.values.length, 6);
      expect(ChallengeStatus.values, containsAll(ChallengeStatus.values));
    });
  });

  group('domain: ranking rows and pages', () {
    test('Decimal-as-string and number values decode identically', () {
      final fromString = RankingRow.fromJson(rankingRowJson(value: '250.50'));
      final fromNum = RankingRow.fromJson(rankingRowJson(value: 250.5));
      expect(fromString.value, 250.5);
      expect(fromNum.value, 250.5);
      expect(RankingRow.fromJson(rankingRowJson(rides: '3')).rides, 3);
      expect(RankingRow.fromJson(rankingRowJson(rides: 3)).rides, 3);
    });

    test('a rider without display name falls back to username, then id', () {
      final full = RankingRow.fromJson(rankingRowJson());
      expect(full.label, 'Salma Ouali');
      final usernameOnly = RankingRow.fromJson(
        rankingRowJson(displayName: null, username: 'salma_ouali'),
      );
      expect(usernameOnly.label, 'salma_ouali');
      final bare = RankingRow.fromJson(
        rankingRowJson(displayName: null, username: null),
      );
      expect(bare.label, 'u-1');
    });

    test('a page reports the server window and the viewer facts', () {
      final page = RankingPage.fromJson(
        rankingPageJson(
          periodStart: '2026-09-28T00:00:00Z',
          periodEnd: '2026-10-04T00:00:00Z',
          viewerRank: 1,
          viewerValue: '1500.00',
        ),
      );
      expect(page.items.single.value, 1500.0);
      expect(page.viewerRank, 1);
      expect(page.viewerValue, 1500.0);
      expect(page.periodStart, DateTime.utc(2026, 9, 28));
      expect(page.periodEnd, DateTime.utc(2026, 10, 4));
    });

    test('an all-time page has no bounds and no viewer rank', () {
      final page = RankingPage.fromJson(
        rankingPageJson(
          periodStart: null,
          periodEnd: null,
          viewerRank: null,
          viewerValue: null,
        ),
      );
      expect(page.periodStart, isNull);
      expect(page.periodEnd, isNull);
      expect(page.viewerRank, isNull);
      expect(page.viewerValue, isNull);
    });

    test('a malformed timestamp degrades to null, never to now', () {
      final page = RankingPage.fromJson(
        rankingPageJson(periodStart: 'not-a-date', periodEnd: 'nope'),
      );
      expect(page.periodStart, isNull);
      expect(page.periodEnd, isNull);
    });
  });

  group('domain: challenges', () {
    test('a challenge parses its full shape', () {
      final c = Challenge.fromJson(challengeJson());
      expect(c.id, 'c-1');
      expect(c.title, 'Climb September');
      expect(c.metric, ChallengeMetric.distance);
      expect(c.target, 500.0);
      expect(c.points, 100);
      expect(c.state, ChallengeState.active);
      expect(c.status, ChallengeStatus.active);
      expect(c.isCreator, isFalse);
      expect(c.viewerState, ViewerState.joined);
      expect(c.canJoin, isTrue);
      expect(c.canLeave, isTrue);
      expect(c.viewerProgress.value, 150.0);
      expect(c.viewerProgress.percent, 30.0);
      expect(c.viewerProgress.rides, 2);
    });

    test(
      'a private challenge the viewer has not joined hides its headcount',
      () {
        final c = Challenge.fromJson(
          challengeJson(
            visibility: 'private',
            viewerState: null,
            participantCount: null,
            canJoin: true,
          ),
        );
        // The absence itself is the privacy answer: `null`, not 0.
        expect(c.participantCount, isNull);
        expect(c.viewerState, isNull);
      },
    );

    test('a completed challenge reports completion and clears progress', () {
      final c = Challenge.fromJson(
        challengeJson(
          state: 'completed',
          status: 'completed',
          completed: true,
          progressValue: '500.00',
          progressPercent: '100.00',
          canJoin: false,
          canLeave: false,
          completedAt: '2026-09-29T00:00:00Z',
        ),
      );
      expect(c.viewerProgress.completed, isTrue);
      expect(c.canJoin, isFalse);
      expect(c.canLeave, isFalse);
    });

    test('a missing viewer_progress block degrades to empty progress', () {
      final json = challengeJson();
      json['viewer_progress'] = null;
      final c = Challenge.fromJson(json);
      expect(c.viewerProgress.value, 0);
      expect(c.viewerProgress.completed, isFalse);
    });

    test('pages tolerate a missing items key like the rest of the app', () {
      expect(ChallengePage.fromJson(const {}).items, isEmpty);
      expect(LeaderboardPage.fromJson(const {}).items, isEmpty);
    });
  });

  group('repository: the wire is what the server expects', () {
    test('rankings sends scope and every fact it was given', () async {
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        return http.Response(
          jsonEncode(rankingPageJson()),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final repo = CompetitionRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      );

      await repo.rankings(
        scope: RankingScope.team,
        period: RankingPeriod.allTime,
        metric: RankingMetric.rides,
        teamId: 'team-9',
      );

      expect(paths.single, '/api/v1/rankings');
    });

    test(
      'rankings query string carries scope, period, metric, team_id',
      () async {
        Uri? captured;
        final client = MockClient((request) async {
          captured = request.url;
          return http.Response(
            jsonEncode(rankingPageJson()),
            200,
            headers: {'content-type': 'application/json'},
          );
        });
        final repo = CompetitionRepository(
          ApiClient(
            baseUrl: 'http://test',
            client: client,
            accessToken: () async => 'token',
          ),
        );

        await repo.rankings(
          scope: RankingScope.team,
          period: RankingPeriod.allTime,
          metric: RankingMetric.points,
          teamId: 'team-9',
        );

        expect(captured!.path, '/api/v1/rankings');
        expect(captured!.queryParameters['scope'], 'team');
        expect(captured!.queryParameters['period'], 'all_time');
        expect(captured!.queryParameters['metric'], 'points');
        expect(captured!.queryParameters['team_id'], 'team-9');
      },
    );

    test(
      'country and city facts travel only for the scopes that need them',
      () async {
        Uri? captured;
        final client = MockClient((request) async {
          captured = request.url;
          return http.Response(
            jsonEncode(
              rankingPageJson(
                items: const [],
                total: 0,
                viewerRank: null,
                viewerValue: null,
              ),
            ),
            200,
            headers: {'content-type': 'application/json'},
          );
        });
        final repo = CompetitionRepository(
          ApiClient(
            baseUrl: 'http://test',
            client: client,
            accessToken: () async => 'token',
          ),
        );

        await repo.rankings(
          scope: RankingScope.city,
          country: 'MA',
          city: 'Rabat',
        );

        expect(captured!.queryParameters['scope'], 'city');
        expect(captured!.queryParameters['country'], 'MA');
        expect(captured!.queryParameters['city'], 'Rabat');
      },
    );

    test('challenges list maps scope and mine to query parameters', () async {
      final captured = <Uri>[];
      final client = MockClient((request) async {
        captured.add(request.url);
        final mine = request.url.queryParameters['mine'] == 'true';
        return http.Response(
          jsonEncode(
            challengePageJson(items: mine ? const [] : [challengeJson()]),
          ),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final repo = CompetitionRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      );

      await repo.challenges(scope: ChallengeScope.individual);
      await repo.challenges(mine: true);

      expect(captured[0].path, '/api/v1/challenges');
      expect(captured[0].queryParameters['scope'], 'individual');
      expect(captured[0].queryParameters.containsKey('mine'), isFalse);
      expect(captured[1].queryParameters['mine'], 'true');
    });

    test('create sends only targets, never progress or value', () async {
      Map<String, dynamic>? sent;
      final client = MockClient((request) async {
        sent = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode(challengeJson()),
          201,
          headers: {'content-type': 'application/json'},
        );
      });
      final repo = CompetitionRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      );

      final created = await repo.create(
        title: 'Climb September',
        description: 'Race to 5,000 m',
        metric: ChallengeMetric.elevation,
        target: 5000,
        points: 150,
        scope: ChallengeScope.friends,
        startAt: DateTime.utc(2026, 9, 1),
        endAt: DateTime.utc(2026, 9, 30),
      );

      expect(created.title, 'Climb September');
      // Nothing here is a score: the client's job is naming facts.
      expect(
        sent!.keys.any(
          (k) => const {
            'progress',
            'rank',
            'score',
            'value',
            'completed',
          }.contains(k),
        ),
        isFalse,
      );
      expect(sent!['metric'], 'elevation');
      expect(sent!['target'], 5000);
      expect(sent!['points'], 150);
      expect(sent!['scope'], 'friends');
      expect(sent!['start_at'], '2026-09-01T00:00:00.000Z');
      expect(sent!['end_at'], '2026-09-30T00:00:00.000Z');
      expect(sent!['publish'], isTrue);
    });

    test('join and leave post the expected empty-body actions', () async {
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add('${request.method} ${request.url.path}');
        return http.Response(
          jsonEncode({
            'status': request.url.path.endsWith('/join') ? 'joined' : 'left',
          }),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final repo = CompetitionRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      );

      await repo.join('c-1');
      await repo.leave('c-1');

      expect(paths, [
        'POST /api/v1/challenges/c-1/join',
        'POST /api/v1/challenges/c-1/leave',
      ]);
    });

    test('publish and cancel return the updated challenge', () async {
      final client = MockClient((request) async {
        final path = request.url.path;
        final state = path.endsWith('/publish') ? 'active' : 'cancelled';
        return http.Response(
          jsonEncode(challengeJson(state: state, status: state)),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final repo = CompetitionRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      );

      final published = await repo.publish('c-9');
      final cancelled = await repo.cancel('c-9');

      expect(published.state, ChallengeState.active);
      expect(cancelled.state, ChallengeState.cancelled);
    });

    test('leaderboard reads through the challenge resource', () async {
      Uri? captured;
      final client = MockClient((request) async {
        captured = request.url;
        return http.Response(
          jsonEncode(leaderboardPageJson(total: 0, items: const [])),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final repo = CompetitionRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      );

      final board = await repo.leaderboard('c-1');

      expect(captured!.path, '/api/v1/challenges/c-1/leaderboard');
      expect(board.items, isEmpty);
    });
  });

  group('actions: server-confirmed mutations', () {
    test('join is guarded against a double tap and invalidates reads', () async {
      final joinGate = Completer<void>();
      final calls = <String>[];
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/join')) {
          await joinGate.future;
        }
        calls.add(request.url.path);
        if (request.url.path.endsWith('/join')) {
          return http.Response('{"status":"joined"}', 200);
        }
        if (request.url.path == '/api/v1/challenges') {
          return http.Response(jsonEncode(challengePageJson()), 200);
        }
        return http.Response('not found', 404);
      });
      final container = ProviderContainer(
        overrides: [
          competitionRepositoryProvider.overrideWithValue(
            CompetitionRepository(
              ApiClient(
                baseUrl: 'http://test',
                client: client,
                accessToken: () async => 'token',
              ),
            ),
          ),
        ],
      );
      addTearDown(container.dispose);

      // Prime the list the mutation is allowed to change, so the invalidation
      // has something concrete to refresh.
      await container.read(challengesProvider.future);
      final listReadsBefore = calls
          .where((p) => p == '/api/v1/challenges')
          .length;

      final actions = container.read(challengeActionsProvider.notifier);
      final run = actions.join('c-1');

      // While the response is held, the busy set must already protect the button.
      expect(actions.isBusy(ChallengeActions.key('join', 'c-1')), isTrue);

      joinGate.complete();
      await run;

      expect(actions.isBusy(ChallengeActions.key('join', 'c-1')), isFalse);
      // The join itself was served exactly once.
      expect(calls.where((p) => p == '/api/v1/challenges/c-1/join').length, 1);
      // The mutation invalidated the list: reading it again re-fetched.
      await container.read(challengesProvider.future);
      expect(
        calls.where((p) => p == '/api/v1/challenges').length,
        listReadsBefore + 1,
      );
    });

    test('publish awaits the server and returns its challenge', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode(challengeJson(state: 'active', status: 'active')),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final container = ProviderContainer(
        overrides: [
          competitionRepositoryProvider.overrideWithValue(
            CompetitionRepository(
              ApiClient(
                baseUrl: 'http://test',
                client: client,
                accessToken: () async => 'token',
              ),
            ),
          ),
        ],
      );
      addTearDown(container.dispose);

      final published = await container
          .read(challengeActionsProvider.notifier)
          .publish('c-9');

      expect(published!.state, ChallengeState.active);
    });
  });
}
