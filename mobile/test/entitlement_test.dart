import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/coach/presentation/coach_page.dart';
import 'package:cyclecoach/features/subscriptions/domain/entitlement.dart';
import 'package:cyclecoach/features/subscriptions/presentation/entitlement_providers.dart';
import 'package:cyclecoach/shared/widgets/pro_locked_feature.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> _stateJson({
  String plan = 'free',
  List<Map<String, dynamic>> entitlements = const [],
}) => {
  'plan': plan,
  'free_capabilities': const [
    'core_ride_recording',
    'core_routes',
    'core_training',
    'limited_ai_status',
  ],
  'entitlements': entitlements,
  'evaluated_at': '2026-10-06T04:00:00Z',
};

Map<String, dynamic> _grant({
  required String feature,
  String status = 'active',
  String source = 'subscription',
}) => {
  'feature': feature,
  'status': status,
  'source': source,
  'starts_at': '2026-09-06T04:00:00Z',
  'expires_at': '2026-11-06T04:00:00Z',
  'effective': status == 'active',
};

/// Fake backend with two accounts. The entitlement response follows the
/// currently authenticated email, which is what makes the account-switch test
/// meaningful: there is no way for A's state to leak into B except through a
/// client bug.
class FakeEntitlementBackend {
  final Map<String, Map<String, dynamic>> plans;
  String? currentEmail;
  int entitlementCalls = 0;

  FakeEntitlementBackend(this.plans);

  MockClient get client => MockClient((req) async {
    final path = req.url.path;
    if (path.endsWith('/auth/login')) {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      currentEmail = '${body['email']}';
      return http.Response(
        '{"access_token":"a-$currentEmail","refresh_token":"r-$currentEmail"}',
        200,
      );
    }
    if (path.endsWith('/auth/me')) {
      return http.Response(
        jsonEncode({
          'user': {
            'id': '00000000-0000-0000-0000-000000000001',
            'email': currentEmail,
            'status': 'active',
            'email_verified': false,
            'created_at': '2026-01-01T00:00:00Z',
            'last_login_at': null,
          },
          'profile': {
            'display_name': 'R',
            'first_name': null,
            'last_name': null,
            'avatar_ref': null,
            'country': null,
            'city': null,
            'preferred_language': 'en',
            'timezone': 'UTC',
            'measurement_system': 'metric',
            'cycling_experience': null,
            'disciplines': [],
            'training_goal': null,
            'profile_visibility': 'public',
            'activity_visibility': 'friends',
          },
        }),
        200,
      );
    }
    if (path.endsWith('/auth/logout')) {
      return http.Response('{"status":"ok"}', 200);
    }
    if (path.endsWith('/me/entitlements')) {
      entitlementCalls++;
      final state = plans[currentEmail] ?? _stateJson();
      return http.Response(jsonEncode(state), 200);
    }
    return http.Response('not found', 404);
  });
}

ProviderContainer containerWith(FakeEntitlementBackend backend) {
  final tokens = MemoryTokenStorage();
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: backend.client,
          accessToken: tokens.readAccess,
        ),
      ),
    ],
  );
}

Widget localized(Widget child) => MaterialApp(
  locale: const Locale('en'),
  supportedLocales: AppLocalizations.supported,
  localizationsDelegates: const [
    AppLocalizationsDelegate(),
    GlobalMaterialLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
  ],
  home: Scaffold(body: child),
);

void main() {
  group('models', () {
    test('parses a Pro state into typed values', () {
      final state = EntitlementState.fromJson(
        _stateJson(
          plan: 'pro',
          entitlements: [_grant(feature: 'ai_coach')],
        ),
      );
      expect(state.plan, SubscriptionPlan.pro);
      expect(state.isPro, isTrue);
      expect(state.freeCapabilities, contains('core_training'));
      final grant = state.entitlements.single;
      expect(grant.feature, EntitlementFeature.aiCoach);
      expect(grant.status, EntitlementStatus.active);
      expect(grant.source, EntitlementSource.subscription);
      expect(grant.effective, isTrue);
      expect(
        grant.isEffectiveAt(DateTime.utc(2026, 10, 6)),
        isTrue,
        reason: 'The cached check must agree with the server timestamp.',
      );
    });

    test('an expired grant parses but is not locally effective', () {
      final state = EntitlementState.fromJson(
        _stateJson(
          plan: 'free',
          entitlements: [
            {
              ..._grant(feature: 'ai_coach', status: 'inactive'),
              'starts_at': '2026-08-06T04:00:00Z',
              'expires_at': '2026-09-06T04:00:00Z',
              'effective': false,
            },
          ],
        ),
      );
      expect(state.isPro, isFalse);
      expect(
        state.hasFeature(EntitlementFeature.aiCoach, DateTime.utc(2026, 10, 6)),
        isFalse,
      );
    });

    test('an unknown future capability degrades instead of breaking state', () {
      final state = EntitlementState.fromJson(
        _stateJson(entitlements: [_grant(feature: 'future_feature')]),
      );
      expect(state.entitlements.single.feature, EntitlementFeature.unknown);
      expect(
        state.hasFeature(EntitlementFeature.aiCoach, DateTime.utc(2026, 10, 6)),
        isFalse,
      );
    });
  });

  group('repository', () {
    test('fetches the typed state from the entitlement endpoint', () async {
      final backend = FakeEntitlementBackend({
        'a@example.com': _stateJson(
          plan: 'pro',
          entitlements: [_grant(feature: 'ai_coach')],
        ),
      });
      final container = containerWith(backend);
      addTearDown(container.dispose);
      await container
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');

      final state = await container.read(entitlementRepositoryProvider).fetch();
      expect(state.plan, SubscriptionPlan.pro);
      expect(backend.entitlementCalls, 1);
    });
  });

  group('session cache', () {
    test('logout clears the cached plan', () async {
      final backend = FakeEntitlementBackend({
        'a@example.com': _stateJson(
          plan: 'pro',
          entitlements: [_grant(feature: 'ai_coach')],
        ),
      });
      final container = containerWith(backend);
      addTearDown(container.dispose);
      await container
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      expect(
        await container.read(entitlementProvider.future),
        isA<EntitlementState>(),
      );

      await container.read(authProvider.notifier).logout();
      expect(await container.read(entitlementProvider.future), isNull);
    });

    test('switching accounts replaces the previous plan', () async {
      final backend = FakeEntitlementBackend({
        'a@example.com': _stateJson(
          plan: 'pro',
          entitlements: [_grant(feature: 'ai_coach')],
        ),
        'b@example.com': _stateJson(),
      });
      final container = containerWith(backend);
      addTearDown(container.dispose);

      await container
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      final pro = await container.read(entitlementProvider.future);
      expect(pro!.plan, SubscriptionPlan.pro);

      await container.read(authProvider.notifier).logout();
      await container
          .read(authProvider.notifier)
          .login('b@example.com', 'StrongPass123');
      final free = await container.read(entitlementProvider.future);
      expect(free!.plan, SubscriptionPlan.free);
      expect(free.entitlements, isEmpty);
    });

    test('a fresh cache is reused and a stale cache refreshes', () async {
      final backend = FakeEntitlementBackend({
        'a@example.com': _stateJson(
          plan: 'pro',
          entitlements: [_grant(feature: 'ai_coach')],
        ),
      });
      ProviderContainer freshContainer({Duration? ttl}) {
        final tokens = MemoryTokenStorage();
        return ProviderContainer(
          overrides: [
            tokenStorageProvider.overrideWithValue(tokens),
            apiClientProvider.overrideWithValue(
              ApiClient(
                baseUrl: 'http://test',
                client: backend.client,
                accessToken: tokens.readAccess,
              ),
            ),
            if (ttl != null) entitlementCacheTtlProvider.overrideWithValue(ttl),
          ],
        );
      }

      final container = freshContainer();
      addTearDown(container.dispose);
      await container
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      await container.read(entitlementProvider.future);
      expect(backend.entitlementCalls, 1);

      await container.read(entitlementProvider.notifier).refreshIfStale();
      expect(
        backend.entitlementCalls,
        1,
        reason: 'A fresh cache must not refetch.',
      );

      // A negative TTL means "always stale", independent of clock
      // granularity: two DateTime.now() calls can land in the same tick.
      final stale = freshContainer(ttl: const Duration(microseconds: -1));
      addTearDown(stale.dispose);
      await stale
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      await stale.read(entitlementProvider.future);
      await stale.read(entitlementProvider.notifier).refreshIfStale();
      expect(
        backend.entitlementCalls,
        3,
        reason: 'A stale cache must ask the server again.',
      );
    });
  });

  group('locked UX', () {
    testWidgets('a 403 renders the Pro lock, not a generic error', (t) async {
      final backend = MockClient((req) async {
        final path = req.url.path;
        if (path.endsWith('/coach/status')) {
          return http.Response(
            jsonEncode({
              'enabled': false,
              'provider': 'none',
              'model': '',
              'fallback_only': true,
              'prompt_versions': const {},
              'limits': {
                'max_message_chars': 1000,
                'max_input_tokens': 4000,
                'max_output_tokens': 600,
                'max_requests_per_user': 20,
                'timeout_seconds': 20,
              },
            }),
            200,
          );
        }
        if (path.endsWith('/coach/message') ||
            path.endsWith('/coach/weekly-summary') ||
            path.contains('/coach/ride/') ||
            path.contains('/coach/workout/')) {
          return http.Response(
            jsonEncode({
              'error': {
                'code': 'ENTITLEMENT_REQUIRED',
                'message': 'This feature requires CycleCoach Pro.',
                'details': {
                  'code': 'ENTITLEMENT_REQUIRED',
                  'message': 'This feature requires CycleCoach Pro.',
                  'feature': 'ai_coach',
                  'required_plan': 'pro',
                },
              },
            }),
            403,
          );
        }
        // The page watches training providers for its quick actions. Empty
        // successes keep them quiet; errors would schedule Riverpod retries
        // that outlive the test.
        if (path.contains('/training/activities')) {
          return http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1, 'page_size': 50}),
            200,
          );
        }
        if (path.contains('/workouts')) {
          return http.Response(
            jsonEncode({'items': [], 'total': 0, 'page': 1, 'page_size': 20}),
            200,
          );
        }
        return http.Response('not found', 404);
      });
      final tokens = MemoryTokenStorage();
      final container = ProviderContainer(
        overrides: [
          tokenStorageProvider.overrideWithValue(tokens),
          apiClientProvider.overrideWithValue(
            ApiClient(
              baseUrl: 'http://test',
              client: backend,
              // The lock test is about authorization, not authentication: the
              // caller is signed in, and the server still says no.
              accessToken: () async => 'test-token',
            ),
          ),
        ],
      );
      addTearDown(container.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: localized(const CoachPage()),
        ),
      );
      await t.pumpAndSettle();

      await t.tap(find.byKey(const Key('coach.ask')));
      await t.pumpAndSettle();
      await t.drag(find.byType(ListView), const Offset(0, -1000));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('subscription.locked')), findsOneWidget);
      expect(
        find.text('This feature requires CycleCoach Pro.'),
        findsOneWidget,
      );
      expect(find.byKey(const Key('coach.answer.error')), findsNothing);
    });

    testWidgets('the lock explains without offering checkout', (t) async {
      await t.pumpWidget(
        localized(const ProLockedFeature(feature: EntitlementFeature.aiCoach)),
      );
      await t.pumpAndSettle();
      expect(
        find.text('This feature requires CycleCoach Pro.'),
        findsOneWidget,
      );
      expect(find.textContaining('AI Coach'), findsOneWidget);
      expect(
        find.text('Pro checkout is not available in this build.'),
        findsOneWidget,
      );
      // No purchase affordance anywhere in the lock.
      expect(find.byType(FilledButton), findsNothing);
      expect(find.byType(TextButton), findsNothing);
    });
  });
}
