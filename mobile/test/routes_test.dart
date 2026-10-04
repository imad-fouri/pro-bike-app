import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/ride/data/ride_database.dart';
import 'package:cyclecoach/features/ride/presentation/ride_providers.dart';
import 'package:cyclecoach/features/routes/data/routes_repository.dart';
import 'package:cyclecoach/features/routes/domain/route.dart';
import 'package:cyclecoach/features/routes/domain/route_validators.dart';
import 'package:cyclecoach/features/routes/presentation/route_detail_page.dart';
import 'package:cyclecoach/features/routes/presentation/route_form_page.dart';
import 'package:cyclecoach/features/routes/presentation/route_list_page.dart';
import 'package:cyclecoach/features/routes/presentation/routes_providers.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _routeJson = {
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

const _geometryJson = {
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

MockClient backend({String mode = 'ok'}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (mode == 'error') {
      return http.Response(
        '{"error":{"code":"ROUTE_NOT_FOUND","message":"Route not found.",'
        '"details":{"code":"ROUTE_NOT_FOUND","message":"Route not found."}}}',
        404,
      );
    }
    if (mode == 'conflict') {
      return http.Response(
        '{"error":{"code":"VERSION_CONFLICT","message":"Stale.",'
        '"details":{"code":"VERSION_CONFLICT","message":"Stale."}}}',
        409,
      );
    }
    if (req.method == 'GET' && path.endsWith('/api/v1/routes')) {
      final empty = req.url.queryParameters['page'] == '9';
      return http.Response(
        jsonEncode({
          'items': empty ? <Map<String, dynamic>>[] : [_routeJson],
          'total': empty ? 0 : 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (path.endsWith('/export/gpx')) {
      return http.Response(
        '<?xml version="1.0"?><gpx><rte/></gpx>',
        200,
        headers: {
          'content-type': 'application/gpx+xml',
          'content-disposition': 'attachment; filename="Lake_Loop.gpx"',
        },
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
    if (path.endsWith('/import/gpx')) {
      expect(req.headers['content-type'], contains('multipart/form-data'));
      expect(utf8.decode(req.bodyBytes), contains('<gpx'));
      return http.Response(
        jsonEncode({
          'route': {..._routeJson, 'id': 'imp', 'source': 'gpx'},
          'imported_points': 3,
          'duplicates_removed': 1,
          'had_timestamps': false,
        }),
        201,
      );
    }
    if (path.endsWith('/api/v1/routes') && req.method == 'POST') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      return http.Response(
        jsonEncode({..._routeJson, 'id': 'r2', 'name': body['name']}),
        201,
      );
    }
    if (path.endsWith('/archive') || path.endsWith('/restore')) {
      final archived = path.endsWith('/archive');
      return http.Response(
        jsonEncode({..._routeJson, 'status': archived ? 'archived' : 'active'}),
        200,
      );
    }
    if (req.method == 'PATCH') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      expect(body['expected_version'], 2);
      return http.Response(
        jsonEncode({..._routeJson, 'name': body['name'] ?? 'Lake Loop'}),
        200,
      );
    }
    if (path.endsWith('/api/v1/routes/r1') && req.method == 'GET') {
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
    if (req.method == 'DELETE') {
      return http.Response(jsonEncode({..._routeJson, 'deleted': true}), 200);
    }
    return http.Response('not found', 404);
  });
}

ApiClient api(MockClient mock) => ApiClient(
  baseUrl: 'http://test',
  client: mock,
  accessToken: () async => 'test-token',
);

void main() {
  test('route model parses and labels metrics without faking data', () {
    final route = AppRoute.fromJson(Map<String, dynamic>.from(_routeJson));
    expect(route.name, 'Lake Loop');
    expect(route.currentVersion, 2);
    expect(route.distanceLabel(imperial: false), '1.2 km');
    expect(route.distanceLabel(imperial: true), contains('mi'));
    expect(route.gainLabel(imperial: false), '+40 m');
    expect(route.durationLabel(), '3m');
    expect(route.difficultyKey, 'routes.difficulty.easy');
    expect(route.privacyKey, 'routes.privacy.private');

    final noEle = AppRoute.fromJson({..._routeJson, 'elevation_gain_m': null});
    expect(noEle.gainLabel(imperial: false), '—'); // unknown, never 0
    expect(
      AppRoute.fromJson({
        ..._routeJson,
        'estimated_duration_s': null,
      }).durationLabel(),
      '—',
    );
  });

  test('geometry round-trips and keeps elevation as data, not noise', () {
    final geometry = RouteGeometry.fromJson(
      Map<String, dynamic>.from(_geometryJson),
    );
    expect(geometry.versionNo, 2);
    expect(geometry.points.length, 3);
    expect(geometry.points.first.ele, 400.0);
    expect(geometry.elevationProfile.first, [0.0, 400.0]);
    final back =
        jsonDecode(jsonEncode(geometry.toJson())) as Map<String, dynamic>;
    expect(RouteGeometry.fromJson(back).points.length, 3);
  });

  test('validators mirror backend limits (no public privacy)', () {
    expect(RouteValidators.validateName(''), isNotNull);
    expect(RouteValidators.validateName('Lake Loop'), isNull);
    expect(RouteValidators.validateName('x' * 121), isNotNull);
    expect(RouteValidators.validateActivity('spaceship'), isNotNull);
    expect(RouteValidators.validateActivity('gravel'), isNull);
    expect(RouteValidators.validatePrivacy('public'), isNotNull);
    expect(RouteValidators.validatePrivacy('unlisted'), isNull);
    expect(
      RouteValidators.validatePoints([
        const RoutePointInput(lat: 46.2, lon: 6.1),
      ]),
      'routes.needTwoPoints',
    );
    expect(
      RouteValidators.validatePoints([
        const RoutePointInput(lat: 999, lon: 0),
        const RoutePointInput(lat: 46, lon: 6),
      ]),
      'routes.invalidPoint',
    );
    expect(
      RouteValidators.form(
        name: 'Loop',
        description: '',
        activity: 'road',
        privacy: 'private',
        points: [
          const RoutePointInput(lat: 46.2, lon: 6.1),
          const RoutePointInput(lat: 46.3, lon: 6.2),
        ],
      ),
      isNull,
    );
  });

  test('repository list/detail/geometry/versions hit real paths', () async {
    final repo = RoutesRepository(api(backend()));
    final page = await repo.list();
    expect(page.total, 1);
    expect(page.items.first.id, 'r1');

    final detail = await repo.detail('r1', includeGeometry: true);
    expect(detail.route.name, 'Lake Loop');
    expect(detail.geometry?.pointCount, 3);

    final geometry = await repo.geometry('r1', version: 2);
    expect(geometry.points.length, 3);

    final versions = await repo.versions('r1');
    expect(versions.single.versionNo, 2);
    expect(versions.single.changelog, 'extend');
  });

  test('repository create/update/archive/restore/remove', () async {
    final repo = RoutesRepository(api(backend()));
    final created = await repo.create(
      name: 'New Loop',
      activityType: 'road',
      privacy: 'private',
      points: const [
        RoutePointInput(lat: 46.2, lon: 6.1),
        RoutePointInput(lat: 46.3, lon: 6.2),
      ],
    );
    expect(created.id, 'r2');

    final updated = await repo.update(
      'r1',
      expectedVersion: 2,
      name: 'Renamed',
      changelog: 'rename',
    );
    expect(updated.name, 'Renamed');

    expect((await repo.archive('r1')).isArchived, isTrue);
    expect((await repo.restore('r1')).isArchived, isFalse);
    await repo.remove('r1'); // no throw = DELETE mapped
  });

  test(
    'repository surfaces 409 VERSION_CONFLICT as a coded ApiException',
    () async {
      final repo = RoutesRepository(api(backend(mode: 'conflict')));
      try {
        await repo.update('r1', expectedVersion: 1, name: 'Stale');
        fail('expected ApiException');
      } on ApiException catch (e) {
        expect(e.status, 409);
        expect(e.code, 'VERSION_CONFLICT');
      }
    },
  );

  test('repository surfaces 404 route-not-found', () async {
    final repo = RoutesRepository(api(backend(mode: 'error')));
    try {
      await repo.detail('nope');
      fail('expected ApiException');
    } on ApiException catch (e) {
      expect(e.status, 404);
      expect(e.code, 'ROUTE_NOT_FOUND');
    }
  });

  test('GPX import sends multipart and parses the result', () async {
    final repo = RoutesRepository(api(backend()));
    final result = await repo.importGpx(
      bytes: utf8.encode('<gpx><rte/></gpx>'),
      filename: 'loop.gpx',
      name: 'Loop',
    );
    expect(result.route.source, 'gpx');
    expect(result.importedPoints, 3);
    expect(result.duplicatesRemoved, 1);
  });

  test('GPX export returns bytes and the server filename', () async {
    final repo = RoutesRepository(api(backend()));
    final res = await repo.exportGpx('r1');
    expect(res.filename, 'Lake_Loop.gpx');
    expect(utf8.decode(res.bytes), contains('<gpx'));
  });

  test('offline cache stores a route keyed by version', () async {
    final db = RideDatabase.forTesting(NativeDatabase.memory());
    await db.cacheRoute(
      id: 'r1',
      routeJson: jsonEncode(_routeJson),
      versionNo: 2,
      geometryJson: jsonEncode(_geometryJson),
    );
    final all = await db.allCachedRoutes();
    expect(all, hasLength(1));
    final row = await db.cachedRoute('r1');
    expect(row, isNotNull);
    expect(RoutesRepository.decodeRoute(row!.routeJson).name, 'Lake Loop');
    final geometry = RouteGeometry.fromJson(
      jsonDecode(row.geometryJson!) as Map<String, dynamic>,
    );
    expect(geometry.versionNo, 2);

    // Cached copy is discoverable to the provider layer.
    final container = ProviderContainer(
      overrides: [rideDatabaseProvider.overrideWithValue(AsyncValue.data(db))],
    );
    final routes = await container.read(offlineRoutesProvider.future);
    expect(routes.single.id, 'r1');
    container.dispose();

    await db.uncacheRoute('r1');
    expect(await db.cachedRoute('r1'), isNull);
    await db.close();
  });

  test('route list provider: data state from the API', () async {
    final c = ProviderContainer(
      overrides: [apiClientProvider.overrideWithValue(api(backend()))],
    );
    final routes = await c.read(routeListProvider.future);
    expect(routes.single.name, 'Lake Loop');
    c.dispose();
  });

  test('routes l10n en/fr/ar', () {
    expect(AppLocalizations(const Locale('en')).get('routes.title'), 'Routes');
    expect(
      AppLocalizations(const Locale('fr')).get('routes.title'),
      'Itinéraires',
    );
    expect(
      AppLocalizations(const Locale('ar')).get('routes.title'),
      'المسارات',
    );
    expect(
      AppLocalizations(const Locale('en')).get('routes.activity.gravel'),
      'Gravel',
    );
    expect(
      AppLocalizations(const Locale('en')).get('routes.privacy.unlisted'),
      contains('Unlisted'),
    );
  });

  testWidgets('route list renders routes, metrics and version chip', (t) async {
    final container = ProviderContainer(
      overrides: [apiClientProvider.overrideWithValue(api(backend()))],
    );
    addTearDown(container.dispose);
    await t.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const RouteListPage(),
        ),
      ),
    );
    await t.pumpAndSettle();
    expect(find.text('Lake Loop'), findsOneWidget);
    expect(find.textContaining('1.2 km'), findsOneWidget);
    expect(find.text('v2'), findsOneWidget);
    expect(find.byIcon(Icons.add), findsOneWidget);
  });

  testWidgets('route list empty state offers create + import', (t) async {
    final container = ProviderContainer(
      overrides: [
        apiClientProvider.overrideWithValue(
          api(
            MockClient(
              (req) async => http.Response(
                jsonEncode({
                  'items': <Map<String, dynamic>>[],
                  'total': 0,
                  'page': 1,
                  'page_size': 20,
                }),
                200,
              ),
            ),
          ),
        ),
      ],
    );
    addTearDown(container.dispose);
    await t.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const RouteListPage(),
        ),
      ),
    );
    await t.pumpAndSettle();
    expect(find.textContaining('No routes yet'), findsOneWidget);
    expect(find.text('Import GPX'), findsOneWidget);
  });

  testWidgets('route form blocks invalid input and missing points', (t) async {
    final en = AppLocalizations(const Locale('en'));
    final container = ProviderContainer(
      overrides: [apiClientProvider.overrideWithValue(api(backend()))],
    );
    addTearDown(container.dispose);
    await t.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const RouteFormPage(),
        ),
      ),
    );
    // The map keeps scheduling frames, so settle manually.
    for (var i = 0; i < 5; i++) {
      await t.pump(const Duration(milliseconds: 50));
    }

    // The create button sits below the fold on a 600px test surface.
    await t.ensureVisible(find.byType(FilledButton));
    await t.pump(const Duration(milliseconds: 100));

    void submit() {
      t.widget<FilledButton>(find.byType(FilledButton)).onPressed!();
    }

    // Empty name first.
    submit();
    await t.pump();
    expect(find.text(en.get('routes.invalidName')), findsOneWidget);

    // Valid name but not enough points.
    await t.enterText(find.byType(TextFormField).first, 'Loop');
    await t.ensureVisible(find.byType(FilledButton));
    await t.pump(const Duration(milliseconds: 100));
    submit();
    await t.pump();
    expect(find.text(en.get('routes.needTwoPoints')), findsOneWidget);
  });

  testWidgets('route detail renders metrics, distance and export action', (
    t,
  ) async {
    final en = AppLocalizations(const Locale('en'));
    final db = RideDatabase.forTesting(NativeDatabase.memory());
    final container = ProviderContainer(
      overrides: [
        apiClientProvider.overrideWithValue(api(backend())),
        rideDatabaseProvider.overrideWithValue(AsyncValue.data(db)),
      ],
    );
    addTearDown(() async {
      container.dispose();
      await db.close();
    });
    await t.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const RouteDetailPage(routeId: 'r1'),
        ),
      ),
    );
    await t.pumpAndSettle();

    expect(find.text('Lake Loop'), findsOneWidget);
    expect(find.textContaining('1.2 km'), findsOneWidget);
    expect(find.text('v2'), findsOneWidget);

    // Action row + version history sit below the fold in the ListView.
    await t.drag(find.text('Lake Loop'), const Offset(0, -900));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    expect(find.text(en.get('routes.export')), findsOneWidget);
    expect(find.text(en.get('routes.versions')), findsOneWidget);
  });
}
