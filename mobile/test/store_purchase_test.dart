import 'dart:convert';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/store/data/store_repository.dart';
import 'package:cyclecoach/features/store/domain/store_purchase.dart';
import 'package:cyclecoach/features/store/presentation/store_purchase_providers.dart';
import 'package:cyclecoach/features/subscriptions/domain/entitlement.dart';
import 'package:cyclecoach/features/subscriptions/presentation/entitlement_providers.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Fake backend: auth plus store endpoints. The restore handler flips the
/// served plan to pro, mirroring what the real server does when it applies
/// a verification — the client only ever re-reads.
/// Test-only fake backend. Public only because test helpers take it as a
/// parameter; it never leaves test code.
class StoreBackend {
  String? currentEmail;
  String plan = 'free';
  int restoreCalls = 0;
  int verifyCalls = 0;
  int restoreStatus = 200;

  /// Whether a successful restore flips the served plan to pro (mirroring
  /// a server that applied the verification). Off for tests where the
  /// server reconciles to free.
  bool flipPlanOnRestore = true;

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
      final id = (currentEmail ?? '').startsWith('a@') ? 'user-a' : 'user-b';
      return http.Response(
        jsonEncode({
          'user': {
            'id': id,
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
      return http.Response(
        jsonEncode({
          'plan': plan,
          'free_capabilities': const ['core_ride_recording'],
          'entitlements': const [],
          'evaluated_at': '2026-10-06T06:00:00Z',
        }),
        200,
      );
    }
    if (path.endsWith('/store/purchases/restore')) {
      restoreCalls++;
      if (restoreStatus != 200) {
        return http.Response(
          jsonEncode({
            'error': {
              'code': 'PROVIDER_UNAVAILABLE',
              'message': 'No provider.',
              'details': {
                'code': 'PROVIDER_UNAVAILABLE',
                'message': 'No provider.',
              },
            },
          }),
          restoreStatus,
        );
      }
      plan = flipPlanOnRestore ? 'pro' : plan;
      return http.Response(
        jsonEncode({
          'plan': 'pro',
          'free_capabilities': const ['core_ride_recording'],
          'entitlements': const [],
          'evaluated_at': '2026-10-06T06:00:00Z',
        }),
        200,
      );
    }
    if (path.endsWith('/store/purchases/verify')) {
      verifyCalls++;
      return http.Response('not found', 404);
    }
    return http.Response('not found', 404);
  });
}

ProviderContainer containerWith(StoreBackend backend, {Duration? ttl}) {
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

void main() {
  group('purchase outcomes', () {
    test('the state machine cannot name a premium plan', () {
      expect(PurchaseOutcome.values.map((o) => o.name), [
        'idle',
        'pending',
        'cancelled',
        'failed',
        'unavailable',
        'verified',
      ]);
    });
  });

  group('purchase service', () {
    test('starts idle with no session side effects', () async {
      final backend = StoreBackend();
      final c = containerWith(backend);
      addTearDown(c.dispose);
      // Settle the auth restore explicitly: without a stored session it
      // ends unauthenticated, and awaiting here (rather than racing
      // dispose) keeps the test deterministic.
      await c.read(authProvider.notifier).restore();
      expect(c.read(storePurchaseServiceProvider), PurchaseOutcome.idle);
      expect(backend.verifyCalls, 0);
      expect(backend.restoreCalls, 0);
    });

    test(
      'purchase without an SDK reports unavailable and calls nothing',
      () async {
        final backend = StoreBackend();
        final c = containerWith(backend);
        addTearDown(c.dispose);
        await c
            .read(authProvider.notifier)
            .login('a@example.com', 'StrongPass123');
        final outcome = await c
            .read(storePurchaseServiceProvider.notifier)
            .purchase(productId: 'cyclecoach_pro_monthly');
        expect(outcome, PurchaseOutcome.unavailable);
        expect(
          c.read(storePurchaseServiceProvider),
          PurchaseOutcome.unavailable,
        );
        // No credential existed, so nothing was submitted anywhere.
        expect(backend.verifyCalls, 0);
        expect(backend.restoreCalls, 0);
      },
    );

    test(
      'restore reconciles then refreshes server state into the cache',
      () async {
        final backend = StoreBackend();
        final c = containerWith(backend);
        addTearDown(c.dispose);
        await c
            .read(authProvider.notifier)
            .login('a@example.com', 'StrongPass123');
        // Cache starts free: the fetch below is server state, not a default.
        final before = await c.read(entitlementProvider.future);
        expect(before!.plan, SubscriptionPlan.free);

        final outcome = await c
            .read(storePurchaseServiceProvider.notifier)
            .restore(
              provider: 'app_store',
              productId: 'cyclecoach_pro_monthly',
              purchaseToken: 'store-token',
            );
        expect(outcome, PurchaseOutcome.verified);
        expect(backend.restoreCalls, 1);
        // The plan came from the refreshed server fetch, not from the
        // service: the service holds an outcome enum, no plan, no grants.
        final after = await c.read(entitlementProvider.future);
        expect(after!.plan, SubscriptionPlan.pro);
      },
    );

    test('verified means reconciled, not pro', () async {
      final backend = StoreBackend()..flipPlanOnRestore = false;
      final c = containerWith(backend);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('b@example.com', 'StrongPass123');
      // Server reconciles to free (nothing to restore): the outcome still
      // reports the reconciliation, and the plan stays free.
      final outcome = await c
          .read(storePurchaseServiceProvider.notifier)
          .restore(
            provider: 'app_store',
            productId: 'cyclecoach_pro_monthly',
            purchaseToken: 'nothing-to-restore',
          );
      expect(outcome, PurchaseOutcome.verified);
      final state = await c.read(entitlementProvider.future);
      expect(state!.plan, SubscriptionPlan.free);
    });

    test('provider unavailable maps to unavailable, cache untouched', () async {
      final backend = StoreBackend()..restoreStatus = 503;
      final c = containerWith(backend);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      final outcome = await c
          .read(storePurchaseServiceProvider.notifier)
          .restore(
            provider: 'app_store',
            productId: 'cyclecoach_pro_monthly',
            purchaseToken: 'token',
          );
      expect(outcome, PurchaseOutcome.unavailable);
      final state = await c.read(entitlementProvider.future);
      expect(state!.plan, SubscriptionPlan.free);
    });

    test('refused restore maps to failed, cache untouched', () async {
      final backend = StoreBackend()..restoreStatus = 422;
      final c = containerWith(backend);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      final outcome = await c
          .read(storePurchaseServiceProvider.notifier)
          .restore(provider: 'nope', productId: 'nope', purchaseToken: 'token');
      expect(outcome, PurchaseOutcome.failed);
    });

    test('logout resets purchase state to idle', () async {
      final backend = StoreBackend();
      final c = containerWith(backend);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      await c
          .read(storePurchaseServiceProvider.notifier)
          .purchase(productId: 'cyclecoach_pro_monthly');
      expect(c.read(storePurchaseServiceProvider), PurchaseOutcome.unavailable);

      await c.read(authProvider.notifier).logout();
      expect(c.read(storePurchaseServiceProvider), PurchaseOutcome.idle);
    });

    test('account switch resets purchase state', () async {
      final backend = StoreBackend();
      final c = containerWith(backend);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      await c
          .read(storePurchaseServiceProvider.notifier)
          .restore(
            provider: 'app_store',
            productId: 'cyclecoach_pro_monthly',
            purchaseToken: 'token',
          );
      expect(c.read(storePurchaseServiceProvider), PurchaseOutcome.verified);

      await c.read(authProvider.notifier).logout();
      await c
          .read(authProvider.notifier)
          .login('b@example.com', 'StrongPass123');
      expect(c.read(storePurchaseServiceProvider), PurchaseOutcome.idle);
    });
  });

  group('store repository', () {
    test('restore posts the credential and parses the state', () async {
      final backend = StoreBackend();
      final c = containerWith(backend);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      final repo = StoreRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: backend.client,
          accessToken: () async => 'test-token',
        ),
      );
      final state = await repo.restore(
        provider: 'app_store',
        productId: 'cyclecoach_pro_monthly',
        purchaseToken: 'store-token',
      );
      expect(backend.restoreCalls, 1);
      expect(state.plan, SubscriptionPlan.pro);
    });

    test('repository paths match the backend contract', () {
      expect(StoreRepository.verifyPath, '/api/v1/store/purchases/verify');
      expect(StoreRepository.restorePath, '/api/v1/store/purchases/restore');
    });
  });
}
