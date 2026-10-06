import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/routing/app_router.dart';
import 'package:cyclecoach/features/auth/domain/auth_user.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/auth/presentation/login_page.dart';
import 'package:cyclecoach/features/auth/presentation/onboarding_page.dart';
import 'package:cyclecoach/features/coach/data/coach_repository.dart';
import 'package:cyclecoach/features/coach/domain/coach_models.dart';
import 'package:cyclecoach/features/coach/presentation/coach_page.dart';
import 'package:cyclecoach/features/coach/presentation/coach_providers.dart';
import 'package:cyclecoach/shared/widgets/placeholder_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A real-looking Coach answer: an explained ride, with provenance, one
/// recommendation the engine range-checked, and one metric the engine could not
/// measure. Decimal fields arrive as strings, exactly as the API serializes
/// them (core/units/api_number.dart).
const _explainedRide = {
  'intent': 'explain_ride',
  'summary': 'You rode 62 min at 0.78 intensity. That is an aerobic session.',
  'observations': [
    {
      'metric': 'normalized_power_w',
      'text': 'Normalized power 195 W was close to your threshold power.',
    },
  ],
  'recommendations': [
    {
      'text': 'Keep this length for now and add one steady ride next week.',
      'load_change_pct': '8.0',
      'load_target': '227.0',
    },
  ],
  'cautions': [
    {
      'code': 'not_medical_advice',
      'text': 'CycleCoach is a training tool, not a medical professional.',
    },
  ],
  'referenced_entities': [
    {'type': 'ride', 'id': 'ride-1', 'label': '2026-05-01'},
  ],
  'provenance': [
    {
      'metric': 'normalized_power_w',
      'entity': 'ride-1',
      'version': 'np_30s_v1',
      'source': 'training_calc',
    },
  ],
  'fallback_used': false,
  'prompt_version': 'coach.explain_ride.v1',
  'context': {
    'metrics': [
      {
        'key': 'normalized_power_w',
        'value': '195.0',
        'unit': 'W',
        'version': 'np_30s_v1',
        'source': 'training_calc',
      },
      // Not measured: the rider had no cadence sensor. This must never be
      // rendered as 0 — the engine had no basis for it.
      {
        'key': 'average_cadence_rpm',
        'value': null,
        'unit': 'rpm',
        'version': null,
        'source': 'training_calc',
      },
    ],
    'notes': ['Zone 2 held for 41 min.'],
    'unavailable': ['cadence', 'power'],
  },
};

const _fallbackAnswer = {
  'intent': 'weekly_summary',
  'summary': 'Your recorded week: 3 sessions, 210 load.',
  'observations': [],
  'recommendations': [],
  'cautions': [
    {'code': 'AI_DISABLED', 'text': 'AI explanations are turned off.'},
  ],
  'referenced_entities': [],
  'provenance': [],
  'fallback_used': true,
  'prompt_version': 'coach.weekly_summary.v1',
  'context': {'metrics': [], 'notes': [], 'unavailable': []},
};

const _statusLive = {
  'enabled': true,
  'provider': 'openai_compatible',
  'model': 'gpt-4o-mini',
  'fallback_only': false,
  'prompt_versions': {'explain_ride': 'coach.explain_ride.v1'},
  'limits': {
    'max_message_chars': 1000,
    'max_input_tokens': 4000,
    'max_output_tokens': 600,
    'max_requests_per_user': 20,
    'timeout_seconds': 20,
  },
};

const _statusFallbackOnly = {
  'enabled': false,
  'provider': 'none',
  'model': '',
  'fallback_only': true,
  'prompt_versions': {'explain_ride': 'coach.explain_ride.v1'},
  'limits': {
    'max_message_chars': 1000,
    'max_input_tokens': 4000,
    'max_output_tokens': 600,
    'max_requests_per_user': 20,
    'timeout_seconds': 20,
  },
};

const _activity = {
  'id': 'a-1',
  'ride_id': 'ride-1',
  'local_date': '2026-05-01',
  'started_at': '2026-05-01T07:00:00Z',
  'ended_at': '2026-05-01T08:05:00Z',
  'elapsed_seconds': 3900,
  'moving_seconds': 3720,
  'distance_m': '32000.0',
  'elevation_gain_m': '180.0',
  'analysis_version': 'activity_analysis_v1',
  'insufficient_data': false,
  'has_power': true,
  'has_heart_rate': true,
  'has_cadence': true,
  'analyzed_seconds': '3720',
  'power_seconds': '3720',
  'hr_seconds': '3720',
  'np_seconds': '3700',
  'average_power_w': '180.0',
  'max_power_w': '420.0',
  'normalized_power_w': '195.0',
  'intensity_factor': '0.78',
  'power_load': '70.0',
  'average_hr_bpm': '142.0',
  'max_hr_bpm': '171.0',
  'average_cadence_rpm': '88.0',
  'hr_load': '55.0',
  'hr_zone_model': 'hrr',
  'effective_ftp_w': '250.0',
  'ftp_source': 'manual',
  'ftp_basis': 'measured',
  'zones': [],
};

const _workout = {
  'id': 'w-1',
  'name': 'Sweet spot 2h',
  'description': null,
  'discipline': 'road',
  'goal': 'threshold',
  'status': 'active',
  'version': 1,
  'target_duration_s': 7200,
  'target_load': null,
  'intensity_note': null,
  'steps': [
    {
      'id': 1,
      'seq': 1,
      'step_type': 'warmup',
      'label': 'Warm up',
      'duration_s': 600,
      'repeat_count': 1,
      'target_zone': null,
      'target_power_low_w': null,
      'target_power_high_w': null,
      'target_hr_low_bpm': null,
      'target_hr_high_bpm': null,
    },
  ],
  'created_at': '2026-05-01T06:00:00Z',
  'updated_at': '2026-05-01T06:00:00Z',
};

/// Every request the page can make, so a test can assert what went on the wire
/// rather than only what came back.
class Log {
  final List<http.Request> requests = [];
  void add(http.Request r) => requests.add(r);
  String get lastPath => requests.last.url.path;
  String get lastBody => requests.last.body;
  Map<String, dynamic> get lastJson =>
      jsonDecode(requests.last.body) as Map<String, dynamic>;
  bool anyPath(String suffix) =>
      requests.any((r) => r.url.path.endsWith(suffix));
}

MockClient mock({
  Object? answer = _explainedRide,
  Object? status = _statusLive,
  int answerStatus = 200,
  Log? log,
}) {
  return MockClient((req) async {
    log?.add(req);
    if (req.url.path.endsWith('/coach/status')) {
      return http.Response(jsonEncode(status), 200);
    }
    if (req.url.path.contains('/training/activities')) {
      return http.Response(
        jsonEncode({
          'items': [_activity],
          'total': 1,
          'page': 1,
          'page_size': 50,
        }),
        200,
      );
    }
    if (req.url.path.contains('/workouts')) {
      return http.Response(
        jsonEncode({
          'items': [_workout],
          'total': 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (req.url.path.startsWith('/api/v1/coach/')) {
      return http.Response(jsonEncode(answer), answerStatus);
    }
    // WS-SM: the home screen mounts an ad slot whose policy reads the
    // entitlement cache. The real backend answers this endpoint, so the
    // double must too — otherwise the fetch errors and Riverpod's retry
    // timer outlives the test.
    if (req.url.path.endsWith('/me/entitlements')) {
      return http.Response(
        jsonEncode({
          'plan': 'free',
          'free_capabilities': const ['core_ride_recording'],
          'entitlements': const [],
          'evaluated_at': '2026-10-06T06:00:00Z',
        }),
        200,
      );
    }
    return http.Response('not found', 404);
  });
}

ApiClient api(MockClient mock) => ApiClient(
  baseUrl: 'http://test',
  client: mock,
  accessToken: () async => 'test-token',
);

ProviderContainer container(MockClient mock) => ProviderContainer(
  overrides: [apiClientProvider.overrideWithValue(api(mock))],
);

/// The page owns its own Scaffold, so the harness only supplies the localizers
/// it needs and a ProviderScope.
Widget harness(ProviderContainer c, {Locale locale = const Locale('en')}) {
  return UncontrolledProviderScope(
    container: c,
    child: MaterialApp(
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const Scaffold(body: CoachPage()),
    ),
  );
}

/// Router harness: `/coach` is behind the session, so the redirect has to see
/// an authenticated state or it would bounce to /login before the page loads.
class _FixedAuth extends AuthNotifier {
  final AuthState fixed;
  _FixedAuth([
    this.fixed = const AuthState(AuthStatus.authenticated, user: _user),
  ]);
  @override
  AuthState build() => fixed;
}

const _user = AuthUser(
  id: 'u1',
  email: 'r@e.com',
  displayName: 'R',
  emailVerified: false,
);

Widget routerHarness(ProviderContainer c) {
  return UncontrolledProviderScope(
    container: c,
    child: Consumer(
      builder: (context, ref, _) => MaterialApp.router(
        locale: const Locale('en'),
        supportedLocales: AppLocalizations.supported,
        localizationsDelegates: const [
          AppLocalizationsDelegate(),
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        routerConfig: ref.watch(routerProvider),
      ),
    ),
  );
}

void main() {
  group('models', () {
    test('parses a decimal-string value and keeps a missing one null', () {
      final m = CoachMessage.fromJson(_explainedRide);
      final metric = m.context.metrics.firstWhere(
        (e) => e.key == 'normalized_power_w',
      );
      expect(metric.value, 195.0);
      expect(metric.display, '195.0 W');

      // The absent measurement must not become 0 — it has to stay unavailable.
      final missing = m.context.metrics.firstWhere(
        (e) => e.key == 'average_cadence_rpm',
      );
      expect(missing.value, isNull);
      expect(missing.display, '—');
    });

    test('intent round-trips the server wire value', () {
      expect(CoachIntent.parse('explain_ride'), CoachIntent.explainRide);
      expect(CoachIntent.parse('weekly_summary'), CoachIntent.weeklySummary);
      expect(CoachIntent.explainRide.wire, 'explain_ride');
      expect(CoachIntent.weeklySummary.needsResourcePointer, isFalse);
      expect(CoachIntent.explainWorkout.needsResourcePointer, isTrue);
      expect(CoachIntent.trainingQuestion.needsMessage, isTrue);
    });

    test('fallback answer is distinguishable from a model answer', () {
      expect(CoachMessage.fromJson(_explainedRide).fallbackUsed, isFalse);
      expect(CoachMessage.fromJson(_fallbackAnswer).fallbackUsed, isTrue);
    });
  });

  group('contract', () {
    /// STEP 17: the mobile model must match the backend JSON shape field for
    /// field — not approximately, but key for key. The fixture mirrors the
    /// backend `CoachResponse` schema (ADR-11 §3, §4).
    test('every backend response key decodes into the typed model', () {
      final raw =
          jsonDecode(jsonEncode(_explainedRide)) as Map<String, dynamic>;
      expect(
        raw.keys,
        containsAll([
          'intent',
          'summary',
          'observations',
          'recommendations',
          'cautions',
          'referenced_entities',
          'provenance',
          'fallback_used',
          'prompt_version',
          'context',
        ]),
      );
      final m = CoachMessage.fromJson(raw);
      expect(m.intent, CoachIntent.explainRide);
      expect(m.summary, isNotEmpty);
      expect(m.observations.single.metric, 'normalized_power_w');
      expect(m.observations.single.text, isNotEmpty);
      expect(m.recommendations.single.loadChangePct, 8.0);
      expect(m.recommendations.single.loadTarget, 227.0);
      expect(m.cautions.single.code, 'not_medical_advice');
      expect(m.referencedEntities.single.type, 'ride');
      expect(m.referencedEntities.single.id, 'ride-1');
      expect(m.provenance.single.source, 'training_calc');
      expect(m.provenance.single.version, 'np_30s_v1');
      expect(m.fallbackUsed, isFalse);
      expect(m.promptVersion, 'coach.explain_ride.v1');
      expect(m.context.notes.single, 'Zone 2 held for 41 min.');
      expect(m.context.unavailable, ['cadence', 'power']);
    });

    test('the fallback shape decodes with the backend caution codes', () {
      final raw =
          jsonDecode(jsonEncode(_fallbackAnswer)) as Map<String, dynamic>;
      final m = CoachMessage.fromJson(raw);
      expect(m.intent, CoachIntent.weeklySummary);
      expect(m.fallbackUsed, isTrue);
      // Uppercase backend contract codes pass through untouched.
      expect(m.cautions.single.code, 'AI_DISABLED');
      expect(m.observations, isEmpty);
      expect(m.context.metrics, isEmpty);
    });
  });

  group('repository', () {
    test('sends intent, message and a pointer — never a metric', () async {
      final log = Log();
      final repo = CoachRepository(api(mock(log: log)));
      await repo.ask(
        intent: CoachIntent.explainRide,
        message: 'Why was that hard?',
        rideId: 'ride-1',
        locale: 'en',
      );
      final body = log.lastJson;
      expect(body['intent'], 'explain_ride');
      expect(body['message'], 'Why was that hard?');
      expect(
        (body['context_reference'] as Map<String, dynamic>)['ride_id'],
        'ride-1',
      );
      // ADR-11 §8: a client-supplied metric is rejected server-side, so the
      // app must not even attempt to send one.
      for (final forbidden in [
        'metrics',
        'normalized_power_w',
        'intensity_factor',
        'power',
        'hr',
      ]) {
        expect(body.keys, isNot(contains(forbidden)));
      }
    });

    test('omits the reference when there is no resource to point at', () async {
      final log = Log();
      final repo = CoachRepository(api(mock(log: log)));
      await repo.ask(
        intent: CoachIntent.weeklySummary,
        message: '',
        locale: 'fr',
      );
      expect(log.lastJson.containsKey('context_reference'), isFalse);
      expect(log.lastJson['locale'], 'fr');
    });

    test('weekly summary is a GET carrying the locale', () async {
      final log = Log();
      final repo = CoachRepository(api(mock(log: log)));
      await repo.weeklySummary(locale: 'ar');
      expect(log.requests.last.method, 'GET');
      expect(log.lastPath, '/api/v1/coach/weekly-summary');
      expect(log.requests.last.url.queryParameters['locale'], 'ar');
    });

    test('decodes a deterministic fallback as a normal answer', () async {
      final repo = CoachRepository(api(mock(answer: _fallbackAnswer)));
      final m = await repo.weeklySummary(locale: 'en');
      expect(m.fallbackUsed, isTrue);
      expect(m.cautions.single.code, 'AI_DISABLED');
    });

    test(
      'a transport failure surfaces as an ApiException, never an answer',
      () async {
        final repo = CoachRepository(api(mock(answerStatus: 500)));
        await expectLater(
          repo.ask(
            intent: CoachIntent.weeklySummary,
            message: '',
            locale: 'en',
          ),
          throwsA(isA<ApiException>()),
        );
      },
    );

    test('a malformed payload surfaces as an error, never an answer', () async {
      final broken = MockClient(
        (_) async => http.Response('this is not JSON', 200),
      );
      final repo = CoachRepository(api(broken));
      // Phase 8.6: a 2xx body that is not a JSON object is now mapped to
      // ApiException/INVALID_RESPONSE instead of leaking a raw FormatException,
      // so callers can distinguish a contract violation from a network fault.
      await expectLater(
        repo.ask(intent: CoachIntent.weeklySummary, message: '', locale: 'en'),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'INVALID_RESPONSE'),
        ),
      );
    });

    test('explainRide posts to the ride shortcut', () async {
      final log = Log();
      final repo = CoachRepository(api(mock(log: log)));
      final m = await repo.explainRide('ride-1', locale: 'en');
      expect(log.requests.last.method, 'POST');
      expect(log.lastPath, '/api/v1/coach/ride/ride-1/explain');
      expect(log.lastJson['locale'], 'en');
      expect(m.intent, CoachIntent.explainRide);
    });

    test('explainWorkout posts to the workout shortcut', () async {
      final log = Log();
      final repo = CoachRepository(api(mock(log: log)));
      // The shared mock answers with the ride fixture; what matters here is
      // the route hit and the locale carried.
      final m = await repo.explainWorkout('w-1', locale: 'fr');
      expect(log.requests.last.method, 'POST');
      expect(log.lastPath, '/api/v1/coach/workout/w-1/explain');
      expect(log.lastJson['locale'], 'fr');
      expect(m.summary, isNotEmpty);
    });

    test('status decodes limits, versions and fallback mode', () async {
      final live = CoachRepository(api(mock()));
      final s = await live.status();
      expect(s.fallbackOnly, isFalse);
      expect(s.limits.maxMessageChars, 1000);
      expect(s.promptVersions['explain_ride'], 'coach.explain_ride.v1');

      final off = CoachRepository(api(mock(status: _statusFallbackOnly)));
      final s2 = await off.status();
      expect(s2.fallbackOnly, isTrue);
      expect(s2.provider, 'none');
    });

    test('an error followed by a retry asks again', () async {
      var calls = 0;
      final flaky = MockClient((_) async {
        calls++;
        if (calls == 1) return http.Response('boom', 500);
        return http.Response(jsonEncode(_explainedRide), 200);
      });
      final repo = CoachRepository(api(flaky));
      await expectLater(
        repo.ask(intent: CoachIntent.weeklySummary, message: '', locale: 'en'),
        throwsA(isA<ApiException>()),
      );
      final m = await repo.ask(
        intent: CoachIntent.weeklySummary,
        message: '',
        locale: 'en',
      );
      expect(m.intent, CoachIntent.explainRide);
      expect(calls, 2);
    });
  });

  group('page', () {
    testWidgets('weekly summary asks and renders the explanation', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('coach.answer.empty')), findsOneWidget);
      // The button is disabled until there is something to ask about; the
      // weekly summary needs no pointer, so it is ready immediately.
      final ask = t.widget<FilledButton>(find.byKey(const Key('coach.ask')));
      expect(ask.onPressed, isNotNull);

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('coach.answer')), findsOneWidget);
      expect(find.text(_explainedRide['summary'] as String), findsOneWidget);
      expect(log.lastPath, '/api/v1/coach/message');
      expect(log.lastJson['intent'], 'weekly_summary');
    });

    testWidgets('ride intent requires a ride before the button is enabled', (
      t,
    ) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.intent.explain_ride')));
      await t.pumpAndSettle();

      // No ride chosen yet: the request must not be sent at all.
      final ask = t.widget<FilledButton>(find.byKey(const Key('coach.ask')));
      expect(ask.onPressed, isNull);
      expect(log.anyPath('/coach/message'), isFalse);
    });

    testWidgets('choosing a ride sends the ride pointer', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.intent.explain_ride')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('coach.ridePicker')));
      await t.pumpAndSettle();
      await t.tap(find.textContaining('2026-05-01').last);
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      expect(log.lastJson['intent'], 'explain_ride');
      expect(
        (log.lastJson['context_reference'] as Map<String, dynamic>)['ride_id'],
        'ride-1',
      );
    });

    testWidgets('a question cannot be sent empty', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.intent.training_question')));
      await t.pumpAndSettle();

      final ask = t.widget<FilledButton>(find.byKey(const Key('coach.ask')));
      expect(ask.onPressed, isNull);

      await t.enterText(
        find.byKey(const Key('coach.message')),
        'Why is my FTP low?',
      );
      await t.pumpAndSettle();
      final ready = t.widget<FilledButton>(find.byKey(const Key('coach.ask')));
      expect(ready.onPressed, isNotNull);

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();
      expect(log.lastJson['message'], 'Why is my FTP low?');
    });

    testWidgets('switching intent clears the previous pointer', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.intent.explain_ride')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('coach.ridePicker')));
      await t.pumpAndSettle();
      await t.tap(find.textContaining('2026-05-01').last);
      await t.pumpAndSettle();

      // A ride pointer must never ride along with a weekly summary request.
      await t.tap(find.byKey(const Key('coach.intent.weekly_summary')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      expect(log.lastJson.containsKey('context_reference'), isFalse);
    });

    testWidgets('shows the deterministic label when the provider is off', (
      t,
    ) async {
      final log = Log();
      final c = container(
        mock(answer: _fallbackAnswer, status: _statusFallbackOnly, log: log),
      );
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      expect(
        find.byKey(const Key('coach.status.fallbackOnly')),
        findsOneWidget,
      );
      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      // The banner and the answer badge must agree, so a user cannot read a
      // deterministic answer as a model's opinion.
      expect(find.text('Recorded numbers only'), findsOneWidget);
    });

    testWidgets('a failed request is an error, never an answer', (t) async {
      final c = container(mock(answer: _statusLive, answerStatus: 500));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('coach.answer.error')), findsOneWidget);
      expect(find.byKey(const Key('coach.answer')), findsNothing);
    });

    testWidgets('an unknown status degrades without hiding the coach', (
      t,
    ) async {
      final c = container(
        MockClient(
          (req) async => req.url.path.endsWith('/coach/status')
              ? http.Response('boom', 500)
              : http.Response(jsonEncode(_explainedRide), 200),
        ),
      );
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('coach.status.unknown')), findsOneWidget);
      expect(
        find.byKey(const Key('coach.intent.weekly_summary')),
        findsOneWidget,
      );
    });

    testWidgets('arabic locale asks in arabic and renders RTL', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c, locale: const Locale('ar')));
      await t.pumpAndSettle();

      expect(find.text('المدرب'), findsWidgets);
      final ctx = t.element(find.byKey(const Key('coach.ask')));
      expect(Directionality.of(ctx), TextDirection.rtl);

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();
      expect(log.lastJson['locale'], 'ar');
    });

    testWidgets('observations, cautions and provenance are all shown', (
      t,
    ) async {
      final c = container(mock());
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      expect(
        find.text('Normalized power 195 W was close to your threshold power.'),
        findsOneWidget,
      );
      expect(
        find.text('CycleCoach is a training tool, not a medical professional.'),
        findsOneWidget,
      );
      expect(find.textContaining('np_30s_v1'), findsWidgets);
      expect(find.textContaining('+8 %'), findsOneWidget);
    });

    testWidgets('an unmeasured metric is shown as unavailable', (t) async {
      final c = container(mock());
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      // The context reports the missing measurement by key, and a 0 must never
      // stand in for it.
      expect(find.text('cadence'), findsOneWidget);
      expect(find.text('0.0 rpm'), findsNothing);
    });

    testWidgets('choosing a workout sends the workout pointer', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.intent.explain_workout')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('coach.workoutPicker')));
      await t.pumpAndSettle();
      await t.tap(find.textContaining('Sweet spot 2h').last);
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();

      expect(log.lastJson['intent'], 'explain_workout');
      expect(
        (log.lastJson['context_reference']
            as Map<String, dynamic>)['workout_id'],
        'w-1',
      );
    });

    testWidgets('shows a loading indicator while the answer is in flight', (
      t,
    ) async {
      final gate = Completer<http.Response>();
      final gated = MockClient((req) async {
        if (req.url.path.endsWith('/coach/message')) return gate.future;
        if (req.url.path.endsWith('/coach/status')) {
          return http.Response(jsonEncode(_statusLive), 200);
        }
        // The quick actions watch the training providers on every build, so
        // those paths must answer too — otherwise their errors leave retry
        // timers pending after the test.
        if (req.url.path.contains('/training/activities')) {
          return http.Response(
            jsonEncode({
              'items': [_activity],
              'total': 1,
              'page': 1,
              'page_size': 50,
            }),
            200,
          );
        }
        if (req.url.path.contains('/workouts')) {
          return http.Response(
            jsonEncode({
              'items': [_workout],
              'total': 1,
              'page': 1,
              'page_size': 20,
            }),
            200,
          );
        }
        return http.Response('not found', 404);
      });
      final c = container(gated);
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pump();
      // The answer area sits below the fold on a phone viewport: scroll like
      // a user before asserting what is rendered there.
      await t.drag(find.byType(ListView), const Offset(0, -1000));
      await t.pump();
      expect(find.byType(CircularProgressIndicator), findsWidgets);

      gate.complete(http.Response(jsonEncode(_explainedRide), 200));
      await t.pumpAndSettle();
      await t.drag(find.byType(ListView), const Offset(0, -1000));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('coach.answer')), findsOneWidget);
    });

    testWidgets('french locale asks in french', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c, locale: const Locale('fr')));
      await t.pumpAndSettle();

      expect(find.text('Demander au coach'), findsOneWidget);
      expect(find.text('Ma semaine'), findsOneWidget);
      expect(
        find.text(
          'Posez une question sur une sortie, une séance, votre semaine ou l’entraînement.',
        ),
        findsOneWidget,
      );

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();
      expect(log.lastJson['locale'], 'fr');
    });

    testWidgets('retry after an error renders the fresh answer', (t) async {
      var calls = 0;
      final flaky = MockClient((req) async {
        if (req.url.path.endsWith('/coach/status')) {
          return http.Response(jsonEncode(_statusLive), 200);
        }
        if (req.url.path.contains('/training/activities')) {
          return http.Response(
            jsonEncode({
              'items': [_activity],
              'total': 1,
              'page': 1,
              'page_size': 50,
            }),
            200,
          );
        }
        if (req.url.path.contains('/workouts')) {
          return http.Response(
            jsonEncode({
              'items': [_workout],
              'total': 1,
              'page': 1,
              'page_size': 20,
            }),
            200,
          );
        }
        calls++;
        if (calls == 1) return http.Response('boom', 500);
        return http.Response(jsonEncode(_explainedRide), 200);
      });
      final c = container(flaky);
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();
      await t.drag(find.byType(ListView), const Offset(0, -1000));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('coach.answer.error')), findsOneWidget);

      // Back up to the button: it scrolled out of view with the drag.
      await t.drag(find.byType(ListView), const Offset(0, 1000));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();
      await t.drag(find.byType(ListView), const Offset(0, -1000));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('coach.answer')), findsOneWidget);
      expect(calls, 2);
    });

    testWidgets('quick last ride asks about the latest ride at once', (
      t,
    ) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.quick.lastRide')));
      await t.pumpAndSettle();

      expect(log.lastJson['intent'], 'explain_ride');
      expect(
        (log.lastJson['context_reference'] as Map<String, dynamic>)['ride_id'],
        'ride-1',
      );
      await t.drag(find.byType(ListView), const Offset(0, -1000));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('coach.answer')), findsOneWidget);
    });

    testWidgets('quick week summarizes without any pointer', (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.quick.week')));
      await t.pumpAndSettle();

      expect(log.lastJson['intent'], 'weekly_summary');
      expect(log.lastJson.containsKey('context_reference'), isFalse);
    });

    testWidgets("quick workout explains the rider's active plan", (t) async {
      final log = Log();
      final c = container(mock(log: log));
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.quick.todaysWorkout')));
      await t.pumpAndSettle();

      expect(log.lastJson['intent'], 'explain_workout');
      expect(
        (log.lastJson['context_reference']
            as Map<String, dynamic>)['workout_id'],
        'w-1',
      );
    });

    testWidgets('quick actions wait for their data instead of asking blind', (
      t,
    ) async {
      final log = Log();
      final empty = MockClient((req) async {
        log.add(req);
        if (req.url.path.endsWith('/coach/status')) {
          return http.Response(jsonEncode(_statusLive), 200);
        }
        if (req.url.path.contains('/training/activities')) {
          return http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1, 'page_size': 50}),
            200,
          );
        }
        if (req.url.path.contains('/workouts')) {
          return http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1, 'page_size': 20}),
            200,
          );
        }
        return http.Response(jsonEncode(_explainedRide), 200);
      });
      final c = container(empty);
      await t.pumpWidget(harness(c));
      await t.pumpAndSettle();

      // Nothing to explain, so the buttons stay disabled and no request that
      // the server would have to refuse with a 422 is ever sent.
      final lastRide = t.widget<FilledButton>(
        find.byKey(const Key('coach.quick.lastRide')),
      );
      expect(lastRide.onPressed, isNull);
      final workout = t.widget<FilledButton>(
        find.byKey(const Key('coach.quick.todaysWorkout')),
      );
      expect(workout.onPressed, isNull);
      expect(log.anyPath('/coach/message'), isFalse);
    });
  });

  group('providers', () {
    test('a second ask while loading issues no second request', () async {
      var calls = 0;
      final gate = Completer<http.Response>();
      final gated = MockClient((_) async {
        calls++;
        return gate.future;
      });
      final c = ProviderContainer(
        overrides: [apiClientProvider.overrideWithValue(api(gated))],
      );
      addTearDown(c.dispose);

      final notifier = c.read(coachAskProvider.notifier);
      // `build()` is async, so the initial state is loading until its
      // microtask settles. Wait for the true initial state first.
      await c.read(coachAskProvider.future);
      final first = notifier.ask(
        intent: CoachIntent.weeklySummary,
        message: '',
        locale: 'en',
      );
      // Still in flight: this submission must be dropped, not sent.
      await notifier.ask(
        intent: CoachIntent.weeklySummary,
        message: '',
        locale: 'en',
      );
      gate.complete(http.Response(jsonEncode(_explainedRide), 200));
      await first;

      expect(calls, 1);
      expect(c.read(coachAskProvider).value?.intent, CoachIntent.explainRide);
    });
  });

  group('router', () {
    testWidgets('/coach resolves to the coach, not a placeholder', (t) async {
      final c = ProviderContainer(
        overrides: [
          authProvider.overrideWith(_FixedAuth.new),
          apiClientProvider.overrideWithValue(api(mock())),
        ],
      );
      addTearDown(c.dispose);

      await t.pumpWidget(routerHarness(c));
      await t.pumpAndSettle();

      // The real router, navigated the way the app navigates. A registered
      // '/coach' must reach CoachPage and must not fall through to the
      // placeholder loop.
      c.read(routerProvider).go('/coach');
      await t.pumpAndSettle();

      expect(find.byType(CoachPage), findsOneWidget);
      expect(find.byType(PlaceholderPage), findsNothing);
    });

    testWidgets('an unauthenticated deep link never reaches the coach', (
      t,
    ) async {
      final c = ProviderContainer(
        overrides: [
          authProvider.overrideWith(
            () => _FixedAuth(const AuthState(AuthStatus.unauthenticated)),
          ),
          apiClientProvider.overrideWithValue(api(mock())),
        ],
      );
      addTearDown(c.dispose);

      await t.pumpWidget(routerHarness(c));
      await t.pumpAndSettle();
      c.read(routerProvider).go('/coach');
      await t.pumpAndSettle();

      expect(find.byType(CoachPage), findsNothing);
      expect(find.byType(OnboardingPage), findsOneWidget);
      expect(find.byType(LoginPage), findsNothing);
    });
  });
}
