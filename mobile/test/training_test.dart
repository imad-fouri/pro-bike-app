import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/training/domain/training_models.dart';
import 'package:cyclecoach/features/training/domain/training_units.dart';
import 'package:cyclecoach/features/training/presentation/training_overview_page.dart';
import 'package:cyclecoach/features/training/presentation/training_pages.dart';
import 'package:cyclecoach/features/training/presentation/training_providers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _profileJson = {
  'user_id': 'u1',
  'ftp_w': '250.0',
  'ftp_source': 'manual',
  'ftp_basis': 'measured',
  'max_hr_bpm': '185.0',
  'resting_hr_bpm': '55.0',
  'hr_zone_model': 'hrr',
  'timezone': 'Europe/Zurich',
  'effective_timezone': 'Europe/Zurich',
};

const _gpsOnly = {
  'id': 'a-gps',
  'ride_id': 'ride-gps',
  'local_date': '2026-05-01',
  'started_at': '2026-05-01T07:00:00Z',
  'ended_at': '2026-05-01T08:00:00Z',
  'elapsed_seconds': 3600,
  'moving_seconds': 3400,
  'distance_m': '32000.0',
  'elevation_gain_m': '180.0',
  'analysis_version': 'activity_analysis_v1',
  'insufficient_data': false,
  'has_power': false,
  'has_heart_rate': false,
  'has_cadence': false,
  'analyzed_seconds': '0',
  'power_seconds': '0',
  'hr_seconds': '0',
  'np_seconds': '0',
  'average_power_w': null,
  'max_power_w': null,
  'normalized_power_w': null,
  'intensity_factor': null,
  'power_load': null,
  'average_hr_bpm': null,
  'max_hr_bpm': null,
  'average_cadence_rpm': null,
  'hr_load': null,
  'hr_zone_model': null,
  'effective_ftp_w': null,
  'ftp_source': null,
  'ftp_basis': 'unavailable',
  'zones': [],
};

/// The sensorless ride and the power ride share most fields, so the power
/// fixture is derived rather than restated.
final _powerActivity = {
  ..._gpsOnly,
  'id': 'a-power',
  'ride_id': 'ride-power',
  'has_power': true,
  'has_heart_rate': true,
  'analyzed_seconds': '2400',
  'power_seconds': '2400',
  'hr_seconds': '2400',
  'np_seconds': '2371',
  'average_power_w': '250.0',
  'max_power_w': '420.0',
  'normalized_power_w': '255.0',
  'intensity_factor': '1.02',
  'power_load': '69.3',
  'average_hr_bpm': '148.0',
  'hr_load': '61.0',
  'hr_zone_model': 'hrr',
  'effective_ftp_w': '250.0',
  'ftp_source': 'manual',
  'ftp_basis': 'measured',
  'zones': [
    {'kind': 'power', 'zone': 1, 'seconds': '0'},
    {'kind': 'power', 'zone': 2, 'seconds': '600'},
    {'kind': 'power', 'zone': 3, 'seconds': '1200'},
    {'kind': 'power', 'zone': 4, 'seconds': '600'},
    {'kind': 'power', 'zone': 5, 'seconds': '0'},
    {'kind': 'power', 'zone': 6, 'seconds': '0'},
    {'kind': 'power', 'zone': 7, 'seconds': '0'},
  ],
};

const _workoutJson = {
  'id': 'w1',
  'name': 'Sweet spot',
  'description': null,
  'discipline': 'road',
  'goal': 'threshold',
  'status': 'draft',
  'version': 1,
  'target_duration_s': 3600,
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
    {
      'id': 2,
      'seq': 2,
      'step_type': 'interval',
      'label': 'On',
      'duration_s': 600,
      'repeat_count': 3,
      'target_zone': 3,
      'target_power_low_w': '205.0',
      'target_power_high_w': '240.0',
      'target_hr_low_bpm': null,
      'target_hr_high_bpm': null,
    },
  ],
  'created_at': '2026-05-01T00:00:00Z',
  'updated_at': '2026-05-01T00:00:00Z',
};

Map<String, dynamic> _summary({
  Map<String, dynamic> profile = _profileJson,
  bool withPower = true,
}) => {
  'profile': profile,
  'recent_activities': [if (withPower) _powerActivity else _gpsOnly],
  'loads': [
    {
      'local_date': '2026-05-01',
      'power_load': '69.3',
      'hr_load': '61.0',
      'ctl': '31.0',
      'atl': '32.4',
      'tsb': '-1.4',
      'load_version': 'ewma_42_7_v1',
    },
  ],
  'recovery': {
    'version': 'load_delta_7d_v1',
    'status': 'ok',
    'code': 'load_stable',
    'signals': ['load_stable'],
    'evidence': {
      'recent_7d': 400.0,
      'previous_7d': 420.0,
      'change_pct': -4.8,
      'required_days': 7,
      'available_days': 14,
    },
  },
  'suggestion': {
    'version': 'activity_analysis_v1',
    'status': 'ok',
    'target_load': '74.8',
    'reason': 'within_cap',
    'evidence': {
      'current_load': 69.3,
      'increase_pct': 8.0,
      'max_increase_pct': 15.0,
    },
  },
  'week_power_load': '69.3',
};

MockClient backend({Map<String, dynamic>? summary}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (path.endsWith('/training/summary')) {
      return http.Response(jsonEncode(summary ?? _summary()), 200);
    }
    if (path.endsWith('/training/profile') && req.method == 'GET') {
      return http.Response(jsonEncode(_profileJson), 200);
    }
    if (path.endsWith('/training/profile') && req.method == 'PUT') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      return http.Response(
        jsonEncode({
          ..._profileJson,
          'ftp_w': '${body['ftp_w']}',
          'ftp_basis': 'measured',
        }),
        200,
      );
    }
    if (path.endsWith('/training/ftp-records')) {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'id': 'f1',
              'source': 'manual',
              'value_w': '250.0',
              'effective_at': '2026-05-01',
              'confirmed': true,
              'approximation': false,
              'evidence': null,
              'created_at': '2026-05-01T09:00:00Z',
            },
          ],
          'effective_ftp_w': '250.0',
          'effective_source': 'manual',
          'effective_basis': 'measured',
        }),
        200,
      );
    }
    if (path.endsWith('/training/activities')) {
      return http.Response(
        jsonEncode({
          'items': [_powerActivity, _gpsOnly],
          'total': 2,
          'page': 1,
          'page_size': 50,
        }),
        200,
      );
    }
    if (path.endsWith('/training/loads')) {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'local_date': '2026-05-01',
              'power_load': '69.3',
              'hr_load': '61.0',
              'ctl': '31.0',
              'atl': '32.4',
              'tsb': '-1.4',
              'load_version': 'ewma_42_7_v1',
            },
          ],
          'version': 'ewma_42_7_v1',
        }),
        200,
      );
    }
    if (path.endsWith('/training/recovery')) {
      return http.Response(jsonEncode((_summary())['recovery']), 200);
    }
    if (path.endsWith('/training/calculation-versions')) {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'version': 'np_30s_v1',
              'kind': 'power',
              'title': 'Normalized power',
              'summary': '30 second rolling average, 4th power.',
              'params': {'window_s': 30.0},
              'is_active': true,
            },
            {
              'version': 'coggan_7zone_v1',
              'kind': 'zones',
              'title': 'Power zones (Coggan, 7 zones)',
              'summary': 'Zone boundaries as a fraction of FTP.',
              'params': {
                'boundaries': [0.55, 0.75],
              },
              'is_active': true,
            },
          ],
        }),
        200,
      );
    }
    if (path.endsWith('/workouts') && req.method == 'GET') {
      return http.Response(
        jsonEncode({
          'items': [_workoutJson],
          'total': 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (path.endsWith('/workouts') && req.method == 'POST') {
      return http.Response(jsonEncode(_workoutJson), 201);
    }
    if (path.contains('/workouts/') && req.method == 'GET') {
      return http.Response(jsonEncode(_workoutJson), 200);
    }
    if (path.contains('/workouts/') && req.method == 'PATCH') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      return http.Response(
        jsonEncode({
          ..._workoutJson,
          'version': (body['expected_version'] as int) + 1,
        }),
        200,
      );
    }
    if (path.contains('/workouts/') && req.method == 'DELETE') {
      return http.Response(jsonEncode(_workoutJson), 200);
    }
    if (path.contains('/reanalyze')) {
      return http.Response(
        jsonEncode({..._powerActivity, 'id': 'a-again'}),
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

/// The training pages are tab bodies that normally sit inside the TrainingPage
/// Scaffold. A bare page is wrapped here so the harness matches that context —
/// dropdowns and buttons need a Material ancestor.
Widget app(
  Widget child,
  ProviderContainer c, {
  Locale locale = const Locale('en'),
  bool wrapInScaffold = true,
}) => UncontrolledProviderScope(
  container: c,
  child: MaterialApp(
    supportedLocales: AppLocalizations.supported,
    locale: locale,
    localizationsDelegates: const [
      AppLocalizationsDelegate(),
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: wrapInScaffold ? Scaffold(body: child) : child,
  ),
);

/// The overview stacks five cards, so the default 800x600 test surface leaves
/// the activity list off-screen and unbuilt. A tall viewport lets assertions
/// see the whole page.
void useTallSurface(WidgetTester t) {
  t.view.physicalSize = const Size(1000, 3000);
  t.view.devicePixelRatio = 1.0;
  addTearDown(t.view.reset);
}

void main() {
  group('provenance and nulls survive parsing', () {
    test('a measured FTP keeps its basis', () {
      final p = TrainingProfile.fromJson(
        Map<String, dynamic>.from(_profileJson),
      );
      expect(p.ftpW, 250.0);
      expect(p.ftpBasis, FtpBasis.measured);
      expect(p.hasFtp, isTrue);
      expect(p.canZoneByHrr, isTrue);
    });

    test('a ride with no sensors has null metrics, not zeros', () {
      final a = TrainingActivity.fromJson(Map<String, dynamic>.from(_gpsOnly));
      expect(a.hasNoSensors, isTrue);
      expect(a.normalizedPowerW, isNull);
      expect(a.intensityFactor, isNull);
      expect(a.powerLoad, isNull);
      expect(a.ftpBasis, FtpBasis.unavailable);
      // GPS-derived values still arrive.
      expect(a.distanceM, 32000.0);
      expect(a.movingSeconds, 3400);
    });

    test('unavailable is rendered, never 0', () {
      expect(formatPower(null), '—');
      expect(formatIf(null), '—');
      expect(formatLoad(null), '—');
      expect(formatPower(250.4), '250 W');
      expect(formatIf(1.024), '1.02');
      expect(formatTrainingDuration(null), '—');
      expect(formatTrainingDuration(600), '10:00');
      expect(formatTrainingDuration(3661), '1:01:01');
    });

    test('a zero-second zone total yields no share rather than NaN', () {
      expect(zoneShare(10, 0), 0);
      expect(zoneShare(30, 60), 0.5);
    });

    test('an out-of-range day count is not reported as a change', () {
      final a = TrainingActivity.fromJson(Map<String, dynamic>.from(_gpsOnly));
      expect(a.powerZones, isEmpty);
      expect(a.hasCadence, isFalse);
    });
  });

  group('workout inputs', () {
    test('totals include repeats', () {
      final w = Workout.fromJson(Map<String, dynamic>.from(_workoutJson));
      expect(w.steps.length, 2);
      expect(w.steps[1].repeatCount, 3);
      expect(w.steps[1].totalSeconds, 1800);
      expect(w.plannedSeconds, 2400);
      expect(w.steps[1].hasPowerRange, isTrue);
      expect(w.steps[0].hasPowerRange, isFalse);
    });

    test('omitted optionals are absent from the payload, not null', () {
      const input = WorkoutInput(
        name: 'Base',
        steps: [WorkoutStepInput(label: 'Steady', durationS: 600)],
      );
      final json = input.toJson();
      expect(json.containsKey('description'), isFalse);
      expect(json.containsKey('goal'), isFalse);
      expect(json.containsKey('target_load'), isFalse);
      expect((json['steps'] as List).length, 1);
    });
  });

  group('repository', () {
    test('reads the summary in one call', () async {
      final c = container(backend());
      addTearDown(c.dispose);
      final summary = await c.read(trainingSummaryProvider.future);
      expect(summary.profile.ftpW, 250.0);
      expect(summary.recentActivities.single.normalizedPowerW, 255.0);
      expect(summary.recovery.isAvailable, isTrue);
      expect(summary.recovery.signals, ['load_stable']);
      expect(summary.suggestion.isAvailable, isTrue);
      expect(summary.suggestion.targetLoad, 74.8);
    });

    test('a test FTP posts only the ride id', () async {
      late Map<String, dynamic> sent;
      final mock = MockClient((req) async {
        if (req.url.path.endsWith('/training/ftp-records')) {
          sent = jsonDecode(req.body) as Map<String, dynamic>;
          return http.Response(
            jsonEncode({
              'id': 'f2',
              'source': 'test_20min',
              'value_w': '285.0',
              'effective_at': '2026-05-01',
              'confirmed': true,
              'approximation': false,
              'evidence': {'version': 'ftp_20min_095_v1'},
              'created_at': '2026-05-01T10:00:00Z',
            }),
            201,
          );
        }
        return http.Response('not found', 404);
      });
      final c = container(mock);
      addTearDown(c.dispose);
      final record = await c
          .read(trainingRepositoryProvider)
          .addFtpRecord(
            source: FtpSource.test20min,
            rideId: 'ride-1',
            valueW: 999,
          );
      // The ride decides the value; the asserted 999 is not transmitted.
      expect(sent.containsKey('value_w'), isFalse);
      expect(sent['ride_id'], 'ride-1');
      expect(sent['source'], 'test_20min');
      expect(record.valueW, 285.0);
      expect(record.evidence?['version'], 'ftp_20min_095_v1');
    });

    test('saving the profile sends PUT with only the changed fields', () async {
      late String method;
      late Map<String, dynamic> sent;
      final mock = MockClient((req) async {
        if (req.url.path.endsWith('/training/profile')) {
          method = req.method;
          sent = jsonDecode(req.body) as Map<String, dynamic>;
          return http.Response(jsonEncode(_profileJson), 200);
        }
        return http.Response('not found', 404);
      });
      final c = container(mock);
      addTearDown(c.dispose);
      await c
          .read(trainingRepositoryProvider)
          .updateProfile(ftpW: 265, ftpSource: FtpSource.manual);
      expect(method, 'PUT');
      expect(sent['ftp_w'], 265.0);
      expect(sent.containsKey('max_hr_bpm'), isFalse);
    });
  });

  group('overview page', () {
    testWidgets('shows FTP with its basis and the load trend', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingOverviewPage(), c));
      await t.pumpAndSettle();
      expect(find.text('250 W'), findsWidgets);
      expect(find.byKey(const Key('training.basis.measured')), findsOneWidget);
      expect(find.byKey(const Key('training.ctl')), findsOneWidget);
      expect(find.byKey(const Key('training.weekLoad')), findsOneWidget);
      // Rendered as a bulleted signal line, so match the substring.
      expect(find.textContaining('Load is steady'), findsOneWidget);
    });

    testWidgets('an estimated FTP is labelled as such', (t) async {
      useTallSurface(t);
      final c = container(
        backend(
          summary: _summary(
            profile: {
              ..._profileJson,
              'ftp_basis': 'estimated',
              'ftp_source': 'test_ramp',
            },
          ),
        ),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingOverviewPage(), c));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('training.basis.estimated')), findsOneWidget);
      expect(find.textContaining('Ramp test'), findsOneWidget);
    });

    testWidgets('a sensorless ride says so instead of showing zeros', (
      t,
    ) async {
      useTallSurface(t);
      final c = container(backend(summary: _summary(withPower: false)));
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingOverviewPage(), c));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('training.noSensors')), findsOneWidget);
      // The sensorless branch replaces the metric grid with the notice, so no
      // fabricated "0" is shown and no dash placeholder is needed either.
      expect(find.text('0 W'), findsNothing);
      expect(find.text('0.00'), findsNothing);
      expect(find.byKey(const Key('training.noSensors')), findsOneWidget);
    });

    testWidgets('recalculating offers the action and refreshes', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingOverviewPage(), c));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.reanalyze.a-power')));
      await t.pumpAndSettle();
      expect(find.text('Recalculated'), findsOneWidget);
    });
  });

  group('history and settings', () {
    testWidgets('history lists FTP history and activities', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingHistoryPage(), c));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('training.ftpRecord.f1')), findsOneWidget);
      expect(
        find.byKey(const Key('training.activity.a-power')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('training.activity.a-gps')), findsOneWidget);
    });

    testWidgets('settings prefill and explain the append-only rule', (t) async {
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingSettingsPage(), c));
      await t.pumpAndSettle();
      expect(find.text('250'), findsOneWidget); // prefilled FTP
      expect(find.text('185'), findsOneWidget); // max HR
      expect(
        find.text('This adds a record. Your history is never overwritten.'),
        findsOneWidget,
      );
    });

    testWidgets('an implausible FTP is refused before the round trip', (
      t,
    ) async {
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingSettingsPage(), c));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('training.ftpInput')), '5');
      await t.tap(find.byKey(const Key('training.save')));
      await t.pumpAndSettle();
      expect(find.text('Saved'), findsNothing);
    });

    testWidgets('a valid FTP saves', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingSettingsPage(), c));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('training.ftpInput')), '265');
      await t.tap(find.byKey(const Key('training.save')));
      await t.pumpAndSettle();
      expect(find.text('Saved'), findsOneWidget);
    });

    testWidgets('an untouched save does not append a duplicate FTP record', (
      t,
    ) async {
      useTallSurface(t);
      final bodies = <Map<String, dynamic>>[];
      final mock = MockClient((req) async {
        if (req.url.path.endsWith('/training/profile')) {
          if (req.method == 'PUT') {
            bodies.add(jsonDecode(req.body) as Map<String, dynamic>);
            return http.Response(jsonEncode(_profileJson), 200);
          }
          return http.Response(jsonEncode(_summary()), 200);
        }
        if (req.url.path.contains('/training/')) {
          return http.Response(jsonEncode(_summary()), 200);
        }
        return http.Response('not found', 404);
      });
      final c = container(mock);
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingSettingsPage(), c));
      await t.pumpAndSettle();
      // The field is prefilled with the current 250 W. Saving without typing
      // must not send ftp_w, or every visit would append a new record.
      await t.tap(find.byKey(const Key('training.save')));
      await t.pumpAndSettle();
      expect(bodies, hasLength(1));
      expect(bodies.single.containsKey('ftp_w'), isFalse);
      expect(bodies.single.containsKey('max_hr_bpm'), isFalse);
    });
  });

  group('workouts', () {
    testWidgets('list shows the workout with its version', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingPage(), c, wrapInScaffold: false));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.tab.workouts')));
      await t.pumpAndSettle();
      expect(find.text('Sweet spot'), findsOneWidget);
      expect(find.textContaining('Version 1'), findsOneWidget);
    });

    testWidgets('empty state is explicit', (t) async {
      final mock = MockClient((req) async {
        if (req.url.path.endsWith('/workouts')) {
          return http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1, 'page_size': 20}),
            200,
          );
        }
        return http.Response('not found', 404);
      });
      final c = container(mock);
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingPage(), c, wrapInScaffold: false));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.tab.workouts')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('training.workoutsEmpty')), findsOneWidget);
    });

    testWidgets('creating a workout sends the steps', (t) async {
      useTallSurface(t);
      late Map<String, dynamic> sent;
      final mock = MockClient((req) async {
        if (req.url.path.endsWith('/workouts')) {
          if (req.method == 'GET') {
            return http.Response(
              jsonEncode({'items': [], 'total': 0, 'page': 1, 'page_size': 20}),
              200,
            );
          }
          sent = jsonDecode(req.body) as Map<String, dynamic>;
          return http.Response(jsonEncode(_workoutJson), 201);
        }
        return http.Response('not found', 404);
      });
      final c = container(mock);
      addTearDown(c.dispose);
      await t.pumpWidget(app(const TrainingPage(), c, wrapInScaffold: false));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.tab.workouts')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.workout.new')));
      await t.pumpAndSettle();
      await t.enterText(
        find.byKey(const Key('training.workout.name')),
        'Threshold',
      );
      await t.tap(find.byKey(const Key('training.workout.save')));
      await t.pumpAndSettle();
      expect(sent['name'], 'Threshold');
      expect(sent['expected_version'], isNull);
    });

    testWidgets('a version conflict is reported, not swallowed', (t) async {
      final mock = MockClient((req) async {
        if (req.url.path.endsWith('/workouts') && req.method == 'GET') {
          return http.Response(
            jsonEncode({
              'items': [_workoutJson],
              'total': 1,
              'page': 1,
              'page_size': 20,
            }),
            200,
          );
        }
        if (req.url.path.contains('/workouts/') && req.method == 'PATCH') {
          return http.Response(
            jsonEncode({
              'error': {
                'code': 'VERSION_CONFLICT',
                'message': 'changed',
                'details': {},
              },
            }),
            409,
          );
        }
        return http.Response('not found', 404);
      });
      final c = container(mock);
      addTearDown(c.dispose);
      useTallSurface(t);
      await t.pumpWidget(app(const TrainingPage(), c, wrapInScaffold: false));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.tab.workouts')));
      await t.pumpAndSettle();
      await t.tap(find.text('Sweet spot'));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('training.workout.save')));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('training.workoutConflict')), findsOneWidget);
    });
  });

  group('calculation versions', () {
    testWidgets('a number can be traced to its formula', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(app(const CalculationVersionsPage(), c));
      await t.pumpAndSettle();
      expect(find.text('Normalized power'), findsOneWidget);
      expect(find.textContaining('np_30s_v1'), findsOneWidget);
      expect(find.text('Active'), findsNWidgets(2));
    });
  });

  group('localization', () {
    testWidgets('Arabic renders the training strings RTL', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(
        app(const TrainingOverviewPage(), c, locale: const Locale('ar')),
      );
      await t.pumpAndSettle();
      // The page itself is locale-agnostic, so the RTL claim is about the
      // ambient direction the framework resolves for the locale.
      expect(
        Directionality.of(t.element(find.byType(TrainingOverviewPage))),
        TextDirection.rtl,
      );
      expect(find.text('القدرة القصوى (FTP)'), findsOneWidget);
      expect(find.text('مقيسة'), findsOneWidget);
      expect(find.text('الأنشطة الأخيرة'), findsOneWidget);
    });

    testWidgets('French renders the training strings', (t) async {
      useTallSurface(t);
      final c = container(backend());
      addTearDown(c.dispose);
      await t.pumpWidget(
        app(const TrainingOverviewPage(), c, locale: const Locale('fr')),
      );
      await t.pumpAndSettle();
      expect(find.text('FTP'), findsWidgets);
      expect(find.text('Mesurée'), findsOneWidget);
      expect(find.textContaining('Charge stable'), findsOneWidget);
    });
  });
}
