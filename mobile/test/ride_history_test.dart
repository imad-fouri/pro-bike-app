import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/domain/auth_user.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/home/home_page.dart';
import 'package:cyclecoach/features/ride/data/ride_database.dart';
import 'package:cyclecoach/features/ride/domain/ride_history.dart';
import 'package:cyclecoach/features/ride/domain/units.dart';
import 'package:cyclecoach/features/ride/presentation/history_providers.dart';
import 'package:cyclecoach/features/ride/presentation/ride_providers.dart';
import 'package:cyclecoach/features/subscriptions/domain/entitlement.dart';
import 'package:cyclecoach/features/subscriptions/presentation/entitlement_providers.dart';
import 'package:cyclecoach/features/ride/presentation/ride_detail_page.dart';
import 'package:cyclecoach/features/ride/presentation/ride_history_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:drift/native.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Ride history, detail, and dashboard: real provider graph with a fake
/// transport, no live network. Decimals arrive as strings (like some
/// encoders emit) to prove the parsers never assume JSON numbers.
void main() {
  group('models tolerate both encodings', () {
    test('history item parses string decimals + missing optionals', () {
      final item = RideHistoryItem.fromJson(
        _rideJson(id: 'r-1', distance: '20.5'),
      );
      expect(item.id, 'r-1');
      expect(item.distanceM, 20.5);
      expect(item.movingSeconds, 10);
      expect(item.isCompleted, isTrue);
      expect(item.routeId, isNull);
      expect(item.endedAt, isNotNull);
    });

    test('overview parses with nullable windows', () {
      final o = RidesOverview.fromJson(_overviewJson());
      expect(o.totalRides, 2);
      expect(o.totalDistanceM, 40.0);
      expect(o.weekDistanceM, 40.0);
      expect(o.isEmpty, isFalse);
      expect(o.weekStart, isNotNull);
    });

    test('track point tolerates missing altitude', () {
      final p = RideTrackPoint.fromJson({'seq': 3, 'lat': 33.0, 'lon': -6.0});
      expect(p.seq, 3);
      expect(p.alt, isNull);
    });
  });

  group('history provider', () {
    test('first page loads newest first', () async {
      final backend = HistoryBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      final state = await c.read(rideHistoryProvider.future);

      expect(state.total, 2);
      expect(state.items.map((e) => e.id), ['r-2', 'r-1']);
      expect(backend.historyCalls, 1);
    });

    test('loadMore appends and stops at the end', () async {
      final backend = HistoryBackend(rideCount: 21);
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      var state = await c.read(rideHistoryProvider.future);
      expect(state.items.length, 20);
      expect(state.hasMore, isTrue);
      await c.read(rideHistoryProvider.notifier).loadMore();
      state = c.read(rideHistoryProvider).value!;
      expect(state.items.length, 21);
      expect(state.hasMore, isFalse);
      expect(backend.historyCalls, 2);
    });

    test('refresh re-reads page one', () async {
      final backend = HistoryBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      await c.read(rideHistoryProvider.future);
      backend.allRides.removeAt(0);
      await c.read(rideHistoryProvider.notifier).refresh();

      final state = c.read(rideHistoryProvider).value!;
      expect(state.total, 1);
      expect(state.items.map((e) => e.id), ['r-1']);
    });

    test('server failure surfaces as an error', () async {
      final backend = HistoryBackend()..failHistory = true;
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      await expectLater(
        c.read(rideHistoryProvider.future),
        throwsA(isA<ApiException>()),
      );
    });

    test('network failure keeps its code for the offline UI', () async {
      final backend = HistoryBackend()..offlineHistory = true;
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      Object? caught;
      try {
        await c.read(rideHistoryProvider.future);
        fail('expected an error');
      } on Object catch (e) {
        caught = e;
      }
      expect((caught as ApiException).code, 'NETWORK_ERROR');
    });
  });

  group('detail + overview providers', () {
    test('detail combines header and trail', () async {
      final backend = HistoryBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      final detail = await c.read(rideDetailProvider('r-1').future);

      expect(detail.ride.id, 'r-1');
      expect(detail.points.map((e) => e.seq), [0, 1, 2]);
      expect(detail.truncated, isFalse);
    });

    test('unknown ride surfaces the 404', () async {
      final backend = HistoryBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      Object? caught;
      try {
        await c.read(rideDetailProvider('missing').future);
        fail('expected an error');
      } on Object catch (e) {
        caught = e;
      }
      expect((caught as ApiException).status, 404);
    });

    test('overview values reconcile with the history', () async {
      final backend = HistoryBackend();
      final c = graphContainer(backend.client);
      addTearDown(c.dispose);

      final overview = await c.read(ridesOverviewProvider.future);
      final history = await c.read(rideHistoryProvider.future);
      final sum = history.items.fold<double>(0, (a, e) => a + e.distanceM);

      expect(overview.totalRides, history.total);
      expect(overview.totalDistanceM, closeTo(sum, 0.01));
    });
  });

  group('history screen', () {
    testWidgets('renders items and navigates to detail', (t) async {
      final backend = HistoryBackend();
      await t.pumpWidget(historyHarness(backend, initialLocation: '/rides'));
      await t.pumpAndSettle();

      expect(find.textContaining('2026-05-'), findsNWidgets(2));
      expect(
        find.text(formatDistance(20.0, imperial: false)),
        findsNWidgets(2),
      );
      await t.tap(find.textContaining('2026-05-').first);
      await t.pumpAndSettle();

      expect(find.text('detail-stub:r-2'), findsOneWidget);
    });

    testWidgets('empty history guides to recording', (t) async {
      final backend = HistoryBackend()..allRides.clear();
      await t.pumpWidget(historyHarness(backend, initialLocation: '/rides'));
      await t.pumpAndSettle();

      expect(find.text('No rides yet'), findsOneWidget);
      expect(
        find.text('Record your first ride to start your history.'),
        findsOneWidget,
      );
    });

    testWidgets('error state retries into data', (t) async {
      final backend = HistoryBackend()..failHistory = true;
      await t.pumpWidget(historyHarness(backend, initialLocation: '/rides'));
      await t.pumpAndSettle();

      expect(find.text('Could not load rides.'), findsOneWidget);
      backend.failHistory = false;
      await t.tap(find.text('Retry'));
      await t.pumpAndSettle();

      expect(find.textContaining('2026-05-'), findsNWidgets(2));
    });

    testWidgets('arabic renders RTL', (t) async {
      final backend = HistoryBackend();
      await t.pumpWidget(
        historyHarness(
          backend,
          initialLocation: '/rides',
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();

      expect(find.text('سجل الجولات'), findsOneWidget);
      final ctx = t.element(find.text('سجل الجولات'));
      expect(Directionality.of(ctx), TextDirection.rtl);
    });
  });

  group('detail screen', () {
    testWidgets('renders map, metrics, and bike', (t) async {
      final backend = HistoryBackend();
      await t.pumpWidget(detailHarness(backend, 'r-1'));
      await t.pumpAndSettle();

      expect(find.text('Ride Details'), findsOneWidget);
      expect(find.text(formatDistance(20.0, imperial: false)), findsOneWidget);
      expect(find.text('Road'), findsOneWidget);
      // Map with the recorded trail (3 accepted fixes).
      expect(find.byType(RideDetailPage), findsOneWidget);
    });

    testWidgets('unknown ride shows not-found with a way back', (t) async {
      final backend = HistoryBackend();
      await t.pumpWidget(detailHarness(backend, 'missing'));
      await t.pumpAndSettle();

      expect(find.text('Ride not found.'), findsOneWidget);
      await t.tap(find.text('Ride History'));
      await t.pumpAndSettle();

      expect(find.text('Ride History'), findsOneWidget);
    });
  });

  group('dashboard', () {
    testWidgets('shows real aggregates and recent rides', (t) async {
      final backend = HistoryBackend();
      await t.pumpWidget(homeHarness(backend));
      await t.pumpAndSettle();

      expect(
        find.text(formatDistance(40.0, imperial: false)),
        findsNWidgets(2),
      );
      expect(find.text('2'), findsOneWidget);
      expect(find.text('Recent Rides'), findsOneWidget);
      // No demo numbers anywhere.
      expect(find.text('180'), findsNothing);
      expect(find.text('2,400'), findsNothing);
    });

    testWidgets('empty account gets guidance, not zeros-as-data', (t) async {
      final backend = HistoryBackend()
        ..allRides.clear()
        ..overview = _overviewJson(totalRides: 0, distance: '0');
      await t.pumpWidget(homeHarness(backend));
      await t.pumpAndSettle();

      expect(find.text('No completed rides yet'), findsOneWidget);
      expect(
        find.text('Finish a ride and it will show up here.'),
        findsOneWidget,
      );
    });

    testWidgets('failed aggregates offer retry', (t) async {
      final backend = HistoryBackend()..failOverview = true;
      await t.pumpWidget(homeHarness(backend));
      await t.pumpAndSettle();

      expect(find.text('Could not load rides.'), findsOneWidget);
      backend.failOverview = false;
      await t.tap(find.text('Retry'));
      await t.pumpAndSettle();

      expect(find.text('Recent Rides'), findsOneWidget);
    });
  });
}

/// Production provider wiring with a fake transport: token storage and HTTP
/// are faked, everything else is real. A valid session is pre-seeded so
/// authenticated requests reach the fake backend.
ProviderContainer graphContainer(
  MockClient backend, {
  bool fixedAuth = false,
  bool memoryDb = false,
  bool fixedEntitlement = false,
}) {
  final tokens = MemoryTokenStorage()
    ..access = 'valid-access'
    ..refresh = 'valid-refresh';
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      httpClientProvider.overrideWithValue(backend),
      if (fixedAuth) authProvider.overrideWith(() => _FixedAuth()),
      // Real on-device database needs platform channels; the in-memory
      // database keeps the recovery probe honest without them.
      if (memoryDb)
        rideDatabaseProvider.overrideWith(
          (ref) async => RideDatabase.forTesting(NativeDatabase.memory()),
        ),
      if (fixedEntitlement)
        entitlementProvider.overrideWith(() => _FixedEntitlement()),
    ],
  );
}

Widget historyHarness(
  HistoryBackend backend, {
  required String initialLocation,
  Locale locale = const Locale('en'),
}) {
  final container = graphContainer(backend.client);
  final router = GoRouter(
    initialLocation: initialLocation,
    routes: [
      GoRoute(path: '/rides', builder: (c, s) => const RideHistoryPage()),
      GoRoute(
        path: '/rides/:id',
        builder: (c, s) => Text('detail-stub:${s.pathParameters['id']}'),
      ),
      GoRoute(path: '/ride/start', builder: (c, s) => const Text('start-stub')),
    ],
  );
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

Widget detailHarness(HistoryBackend backend, String rideId) {
  final container = graphContainer(backend.client);
  final router = GoRouter(
    initialLocation: '/rides/$rideId',
    routes: [
      GoRoute(path: '/rides', builder: (c, s) => const RideHistoryPage()),
      GoRoute(
        path: '/rides/:id',
        builder: (c, s) => RideDetailPage(rideId: s.pathParameters['id']!),
      ),
    ],
  );
  return UncontrolledProviderScope(
    container: container,
    child: MaterialApp.router(
      locale: const Locale('en'),
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

Widget homeHarness(HistoryBackend backend) {
  final container = graphContainer(
    backend.client,
    fixedAuth: true,
    memoryDb: true,
    fixedEntitlement: true,
  );
  final router = GoRouter(
    initialLocation: '/home',
    routes: [
      GoRoute(path: '/home', builder: (c, s) => const HomePage()),
      GoRoute(path: '/rides', builder: (c, s) => const Text('rides-stub')),
      GoRoute(path: '/rides/:id', builder: (c, s) => const Text('detail-stub')),
      GoRoute(
        path: '/training',
        builder: (c, s) => const Text('training-stub'),
      ),
      GoRoute(
        path: '/performance',
        builder: (c, s) => const Text('performance-stub'),
      ),
      GoRoute(path: '/profile', builder: (c, s) => const Text('profile-stub')),
      GoRoute(
        path: '/notifications',
        builder: (c, s) => const Text('notifications-stub'),
      ),
      GoRoute(path: '/ride/start', builder: (c, s) => const Text('start-stub')),
    ],
  );
  return UncontrolledProviderScope(
    container: container,
    child: MaterialApp.router(
      locale: const Locale('en'),
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

Map<String, dynamic> _rideJson({
  required String id,
  Object distance = 20.0,
  int moving = 10,
}) {
  return {
    'id': id,
    'bike_id': 'bike-1',
    'status': 'completed',
    'started_at': '2026-05-01T12:00:00Z',
    'ended_at': '2026-05-01T12:30:00Z',
    'summary': {
      'distance_m': '$distance',
      'elevation_gain_m': '0',
      'elevation_loss_m': '0',
      'moving_seconds': moving,
      'elapsed_seconds': 1800,
      'average_speed_m_s': '2.0',
      'max_speed_m_s': '2.5',
      'accepted_points': 3,
    },
    'start_lat': '33.0',
    'start_lon': '-6.0',
    'end_lat': '33.00018',
    'end_lon': '-6.0',
    'route_id': null,
    'route_version': null,
    'integrity_status': 'accepted',
  };
}

Map<String, dynamic> _overviewJson({
  int totalRides = 2,
  String distance = '40',
}) {
  return {
    'total_rides': totalRides,
    'total_distance_m': distance,
    'total_moving_seconds': 20,
    'total_elevation_gain_m': '0',
    'week_distance_m': distance,
    'month_distance_m': distance,
    'week_start': '2026-04-27T00:00:00Z',
    'month_start': '2026-05-01T00:00:00Z',
  };
}

class _FixedAuth extends AuthNotifier {
  static const user = AuthUser(
    id: 'u',
    email: 'r@e.com',
    displayName: 'R',
    emailVerified: false,
  );

  @override
  AuthState build() => AuthState(AuthStatus.authenticated, user: user);
}

/// Free-account entitlement: the dashboard never displays it, so the ad
/// chain stays out of these tests (its live fetch would need a full
/// entitlement fixture for zero dashboard value).
class _FixedEntitlement extends EntitlementNotifier {
  @override
  Future<EntitlementState?> build() async => null;
}

/// Scriptable backend: history newest-first, detail, points, overview,
/// notifications count, and one bike. Failure flags per surface.
class HistoryBackend {
  HistoryBackend({int rideCount = 2}) {
    allRides = [for (var i = rideCount; i >= 1; i--) _rideJson(id: 'r-$i')];
  }

  late List<Map<String, dynamic>> allRides;
  Map<String, dynamic> overview = _overviewJson();
  bool failHistory = false;
  bool offlineHistory = false;
  bool failOverview = false;
  int historyCalls = 0;

  late final MockClient client = MockClient((req) async {
    final path = req.url.path;
    if (path == '/api/v1/rides') {
      historyCalls++;
      if (offlineHistory) throw const SocketFailure();
      if (failHistory) {
        return http.Response('{"error":{"code":"X","message":"boom"}}', 500);
      }
      final page = int.tryParse(req.url.queryParameters['page'] ?? '1') ?? 1;
      final size =
          int.tryParse(req.url.queryParameters['page_size'] ?? '20') ?? 20;
      final start = (page - 1) * size;
      final items = allRides.skip(start).take(size).toList();
      return http.Response(
        jsonEncode({
          'items': items,
          'total': allRides.length,
          'page': page,
          'page_size': size,
        }),
        200,
      );
    }
    if (path == '/api/v1/rides/stats/overview') {
      if (failOverview) {
        return http.Response('{"error":{"code":"X","message":"boom"}}', 500);
      }
      return http.Response(jsonEncode(overview), 200);
    }
    if (path.endsWith('/points')) {
      final id = req.url.pathSegments[req.url.pathSegments.length - 2];
      final match = allRides.where((r) => r['id'] == id).toList();
      if (match.isEmpty) return http.Response('not found', 404);
      return http.Response(
        '{"points":[{"seq":0,"lat":33.0,"lon":-6.0},'
        '{"seq":1,"lat":33.00009,"lon":-6.0},'
        '{"seq":2,"lat":33.00018,"lon":-6.0}],'
        '"total":3,"truncated":false}',
        200,
      );
    }
    if (path.startsWith('/api/v1/rides/')) {
      final id = req.url.pathSegments.last;
      final match = allRides.where((r) => r['id'] == id).toList();
      if (match.isEmpty) return http.Response('not found', 404);
      return http.Response(jsonEncode(match.first), 200);
    }
    if (path.endsWith('/notifications/unread-count')) {
      return http.Response('{"unread_count":0}', 200);
    }
    if (path == '/api/v1/bikes') {
      return http.Response(
        '{"items":[{"id":"bike-1","name":"Road","category":"road",'
        '"status":"active"}],"total":1}',
        200,
      );
    }
    return http.Response('not found', 404);
  });
}

class SocketFailure implements Exception {
  const SocketFailure();
}
