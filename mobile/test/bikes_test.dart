import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/routing/app_router.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/bikes/data/bikes_repository.dart';
import 'package:cyclecoach/features/bikes/domain/bike.dart';
import 'package:cyclecoach/features/bikes/domain/bike_validators.dart';
import 'package:cyclecoach/features/bikes/presentation/bikes_providers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _bikeJson = {
  'id': 'b1',
  'name': 'Road Bike',
  'category': 'road',
  'brand': 'Spec',
  'model': 'Tarmac',
  'model_year': 2023,
  'frame_size': 'M',
  'weight_kg': 8.2,
  'notes': null,
  'image_ref': null,
  'status': 'active',
  'initial_distance_km': 0,
  'version': 1,
  'created_at': '2026-01-01T00:00:00Z',
  'updated_at': '2026-01-01T00:00:00Z',
};

MockClient backend({String mode = 'ok'}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (mode == 'error') {
      return http.Response(
        '{"error":{"code":"BIKE_NOT_FOUND","message":"Bike not found.","details":{}}}',
        404,
      );
    }
    if (req.method == 'GET' && path.endsWith('/api/v1/bikes')) {
      final empty = req.url.queryParameters['page'] == '9';
      final items = empty ? <Map<String, dynamic>>[] : [_bikeJson];
      return http.Response(
        jsonEncode({
          'items': items,
          'total': empty ? 0 : 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (path.endsWith('/api/v1/bikes/b1')) {
      if (req.method == 'DELETE') {
        return http.Response('{"id":"b1","status":"active"}', 200);
      }
      return http.Response(
        '{"id":"b1","name":"Road Bike","category":"road","status":"active",'
        '"weight_kg":8.2,"initial_distance_km":0,"version":1,'
        '"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}',
        200,
      );
    }
    if (path.endsWith('/api/v1/bikes') && req.method == 'POST') {
      return http.Response(
        '{"id":"b2","name":"New","category":"gravel","status":"active",'
        '"initial_distance_km":0,"version":1,'
        '"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}',
        201,
      );
    }
    if (path.endsWith('/archive')) {
      return http.Response(
        '{"id":"b1","name":"Road Bike","category":"road","status":"archived",'
        '"initial_distance_km":0,"version":2,'
        '"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}',
        200,
      );
    }
    return http.Response('not found', 404);
  });
}

ApiClient api(MockClient backend) => ApiClient(
  baseUrl: 'http://test',
  client: backend,
  accessToken: () async => 'test-token',
);

void main() {
  test('bike model parses + converts units, never fakes mileage', () {
    final bike = Bike.fromJson(Map<String, dynamic>.from(_bikeJson));
    expect(bike.name, 'Road Bike');
    expect(bike.displayWeight(imperial: false), '8.2 kg');
    expect(bike.displayWeight(imperial: true), contains('lb'));
    expect(bike.subtitle(), contains('Spec'));
    expect(
      Bike.fromJson({
        ..._bikeJson,
        'weight_kg': null,
      }).displayWeight(imperial: false),
      isEmpty,
    );
  });

  test('bike validators mirror backend limits', () {
    expect(BikeValidators.name(''), isNotNull);
    expect(BikeValidators.name('Road'), isNull);
    expect(BikeValidators.category('spaceship'), isNotNull);
    expect(BikeValidators.category('gravel'), isNull);
    expect(BikeValidators.modelYear('1800'), isNotNull);
    expect(BikeValidators.modelYear(''), isNull);
    expect(BikeValidators.weightKg('-2'), isNotNull);
    expect(BikeValidators.weightKg('0'), isNotNull);
    expect(BikeValidators.weightKg('8.2'), isNull);
  });

  test('repository list/detail/create/archive/remove hit real paths', () async {
    final repo = BikesRepository(api(backend()));
    final page = await repo.list();
    expect(page.total, 1);
    expect(page.items.first.id, 'b1');
    expect((await repo.detail('b1')).name, 'Road Bike');
    expect(
      (await repo.create(
        const Bike(
          id: '',
          name: 'New',
          category: 'gravel',
          status: 'active',
          initialDistanceKm: 0,
          version: 1,
        ),
      )).id,
      'b2',
    );
    expect((await repo.archive('b1')).isArchived, isTrue);
    await repo.remove('b1'); // no throw = DELETE mapped + parsed
  });

  test('repository surfaces 404 as ApiException', () async {
    final repo = BikesRepository(api(backend(mode: 'error')));
    try {
      await repo.detail('nope');
      fail('expected ApiException');
    } on ApiException catch (e) {
      expect(e.status, 404);
    }
  });

  test('list provider: data state', () async {
    final c = ProviderContainer(
      overrides: [apiClientProvider.overrideWithValue(api(backend()))],
    );
    final bikes = await c.read(bikeListProvider.future);
    expect(bikes.length, 1);
    c.dispose();
  });

  test('list provider: error state', () async {
    final c = ProviderContainer(
      overrides: [
        apiClientProvider.overrideWithValue(api(backend(mode: 'error'))),
      ],
    );
    final sub = c.listen(bikeListProvider, (prev, next) {});
    for (var i = 0; i < 50; i++) {
      final s = c.read(bikeListProvider);
      if (s.hasValue || s.hasError) break;
      await Future.delayed(const Duration(milliseconds: 100));
    }
    sub.close();
    final state = c.read(bikeListProvider);
    expect(state.hasError, isTrue);
    expect(state.error, isA<ApiException>());
    c.dispose();
  });

  test('bikes l10n en/fr/ar', () {
    expect(AppLocalizations(const Locale('en')).get('bikes.title'), 'My Bikes');
    expect(
      AppLocalizations(const Locale('fr')).get('bikes.category.road'),
      'Route',
    );
    expect(AppLocalizations(const Locale('ar')).get('bikes.title'), 'دراجاتي');
  });

  testWidgets('unauthenticated /bikes → onboarding guard', (t) async {
    await t.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(
            () => _FixedBikeAuth(const AuthState(AuthStatus.unauthenticated)),
          ),
        ],
        child: Consumer(
          builder: (context, ref, _) {
            return MaterialApp.router(
              supportedLocales: AppLocalizations.supported,
              localizationsDelegates: const [
                AppLocalizationsDelegate(),
                GlobalMaterialLocalizations.delegate,
                GlobalWidgetsLocalizations.delegate,
                GlobalCupertinoLocalizations.delegate,
              ],
              routerConfig: ref.watch(routerProvider),
            );
          },
        ),
      ),
    );
    await t.pumpAndSettle();
    // The guard funnels strangers to Get Started, not straight to login.
    expect(find.byKey(const Key('onboarding.getStarted')), findsOneWidget);
  });
}

class _FixedBikeAuth extends AuthNotifier {
  final AuthState fixed;
  _FixedBikeAuth(this.fixed);
  @override
  AuthState build() => fixed;
}
