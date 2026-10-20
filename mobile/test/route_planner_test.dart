import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/ride/data/ride_database.dart';
import 'package:cyclecoach/features/ride/data/ride_recorder.dart';
import 'package:cyclecoach/features/ride/presentation/ride_providers.dart';
import 'package:drift/native.dart';
import 'package:cyclecoach/features/ride/domain/gps_processor.dart';
import 'package:cyclecoach/features/ride/domain/units.dart';
import 'package:cyclecoach/features/ride/presentation/ride_live_map.dart';
import 'package:cyclecoach/features/ride/presentation/ride_start_page.dart';
import 'package:cyclecoach/features/routes/data/route_planner_repository.dart';
import 'package:cyclecoach/features/routes/domain/route_planner.dart';
import 'package:cyclecoach/features/routes/presentation/route_detail_page.dart';
import 'package:cyclecoach/features/routes/presentation/route_planner_page.dart';
import 'package:cyclecoach/features/routes/presentation/route_planner_providers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:latlong2/latlong.dart';

/// Route planning: real provider graph with a fake transport. Upstream
/// fixtures mirror the verified OSRM/Nominatim shapes; nothing here invents
/// geometry, elevation, or surface data.
void main() {
  group('planner repository', () {
    test('search parses place matches', () async {
      final backend = PlannerBackend();
      final repo = RoutePlannerRepository(_api(backend.client));
      final results = await repo.search('alex kreuz');
      expect(results.length, 2);
      expect(results.first.name, contains('Alexanderplatz'));
      expect(results.first.lat, closeTo(52.5219814, 1e-6));
    });

    test('plan parses geometry, distance, and duration', () async {
      final backend = PlannerBackend();
      final repo = RoutePlannerRepository(_api(backend.client));
      final route = await repo.plan(
        waypoints: const [
          PlannedPoint(lat: 52.517037, lon: 13.38886),
          PlannedPoint(lat: 52.529407, lon: 13.397634),
        ],
      );
      expect(route.points.length, 3);
      expect(route.points.first.lat, closeTo(52.517037, 1e-6));
      expect(route.distanceM, closeTo(1888.0, 0.5));
      expect(route.durationS, closeTo(260.4, 0.5));
    });
  });

  group('planner state', () {
    test('short queries clear without HTTP', () async {
      final backend = PlannerBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      await c.read(routePlannerProvider.notifier).search('ab', target: 'start');
      expect(c.read(routePlannerProvider).search, isNull);
      expect(backend.geocodeCalls, 0);
    });

    test('search error surfaces', () async {
      final backend = PlannerBackend()..failSearch = true;
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      await c
          .read(routePlannerProvider.notifier)
          .search('Berlin', target: 'start');
      final search = c.read(routePlannerProvider).search;
      expect(search, isNotNull);
      expect(search!.hasError, isTrue);
    });

    test('waypoint editing: set, stops, swap, remove, cap', () async {
      final backend = PlannerBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);
      final notifier = c.read(routePlannerProvider.notifier);

      notifier.setStart(const PlanWaypoint(label: 'A', lat: 0, lon: 0));
      notifier.setDestination(const PlanWaypoint(label: 'B', lat: 1, lon: 1));
      expect(c.read(routePlannerProvider).canPlan, isTrue);
      notifier.swap();
      expect(c.read(routePlannerProvider).start!.label, 'B');
      expect(c.read(routePlannerProvider).destination!.label, 'A');
      notifier.swap();

      for (var i = 0; i < 23; i++) {
        expect(
          notifier.addStop(PlanWaypoint(label: 'S$i', lat: 0, lon: 0)),
          isTrue,
        );
      }
      // 2 endpoints + 23 stops = 25: the cap holds.
      expect(
        notifier.addStop(const PlanWaypoint(label: 'X', lat: 0, lon: 0)),
        isFalse,
      );
      notifier.removeStopAt(0);
      expect(c.read(routePlannerProvider).stops.length, 22);
      expect(
        notifier.addStop(const PlanWaypoint(label: 'X', lat: 0, lon: 0)),
        isTrue,
      );
    });

    test('waypoint edits invalidate a calculated route', () async {
      final backend = PlannerBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);
      final notifier = c.read(routePlannerProvider.notifier);
      notifier.setStart(const PlanWaypoint(label: 'A', lat: 52.5, lon: 13.38));
      notifier.setDestination(
        const PlanWaypoint(label: 'B', lat: 52.53, lon: 13.4),
      );
      await notifier.plan();
      expect(c.read(routePlannerProvider).plan!.value, isNotNull);
      notifier.addStop(const PlanWaypoint(label: 'S', lat: 52.51, lon: 13.39));
      expect(c.read(routePlannerProvider).plan, isNull);
    });

    test('plan maps unreachable and offline codes', () async {
      final unreachable = PlannerBackend()..planMode = 'noroute';
      var c = graphContainer(unreachable.client);
      addTearDown(c.dispose);
      var notifier = c.read(routePlannerProvider.notifier);
      notifier.setStart(const PlanWaypoint(label: 'A', lat: 0, lon: 0));
      notifier.setDestination(const PlanWaypoint(label: 'B', lat: 1, lon: 1));
      await notifier.plan();
      var err = c.read(routePlannerProvider).plan!;
      expect(err.hasError, isTrue);

      final offline = PlannerBackend()..planMode = 'offline';
      c = graphContainer(offline.client);
      addTearDown(c.dispose);
      notifier = c.read(routePlannerProvider.notifier);
      notifier.setStart(const PlanWaypoint(label: 'A', lat: 0, lon: 0));
      notifier.setDestination(const PlanWaypoint(label: 'B', lat: 1, lon: 1));
      await notifier.plan();
      err = c.read(routePlannerProvider).plan!;
      expect(err.hasError, isTrue);
    });
  });

  group('planner screen', () {
    testWidgets('renders fields, actions, map, and disabled calculate', (
      t,
    ) async {
      final backend = PlannerBackend();
      await t.pumpWidget(
        plannerHarness(backend, initialLocation: '/routes/plan'),
      );
      await t.pumpAndSettle();

      expect(find.byKey(const Key('planner.start')), findsOneWidget);
      expect(find.byKey(const Key('planner.destination')), findsOneWidget);
      expect(find.byKey(const Key('planner.swap')), findsOneWidget);
      expect(find.byType(FlutterMap), findsOneWidget);
      expect(find.text('Find cycling route'), findsOneWidget);
      expect(
        find.text('Choose a start and destination first.'),
        findsOneWidget,
      );
      expect(find.byType(PolylineLayer), findsNothing);
    });

    testWidgets('debounced search shows results; pick sets the start', (
      t,
    ) async {
      final backend = PlannerBackend();
      await t.pumpWidget(
        plannerHarness(backend, initialLocation: '/routes/plan'),
      );
      await t.pumpAndSettle();

      await t.enterText(find.byKey(const Key('planner.start')), 'Alex');
      await t.pump(const Duration(milliseconds: 400));
      expect(backend.geocodeCalls, 0);
      await t.pump(const Duration(milliseconds: 500));
      await t.pumpAndSettle();
      expect(backend.geocodeCalls, 1);
      expect(find.textContaining('Alexanderplatz'), findsWidgets);

      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Alexanderplatz'),
        ),
      );
      await t.pumpAndSettle();
      expect(backend.geocodeCalls, 1);
    });

    testWidgets('calculate renders route, distance, and no elevation', (
      t,
    ) async {
      final backend = PlannerBackend();
      await t.pumpWidget(
        plannerHarness(backend, initialLocation: '/routes/plan'),
      );
      await t.pumpAndSettle();

      await t.enterText(
        find.byKey(const Key('planner.start')),
        'Alexanderplatz',
      );
      await t.pump(const Duration(milliseconds: 900));
      await t.pumpAndSettle();
      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Alexanderplatz'),
        ),
      );
      await t.pumpAndSettle();
      await t.enterText(
        find.byKey(const Key('planner.destination')),
        'Kreuzberg',
      );
      await t.pump(const Duration(milliseconds: 900));
      await t.pumpAndSettle();
      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Kreuzberg'),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('planner.calculate')));
      await t.pumpAndSettle();

      expect(find.byType(PolylineLayer), findsOneWidget);
      expect(
        find.text(formatDistance(1888.0, imperial: false)),
        findsOneWidget,
      );
      expect(
        find.text('Elevation unavailable for calculated routes'),
        findsOneWidget,
      );
      expect(backend.planCalls, 1);
    });

    testWidgets('unreachable destination shows an honest error', (t) async {
      final backend = PlannerBackend()..planMode = 'noroute';
      await t.pumpWidget(
        plannerHarness(backend, initialLocation: '/routes/plan'),
      );
      await t.pumpAndSettle();

      await t.enterText(
        find.byKey(const Key('planner.start')),
        'Alexanderplatz',
      );
      await t.pump(const Duration(milliseconds: 900));
      await t.pumpAndSettle();
      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Alexanderplatz'),
        ),
      );
      await t.pumpAndSettle();
      await t.enterText(
        find.byKey(const Key('planner.destination')),
        'Kreuzberg',
      );
      await t.pump(const Duration(milliseconds: 900));
      await t.pumpAndSettle();
      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Kreuzberg'),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('planner.calculate')));
      await t.pumpAndSettle();

      expect(
        find.text('No cycling route connects these points.'),
        findsOneWidget,
      );
      expect(find.byType(PolylineLayer), findsNothing);
    });

    testWidgets('save posts planner geometry and opens the detail', (t) async {
      final backend = PlannerBackend();
      await t.pumpWidget(
        plannerHarness(backend, initialLocation: '/routes/plan'),
      );
      await t.pumpAndSettle();

      await t.enterText(
        find.byKey(const Key('planner.start')),
        'Alexanderplatz',
      );
      await t.pump(const Duration(milliseconds: 900));
      await t.pumpAndSettle();
      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Alexanderplatz'),
        ),
      );
      await t.pumpAndSettle();
      await t.enterText(
        find.byKey(const Key('planner.destination')),
        'Kreuzberg',
      );
      await t.pump(const Duration(milliseconds: 900));
      await t.pumpAndSettle();
      await t.tap(
        find.descendant(
          of: find.byType(ListTile),
          matching: find.textContaining('Kreuzberg'),
        ),
      );
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('planner.calculate')));
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('planner.save')));
      await t.pumpAndSettle();
      await t.enterText(
        find.byKey(const Key('planner.save.name')),
        'Planned Loop',
      );
      await t.tap(find.text('Save route').last);
      await t.pumpAndSettle();

      expect(backend.savedName, 'Planned Loop');
      expect(backend.savedPoints, 3);
      expect(find.text('detail-stub:r9'), findsOneWidget);
    });

    testWidgets('arabic renders RTL', (t) async {
      final backend = PlannerBackend();
      await t.pumpWidget(
        plannerHarness(
          backend,
          initialLocation: '/routes/plan',
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('خطط لمسار'), findsOneWidget);
      final ctx = t.element(find.byKey(const Key('planner.calculate')));
      expect(Directionality.of(ctx), TextDirection.rtl);
    });
  });

  group('ride from a saved route', () {
    testWidgets('detail offers ride-this-route into the start flow', (t) async {
      final backend = PlannerBackend();
      await t.pumpWidget(detailHarness(backend, 'r1'));
      await t.pumpAndSettle();

      expect(find.text('Lake Loop'), findsOneWidget);
      // The action row sits below the fold; scroll down to it.
      await t.drag(find.text('Lake Loop'), const Offset(0, -900));
      await t.pump();
      await t.pump(const Duration(milliseconds: 300));

      expect(find.text('Ride this route'), findsOneWidget);
      await t.tap(find.text('Ride this route'));
      await t.pumpAndSettle();

      expect(find.text('start-stub:route=r1'), findsOneWidget);
    });

    testWidgets('start preselects a readable route, ignores unknown ids', (
      t,
    ) async {
      final backend = PlannerBackend();
      await t.pumpWidget(startHarness(backend, 'r1'));
      await t.pumpAndSettle();

      expect(find.text('Lake Loop'), findsOneWidget);

      await t.pumpWidget(startHarness(backend, 'nope'));
      await t.pumpAndSettle();

      expect(find.text('Lake Loop'), findsNothing);
    });
  });

  group('geometry separation', () {
    testWidgets('planned and recorded layers stay distinct', (t) async {
      await t.pumpWidget(
        MaterialApp(
          locale: const Locale('en'),
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: Scaffold(
            body: SizedBox(
              height: 400,
              child: RideLiveMap(
                live: _liveWithTrack(),
                recording: true,
                planned: const [LatLng(46.2, 6.14), LatLng(46.202, 6.142)],
              ),
            ),
          ),
        ),
      );
      await t.pumpAndSettle();

      final layer = t.widget<PolylineLayer>(find.byType(PolylineLayer));
      expect(layer.polylines.length, 2);
      final patterns = layer.polylines.map((p) => p.pattern).toSet();
      expect(patterns.length, 2);
    });
  });

  group('planner localization', () {
    test('planner keys resolve in en/fr/ar', () {
      const keys = [
        'planner.title',
        'planner.start',
        'planner.destination',
        'planner.searchHint',
        'planner.swap',
        'planner.addStop',
        'planner.tapHint',
        'planner.searching',
        'planner.noResults',
        'planner.searchError',
        'planner.plan',
        'planner.planning',
        'planner.unreachable',
        'planner.planFailed',
        'planner.duration',
        'planner.elevationUnavailable',
        'planner.save',
        'planner.routeName',
        'planner.rideThisRoute',
        'planner.stops',
        'planner.removeStop',
        'planner.needTwoPoints',
      ];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = AppLocalizations(Locale(lang));
        for (final key in keys) {
          expect(loc.get(key), isNotEmpty, reason: '$key missing in $lang');
          expect(loc.get(key), isNot(key), reason: '$key untranslated');
        }
      }
    });
  });
}

LiveRide _liveWithTrack() {
  return const LiveRide(
    localId: 'l',
    bikeId: 'b',
    bikeName: 'Road',
    phase: RecordingPhase.recording,
    metrics: RideMetrics(
      distanceM: 100,
      gainM: 5,
      lossM: 0,
      movingS: 60,
      elapsedS: 70,
      avgSpeedMS: 1.6,
      maxSpeedMS: 3.0,
    ),
    gpsQuality: 'good',
    syncState: 'idle',
    track: [(lat: 33.0, lon: -6.0), (lat: 33.00009, lon: -6.0)],
  );
}

/// Production provider wiring with a fake transport: token storage and HTTP
/// are faked, everything else is real. A valid session is pre-seeded.
ProviderContainer graphContainer(MockClient backend) {
  final tokens = MemoryTokenStorage()
    ..access = 'valid-access'
    ..refresh = 'valid-refresh';
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      httpClientProvider.overrideWithValue(backend),
      rideDatabaseProvider.overrideWith(
        (ref) async => RideDatabase.forTesting(NativeDatabase.memory()),
      ),
    ],
  );
}

ApiClient _api(MockClient backend) => ApiClient(
  baseUrl: 'http://test',
  client: backend,
  accessToken: () async => 'tok',
);

Widget _scoped(
  ProviderContainer container,
  GoRouter router, {
  Locale locale = const Locale('en'),
}) {
  return UncontrolledProviderScope(
    container: container,
    child: MaterialApp.router(
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      routerConfig: router,
    ),
  );
}

Widget plannerHarness(
  PlannerBackend backend, {
  required String initialLocation,
  Locale locale = const Locale('en'),
}) {
  final container = graphContainer(backend.client);
  final router = GoRouter(
    initialLocation: initialLocation,
    routes: [
      GoRoute(
        path: '/routes/plan',
        builder: (c, s) => const RoutePlannerPage(),
      ),
      GoRoute(
        path: '/routes/:id',
        builder: (c, s) => Text('detail-stub:${s.pathParameters['id']}'),
      ),
    ],
  );
  return _scoped(container, router, locale: locale);
}

Widget detailHarness(PlannerBackend backend, String routeId) {
  final container = graphContainer(backend.client);
  final router = GoRouter(
    initialLocation: '/routes/$routeId',
    routes: [
      GoRoute(
        path: '/routes/:id',
        builder: (c, s) => RouteDetailPage(routeId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/ride/start',
        builder: (c, s) =>
            Text('start-stub:route=${s.uri.queryParameters['route']}'),
      ),
    ],
  );
  return _scoped(container, router);
}

Widget startHarness(PlannerBackend backend, String? routeId) {
  final container = graphContainer(backend.client);
  final location = routeId == null
      ? '/ride/start'
      : '/ride/start?route=$routeId';
  final router = GoRouter(
    initialLocation: location,
    routes: [
      GoRoute(
        path: '/ride/start',
        builder: (c, s) =>
            RideStartPage(initialRouteId: s.uri.queryParameters['route']),
      ),
    ],
  );
  return _scoped(container, router);
}

/// Scriptable planner backend: geocode, plan, save, detail, versions,
/// geometry, bikes, and route list. Failure modes per surface.
class PlannerBackend {
  bool failSearch = false;
  bool offlineSearch = false;
  String planMode = 'ok'; // ok | noroute | offline | malformed
  int geocodeCalls = 0;
  int planCalls = 0;
  String? savedName;
  int savedPoints = 0;

  late final MockClient client = MockClient((req) async {
    final path = req.url.path;
    if (path == '/api/v1/routes/geocode') {
      geocodeCalls++;
      if (offlineSearch) throw const SocketFailure();
      if (failSearch) {
        return http.Response('{"error":{"code":"X","message":"boom"}}', 500);
      }
      final q = req.url.queryParameters['q'] ?? '';
      final alex = q.toLowerCase().contains('alex');
      final kreuz = q.toLowerCase().contains('kreuz');
      final hits = [
        if (alex)
          {
            'name': 'Alexanderplatz, Berlin, Germany',
            'lat': 52.5219814,
            'lon': 13.4112804,
          },
        if (kreuz)
          {
            'name': 'Kreuzberg, Berlin, Germany',
            'lat': 52.499008,
            'lon': 13.403321,
          },
      ];
      return http.Response(jsonEncode({'query': q, 'results': hits}), 200);
    }
    if (path == '/api/v1/routes/plan') {
      planCalls++;
      if (planMode == 'offline') throw const SocketFailure();
      if (planMode == 'noroute') {
        return http.Response(
          '{"error":{"code":"ROUTE_UNREACHABLE","message":"sea",'
          '"details":{"code":"ROUTE_UNREACHABLE","message":"sea"}}}',
          422,
        );
      }
      if (planMode == 'malformed') {
        return http.Response('{"code":"Ok"}', 200);
      }
      return http.Response(
        jsonEncode({
          'points': [
            {'lat': 52.517037, 'lon': 13.38886},
            {'lat': 52.52, 'lon': 13.39},
            {'lat': 52.529407, 'lon': 13.397634},
          ],
          'distance_m': 1888.0,
          'duration_s': 260.4,
          'profile': 'cycling',
        }),
        200,
      );
    }
    if (path == '/api/v1/routes' && req.method == 'POST') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      savedName = body['name'] as String?;
      savedPoints = (body['points'] as List).length;
      return http.Response(
        jsonEncode({..._routeJson, 'id': 'r9', 'name': savedName}),
        201,
      );
    }
    if (path == '/api/v1/routes' && req.method == 'GET') {
      return http.Response(
        jsonEncode({
          'items': [_routeJson],
          'total': 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (path == '/api/v1/routes/r1' && req.method == 'GET') {
      final withGeometry =
          req.url.queryParameters['include_geometry'] == 'true';
      return http.Response(
        jsonEncode({
          ..._routeJson,
          if (withGeometry) 'geometry': _geometryJson,
        }),
        200,
      );
    }
    if (path.endsWith('/geometry')) {
      return http.Response(jsonEncode(_geometryJson), 200);
    }
    if (path.endsWith('/versions')) {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'id': 'v2',
              'version_no': 2,
              'point_count': 3,
              'distance_m': 1234.5,
              'elevation_gain_m': 40.0,
              'estimated_duration_s': 180,
              'difficulty': 'easy',
              'changelog': 'extend',
              'created_at': '2026-01-02T00:00:00Z',
            },
          ],
        }),
        200,
      );
    }
    if (path == '/api/v1/bikes') {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'id': 'bike-1',
              'name': 'Road',
              'category': 'road',
              'status': 'active',
            },
          ],
          'total': 1,
        }),
        200,
      );
    }
    return http.Response('not found', 404);
  });

  static const _routeJson = {
    'id': 'r1',
    'name': 'Lake Loop',
    'description': 'Flat laps',
    'activity_type': 'road',
    'privacy': 'private',
    'status': 'active',
    'source': 'manual',
    'current_version': 2,
    'distance_m': 1234.5,
    'elevation_gain_m': 40.0,
    'elevation_loss_m': 38.0,
    'highest_point_m': 460.0,
    'lowest_point_m': 400.0,
    'estimated_duration_s': 180,
    'difficulty': 'easy',
    'point_count': 3,
    'start_lat': 46.2,
    'start_lon': 6.14,
    'end_lat': 46.202,
    'end_lon': 6.142,
    'created_at': '2026-01-01T00:00:00Z',
    'updated_at': '2026-01-02T00:00:00Z',
  };

  static const _geometryJson = {
    'route_id': 'r1',
    'version_no': 2,
    'point_count': 3,
    'points': [
      {'seq': 0, 'lat': 46.2, 'lon': 6.14, 'ele': 400.0},
      {'seq': 1, 'lat': 46.201, 'lon': 6.141, 'ele': 420.0},
      {'seq': 2, 'lat': 46.202, 'lon': 6.142, 'ele': 410.0},
    ],
    'elevation_profile': [
      [0.0, 400.0],
      [150.0, 420.0],
    ],
  };
}

class SocketFailure implements Exception {
  const SocketFailure();
}
