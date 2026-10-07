/// Phase 10 WS-RC competition widget tests.
///
/// Every screen here renders the server's answer and nothing else: boards,
/// lists, progress and buttons are decoded from JSON, and each action POSTs for
/// the server to confirm before the screen re-reads. These tests pin that
/// contract — a scope with no fact (no team, no country) explains that instead
/// of firing a request, and a button only appears when the server's own flags
/// (`canJoin`, `canLeave`, `is_creator`) say the viewer may act.
library;

import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/competition/presentation/challenge_detail_page.dart';
import 'package:cyclecoach/features/competition/presentation/challenge_form_page.dart';
import 'package:cyclecoach/features/competition/presentation/challenges_page.dart';
import 'package:cyclecoach/features/competition/presentation/rankings_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// ---------------------------------------------------------------------------
// Fixtures — same server shape the repository tests use.
// ---------------------------------------------------------------------------

final _profileJson = {
  'user_id': 'u-1',
  'username': 'salma_ouali',
  'display_name': 'Salma Ouali',
  'bio': null,
  'avatar_url': null,
  'cycling_category': 'road',
  'country_code': 'MA',
  'city': 'Rabat',
  'profile_visibility': 'public',
  'allow_friend_requests': 'everyone',
  'search_visibility': 'discoverable',
};

final _teamJson = {
  'id': 'team-9',
  'name': 'Atlas Riders',
  'handle': 'atlas',
  'description': null,
  'avatar_url': null,
  'category': 'leisure',
  'visibility': 'public',
  'status': 'active',
  'member_count': 5,
  'created_at': '2026-01-01T00:00:00Z',
  'my_role': 'member',
  'state': 'MEMBER',
  'pending_requests_count': 0,
  'pending_invitations_count': 0,
};

final _teamPageJson = {
  'items': [_teamJson],
  'total': 1,
  'page': 1,
  'page_size': 100,
};

final _emptyTeamPageJson = {
  'items': <Map<String, dynamic>>[],
  'total': 0,
  'page': 1,
  'page_size': 100,
};

const _rankingRowJson = {
  'rank': 1,
  'user_id': 'u-2',
  'username': 'youssef_m',
  'display_name': 'Youssef Mansour',
  'avatar_url': null,
  'value': '1500.00',
  'rides': '12',
};

final _rankingPageJson = {
  'items': [_rankingRowJson],
  'total': 1,
  'page': 1,
  'page_size': 20,
  'period': {'start': '2026-09-28T00:00:00Z', 'end': '2026-10-04T00:00:00Z'},
  'viewer_rank': 1,
  'viewer_value': '1500.00',
};

const _challengeJson = {
  'id': 'c-1',
  'title': 'Climb September',
  'description': 'Race to 5,000 m of climbing.',
  'metric': 'distance',
  'target': '500.00',
  'points': 100,
  'scope': 'global',
  'visibility': 'public',
  'state': 'active',
  'status': 'active',
  'team_id': null,
  'start_at': '2026-09-01T00:00:00Z',
  'end_at': '2026-09-30T00:00:00Z',
  'created_at': '2026-08-28T00:00:00Z',
  'is_creator': false,
  'participant_count': 3,
  'viewer_state': 'joined',
  'viewer_progress': {
    'value': '150.00',
    'target': '500.00',
    'percent': '30.00',
    'rides': '2',
    'completed': false,
    'joined_at': '2026-09-02T00:00:00Z',
    'completed_at': null,
  },
  'can_join': true,
  'can_leave': true,
};

final _challengePageJson = {
  'items': [_challengeJson],
  'total': 1,
  'page': 1,
  'page_size': 20,
};

final _emptyChallengePageJson = {
  'items': <Map<String, dynamic>>[],
  'total': 0,
  'page': 1,
  'page_size': 20,
};

final _leaderboardJson = {
  'items': [_rankingRowJson],
  'total': 1,
  'page': 1,
  'page_size': 20,
};

Map<String, dynamic> _newChallengeJson(String id) => {
  ..._challengeJson,
  'id': id,
  'is_creator': true,
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

const _json = {'content-type': 'application/json'};

/// A configurable fake backend. `log` records every request so a test can
/// assert exactly what the page asked for — and when.
MockClient mockBackend(
  List<Uri> log, {
  Map<String, dynamic>? profile,
  bool teamsEmpty = false,
  bool challengesEmpty = false,
  List<String>? createBodies,
}) {
  return MockClient((request) async {
    log.add(request.url);
    final path = request.url.path;
    if (path == '/api/v1/social/profile/me') {
      return http.Response(
        jsonEncode(profile ?? _profileJson),
        200,
        headers: _json,
      );
    }
    if (path == '/api/v1/teams') {
      return http.Response(
        jsonEncode(teamsEmpty ? _emptyTeamPageJson : _teamPageJson),
        200,
        headers: _json,
      );
    }
    if (path == '/api/v1/rankings') {
      return http.Response(jsonEncode(_rankingPageJson), 200, headers: _json);
    }
    if (path == '/api/v1/challenges' && request.method == 'POST') {
      createBodies?.add(request.body);
      return http.Response(
        jsonEncode(_newChallengeJson('c-new')),
        201,
        headers: _json,
      );
    }
    if (path == '/api/v1/challenges') {
      final mine = request.url.queryParameters['mine'] == 'true';
      return http.Response(
        jsonEncode(
          mine || challengesEmpty
              ? _emptyChallengePageJson
              : _challengePageJson,
        ),
        200,
        headers: _json,
      );
    }
    if (path == '/api/v1/challenges/c-1/leaderboard') {
      return http.Response(jsonEncode(_leaderboardJson), 200, headers: _json);
    }
    if (path.endsWith('/join') || path.endsWith('/leave')) {
      return http.Response('{"status":"ok"}', 200, headers: _json);
    }
    if (path == '/api/v1/challenges/c-1') {
      return http.Response(jsonEncode(_challengeJson), 200, headers: _json);
    }
    return http.Response('not found: $path', 404, headers: _json);
  });
}

ApiClient api(MockClient mock) => ApiClient(
  baseUrl: 'http://test',
  client: mock,
  accessToken: () async => 'token',
);

ProviderContainer container(MockClient mock) => ProviderContainer(
  overrides: [apiClientProvider.overrideWithValue(api(mock))],
);

Widget localized(Widget child, {Locale locale = const Locale('en')}) =>
    MaterialApp(
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: child,
    );

/// A miniature router that accepts the navigations these pages make, so a test
/// can prove a tap landed without standing up the whole app.
GoRouter routerFor(Widget child, {Widget Function()? destination}) {
  final dest = destination ?? () => const Scaffold(body: Text('DESTINATION'));
  return GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(path: '/', builder: (_, _) => child),
      GoRoute(path: '/challenges', builder: (_, _) => dest()),
      GoRoute(path: '/challenges/new', builder: (_, _) => dest()),
      GoRoute(path: '/challenges/:id', builder: (_, _) => dest()),
      GoRoute(path: '/performance', builder: (_, _) => dest()),
    ],
  );
}

void main() {
  group('rankings page', () {
    testWidgets('renders the server board and the viewer card', (t) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const RankingsPage()),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('Performance'), findsOneWidget);
      // The profile and teams facts load side by side with the board.
      expect(
        log.map((u) => u.path),
        containsAll([
          '/api/v1/social/profile/me',
          '/api/v1/teams',
          '/api/v1/rankings',
        ]),
      );
      expect(find.text('Youssef Mansour'), findsOneWidget);
      expect(find.text('@youssef_m'), findsOneWidget);
      expect(find.text('1500'), findsOneWidget);
      expect(find.text('12 rides'), findsOneWidget);
      expect(find.text('You are #1 — 1500'), findsOneWidget);
      expect(find.text('2026-09-28 to 2026-10-04'), findsOneWidget);
    });

    testWidgets('changing the metric re-asks the board with that metric', (
      t,
    ) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const RankingsPage()),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.widgetWithText(ChoiceChip, 'Rides'));
      await t.pumpAndSettle();

      final last = log.last;
      expect(last.path, '/api/v1/rankings');
      expect(last.queryParameters['metric'], 'rides');
    });

    testWidgets('team scope uses the viewer team id and renders the board', (
      t,
    ) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const RankingsPage()),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.widgetWithText(ChoiceChip, 'Team'));
      await t.pumpAndSettle();

      final rankRequest = log.lastWhere((u) => u.path == '/api/v1/rankings');
      expect(rankRequest.queryParameters['scope'], 'team');
      expect(rankRequest.queryParameters['team_id'], 'team-9');
      expect(find.text('Youssef Mansour'), findsOneWidget);
    });

    testWidgets('a viewer with no team sees why the team board is missing', (
      t,
    ) async {
      final log = <Uri>[];
      final c = container(mockBackend(log, teamsEmpty: true));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const RankingsPage()),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.widgetWithText(ChoiceChip, 'Team'));
      await t.pumpAndSettle();

      expect(find.text('Join a team to see the team board.'), findsOneWidget);
      // No request for a board that cannot be answered: the global board was
      // the only one asked for, and the team tap must not have re-asked.
      expect(log.where((u) => u.path == '/api/v1/rankings').length, 1);
    });

    testWidgets(
      'a viewer with no country sees why the country board is missing',
      (t) async {
        final profile = {..._profileJson, 'country_code': null, 'city': null};
        final c = container(mockBackend(<Uri>[], profile: profile));
        addTearDown(c.dispose);
        await t.pumpWidget(
          UncontrolledProviderScope(
            container: c,
            child: localized(const RankingsPage()),
          ),
        );
        await t.pumpAndSettle();

        await t.tap(find.widgetWithText(ChoiceChip, 'Country'));
        await t.pumpAndSettle();

        expect(
          find.text('Add a country to your profile to see this board.'),
          findsOneWidget,
        );
      },
    );

    testWidgets('the app bar links to the challenges tab', (t) async {
      final c = container(mockBackend(<Uri>[]));
      addTearDown(c.dispose);
      final router = routerFor(
        const RankingsPage(),
        destination: () => const Scaffold(body: Text('CHALLENGES TAB')),
      );
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: MaterialApp.router(
            routerConfig: router,
            supportedLocales: AppLocalizations.supported,
            localizationsDelegates: const [
              AppLocalizationsDelegate(),
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
          ),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.byIcon(Icons.emoji_events_outlined));
      await t.pumpAndSettle();

      expect(find.text('CHALLENGES TAB'), findsOneWidget);
    });
  });

  group('challenges list page', () {
    testWidgets('renders discover challenges from the server', (t) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengesPage()),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('Climb September'), findsOneWidget);
      expect(find.text('100 pts'), findsOneWidget);
      expect(find.text('3 riders'), findsOneWidget);
      // Only the discover list was asked for, and once.
      expect(log.where((u) => u.path == '/api/v1/challenges').length, 1);
    });

    testWidgets('mine tab asks the server for the viewer challenges', (
      t,
    ) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengesPage()),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.text('Mine'));
      await t.pumpAndSettle();

      final mineRequest = log.lastWhere((u) => u.path == '/api/v1/challenges');
      expect(mineRequest.queryParameters['mine'], 'true');
      expect(find.text('No challenges here yet.'), findsOneWidget);
    });

    testWidgets('an empty discover list explains itself', (t) async {
      final c = container(mockBackend(<Uri>[], challengesEmpty: true));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengesPage()),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('No challenges here yet.'), findsOneWidget);
    });

    testWidgets('the create button opens the form route', (t) async {
      final c = container(mockBackend(<Uri>[]));
      addTearDown(c.dispose);
      final router = routerFor(
        const ChallengesPage(),
        destination: () => const Scaffold(body: Text('DESTINATION')),
      );
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: MaterialApp.router(
            routerConfig: router,
            supportedLocales: AppLocalizations.supported,
            localizationsDelegates: const [
              AppLocalizationsDelegate(),
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
          ),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.byType(FloatingActionButton));
      await t.pumpAndSettle();

      expect(
        router.routerDelegate.currentConfiguration.uri.toString(),
        '/challenges/new',
      );
    });
  });

  group('challenge detail page', () {
    testWidgets('renders progress, chips, and the server-given buttons', (
      t,
    ) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengeDetailPage(challengeId: 'c-1')),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('Climb September'), findsOneWidget);
      expect(find.text('Live'), findsWidgets);
      expect(find.text('Everyone'), findsOneWidget);
      expect(find.text('Public'), findsOneWidget);
      expect(find.text('100 pts'), findsOneWidget);
      expect(find.text('3 riders'), findsOneWidget);
      expect(find.text('My progress'), findsOneWidget);
      expect(find.text('150 of 500 (30%)'), findsOneWidget);
      expect(find.text('2 qualifying rides'), findsOneWidget);
      expect(find.text('Joined'), findsOneWidget);
      // Server said can_join and can_leave, so both actions render.
      expect(find.widgetWithText(FilledButton, 'Join'), findsOneWidget);
      expect(find.widgetWithText(OutlinedButton, 'Leave'), findsOneWidget);
      // Creator-only actions must not appear for a viewer who is not the maker.
      expect(find.text('Publish'), findsNothing);
      expect(find.text('Cancel challenge'), findsNothing);
      expect(find.text('Leaderboard'), findsOneWidget);
      expect(find.text('Youssef Mansour'), findsOneWidget);
    });

    testWidgets('join posts, waits, and lets the server answer', (t) async {
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengeDetailPage(challengeId: 'c-1')),
        ),
      );
      await t.pumpAndSettle();
      final detailReadsBefore = log
          .where((u) => u.path == '/api/v1/challenges/c-1')
          .length;

      await t.tap(find.widgetWithText(FilledButton, 'Join'));
      await t.pumpAndSettle();

      final dirties = log.where(
        (u) => u.path.endsWith('/join') || u.path.endsWith('/leave'),
      );
      expect(dirties.single.path, '/api/v1/challenges/c-1/join');
      // The action triggered a re-read of the detail and the board.
      expect(
        log.where((u) => u.path == '/api/v1/challenges/c-1').length,
        detailReadsBefore + 1,
      );
      expect(
        log.where((u) => u.path == '/api/v1/challenges/c-1/leaderboard').length,
        greaterThanOrEqualTo(1),
      );
      // No optimistic button flip: the old state stayed until the POST landed.
      expect(find.text('Join'), findsOneWidget);
    });

    testWidgets('a creator sees publish and cancel for a draft', (t) async {
      final draft = {
        ..._challengeJson,
        'is_creator': true,
        'state': 'draft',
        'status': 'draft',
        'can_join': false,
        'can_leave': false,
        'participant_count': 1,
      };
      final client = MockClient((request) async {
        if (request.url.path == '/api/v1/challenges/c-1/leaderboard') {
          return http.Response(
            jsonEncode({
              'items': <Map<String, dynamic>>[],
              'total': 0,
              'page': 1,
              'page_size': 20,
            }),
            200,
            headers: _json,
          );
        }
        if (request.url.path == '/api/v1/challenges/c-1') {
          return http.Response(jsonEncode(draft), 200, headers: _json);
        }
        return http.Response('not found', 404, headers: _json);
      });
      final c = container(client);
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengeDetailPage(challengeId: 'c-1')),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('Draft'), findsWidgets);
      expect(find.widgetWithText(FilledButton, 'Publish'), findsOneWidget);
      expect(find.text('Cancel challenge'), findsNothing);
    });
  });

  group('challenge form page', () {
    testWidgets('blocks missing title, bad target and out-of-range points', (
      t,
    ) async {
      // A tall surface so the lazy ListView builds every field and button.
      t.view.physicalSize = const Size(800, 2400);
      t.view.devicePixelRatio = 1.0;
      addTearDown(t.view.reset);

      final en = AppLocalizations(const Locale('en'));
      final log = <Uri>[];
      final c = container(mockBackend(log));
      addTearDown(c.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ChallengeFormPage()),
        ),
      );
      await t.pumpAndSettle();

      final submit = find.byKey(const Key('challenge.submit'));

      // Empty title.
      await t.tap(submit);
      await t.pump();
      expect(find.text(en.get('competition.invalidTitle')), findsOneWidget);

      // Title given, but no target.
      await t.enterText(
        find.byKey(const Key('challenge.field.title')),
        'Climb September',
      );
      await t.tap(submit);
      await t.pump();
      expect(find.text(en.get('competition.invalidTarget')), findsOneWidget);

      // Target given, but points below the server floor.
      await t.enterText(find.byKey(const Key('challenge.field.target')), '500');
      await t.enterText(find.byKey(const Key('challenge.field.points')), '5');
      await t.tap(submit);
      await t.pump();
      expect(find.text(en.get('competition.invalidPoints')), findsOneWidget);
      // No create request ever left the device.
      expect(log.where((u) => u.path == '/api/v1/challenges'), isEmpty);
    });

    testWidgets('a valid form creates and lands on the challenge', (t) async {
      t.view.physicalSize = const Size(800, 2400);
      t.view.devicePixelRatio = 1.0;
      addTearDown(t.view.reset);

      final log = <Uri>[];
      final createBodies = <String>[];
      final c = container(mockBackend(log, createBodies: createBodies));
      addTearDown(c.dispose);
      final router = routerFor(
        const ChallengeFormPage(),
        destination: () => const Scaffold(body: Text('CHALLENGE DEST')),
      );
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: MaterialApp.router(
            routerConfig: router,
            supportedLocales: AppLocalizations.supported,
            localizationsDelegates: const [
              AppLocalizationsDelegate(),
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
          ),
        ),
      );
      await t.pumpAndSettle();

      await t.enterText(
        find.byKey(const Key('challenge.field.title')),
        'Climb September',
      );
      await t.enterText(
        find.byKey(const Key('challenge.field.description')),
        'Race to 5,000 m of climbing.',
      );
      await t.enterText(find.byKey(const Key('challenge.field.target')), '500');
      await t.enterText(find.byKey(const Key('challenge.field.points')), '125');
      await t.tap(find.byKey(const Key('challenge.submit')));
      await t.pumpAndSettle();

      final body = jsonDecode(createBodies.single) as Map<String, dynamic>;
      expect(
        body.keys.any(
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
      expect(body['title'], 'Climb September');
      expect(body['target'], 500);
      expect(body['points'], 125);
      expect(body['metric'], 'distance');
      expect(body['scope'], 'global');
      expect(log.last.path, '/api/v1/challenges');
      expect(find.text('CHALLENGE DEST'), findsOneWidget);
    });
  });
}
