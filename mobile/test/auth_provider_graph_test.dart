import 'dart:convert';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/auth/presentation/onboarding_page.dart';
import 'package:cyclecoach/main.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Provider-graph regression tests for the startup CircularDependencyError.
///
/// The bug: the API client's refresh callback resolved
/// `authRepositoryProvider` through the client's own ref, while the
/// repository watches that same client. Riverpod 3 rejects the read because
/// the repository is a dependent of the client — so the first 401 with a
/// working token store crashed startup on splash with
/// `CircularDependencyError`.
///
/// The fix: refresh resolves `sessionCoordinatorProvider` instead — a
/// provider that depends only on a callback-free HTTP client and token
/// storage. These tests drive the REAL provider graph (only the transport
/// and token storage are faked) so any reintroduced cycle fails loudly. No
/// live network calls.
void main() {
  group('startup restores the session without a dependency cycle', () {
    test('no stored token settles unauthenticated', () async {
      final tokens = MemoryTokenStorage();
      final backend = GraphBackend();
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      await container.read(authProvider.notifier).restore();

      expect(container.read(authProvider).status, AuthStatus.unauthenticated);
      // No HTTP at all: with no access token the 401 happens locally, and
      // with no refresh token the coordinator refuses before posting.
      expect(backend.meCalls, 0);
      expect(backend.refreshCalls, 0);
    });

    test('valid stored token settles authenticated', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('valid-access', 'valid-refresh');
      final backend = GraphBackend();
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      await container.read(authProvider.notifier).restore();

      final state = container.read(authProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user!.email, 'r@e.com');
      expect(backend.meCalls, 1);
      expect(backend.refreshCalls, 0);
      // Rotation did not run: the stored pair is untouched.
      expect(await tokens.readAccess(), 'valid-access');
    });

    test(
      'expired token with successful refresh settles authenticated',
      () async {
        final tokens = MemoryTokenStorage();
        await tokens.save('expired-access', 'valid-refresh');
        final backend = GraphBackend();
        final container = graphWith(backend: backend.client, tokens: tokens);
        addTearDown(container.dispose);

        await container.read(authProvider.notifier).restore();

        expect(container.read(authProvider).status, AuthStatus.authenticated);
        expect(backend.refreshCalls, 1);
        expect(backend.meCalls, 2);
        expect(await tokens.readAccess(), 'new-access');
        expect(await tokens.readRefresh(), 'new-refresh');
      },
    );

    test('expired token with failed refresh settles unauthenticated', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('expired-access', 'revoked-refresh');
      final backend = GraphBackend()..refreshSucceeds = false;
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      await container.read(authProvider.notifier).restore();

      expect(container.read(authProvider).status, AuthStatus.unauthenticated);
      expect(backend.refreshCalls, 1);
      expect(await tokens.readAccess(), isNull);
      expect(await tokens.readRefresh(), isNull);
    });
  });

  group('authenticated requests through the graph client', () {
    test('a normal request succeeds without refreshing', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('valid-access', 'valid-refresh');
      final backend = GraphBackend();
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      final result = await container
          .read(apiClientProvider)
          .get('/protected', auth: true);

      expect(result, {'ok': true});
      expect(backend.protectedCalls, 1);
      expect(backend.refreshCalls, 0);
    });

    test('a 401 refreshes once and retries the request', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('expired-access', 'valid-refresh');
      final backend = GraphBackend();
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      final result = await container
          .read(apiClientProvider)
          .get('/protected', auth: true);

      expect(result, {'ok': true});
      expect(backend.refreshCalls, 1);
      expect(backend.protectedCalls, 2);
    });

    test('a 401 with a failed refresh throws without retrying', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('expired-access', 'revoked-refresh');
      final backend = GraphBackend()..refreshSucceeds = false;
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      await expectLater(
        container.read(apiClientProvider).get('/protected', auth: true),
        throwsA(isA<ApiException>()),
      );

      expect(backend.refreshCalls, 1);
      expect(backend.protectedCalls, 1);
    });

    test('concurrent 401s share one refresh operation', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('expired-access', 'valid-refresh');
      final backend = GraphBackend()
        ..refreshDelay = const Duration(milliseconds: 50);
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);
      final api = container.read(apiClientProvider);

      final results = await Future.wait([
        api.get('/protected', auth: true),
        api.post('/protected', {}, auth: true),
        api.get('/protected', auth: true),
      ]);

      expect(results, [
        {'ok': true},
        {'ok': true},
        {'ok': true},
      ]);
      expect(backend.refreshCalls, 1);
    });

    test('a 401 from the refresh endpoint itself does not recurse', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('expired-access', 'valid-refresh');
      final backend = GraphBackend()..refreshSucceeds = false;
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);
      // Settle startup first: it consumes the failing refresh exactly once
      // and clears the session, so the probe below cannot race it.
      await container.read(authProvider.notifier).restore();
      await tokens.save('expired-access', 'valid-refresh');

      await expectLater(
        container.read(authRepositoryProvider).me(),
        throwsA(isA<ApiException>()),
      );

      // One refresh POST per failed request — the refresh endpoint's own
      // 401 propagated instead of triggering another refresh cycle (which
      // would show up as refreshCalls outgrowing meCalls).
      expect(backend.refreshCalls, 2);
      expect(backend.meCalls, 2);
    });
  });

  group('logout and session expiry stay non-recursive', () {
    test('logout revokes once without refreshing', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('valid-access', 'valid-refresh');
      final backend = GraphBackend();
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      await container.read(authProvider.notifier).logout();

      expect(container.read(authProvider).status, AuthStatus.unauthenticated);
      expect(backend.logoutCalls, 1);
      expect(backend.refreshCalls, 0);
      expect(await tokens.readAccess(), isNull);
      expect(await tokens.readRefresh(), isNull);
    });

    test(
      'session expiry clears credentials and ends the session safely',
      () async {
        final tokens = MemoryTokenStorage();
        await tokens.save('expired-access', 'revoked-refresh');
        final backend = GraphBackend();
        final container = graphWith(backend: backend.client, tokens: tokens);
        addTearDown(container.dispose);

        await container.read(authProvider.notifier).handleSessionExpired();

        expect(container.read(authProvider).status, AuthStatus.unauthenticated);
        expect(backend.logoutCalls, 1);
        expect(backend.refreshCalls, 0);
        expect(await tokens.readAccess(), isNull);
        expect(await tokens.readRefresh(), isNull);
      },
    );
  });

  group('provider initialization has no dependency cycle', () {
    test('build order does not matter: client first, then restore', () async {
      final tokens = MemoryTokenStorage();
      final backend = GraphBackend();
      final container = graphWith(backend: backend.client, tokens: tokens);
      addTearDown(container.dispose);

      // Touch the client before anything else: with the old wiring,
      // resolving the graph in this order still ended in the cycle.
      expect(container.read(apiClientProvider), isA<ApiClient>());
      await container.read(authProvider.notifier).restore();

      expect(container.read(authProvider).status, AuthStatus.unauthenticated);
    });

    testWidgets('the full app boots past splash to onboarding', (t) async {
      final tokens = MemoryTokenStorage();
      final backend = GraphBackend();
      await t.pumpWidget(
        ProviderScope(
          overrides: [
            tokenStorageProvider.overrideWithValue(tokens),
            httpClientProvider.overrideWithValue(backend.client),
          ],
          child: const CycleCoachApp(),
        ),
      );
      await t.pumpAndSettle();

      // No stored session: restore settles unauthenticated and the router
      // leaves the bootstrap splash for the Get Started screen.
      expect(find.byType(OnboardingPage), findsOneWidget);
      expect(t.takeException(), isNull);
    });
  });
}

/// Overrides only the transport and token storage: every provider under test
/// is the production wiring.
ProviderContainer graphWith({
  required MockClient backend,
  required MemoryTokenStorage tokens,
}) {
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      httpClientProvider.overrideWithValue(backend),
    ],
  );
}

/// Scriptable fake backend speaking the real envelopes. Accepts exactly one
/// access token at a time so rotation is observable.
class GraphBackend {
  String validAccess = 'valid-access';
  String expectedRefresh = 'valid-refresh';
  String rotatedAccess = 'new-access';
  String rotatedRefresh = 'new-refresh';
  bool refreshSucceeds = true;
  Duration refreshDelay = Duration.zero;

  int meCalls = 0;
  int refreshCalls = 0;
  int logoutCalls = 0;
  int protectedCalls = 0;

  late final MockClient client = MockClient((req) async {
    final path = req.url.path;
    if (path.endsWith('/auth/refresh')) {
      refreshCalls++;
      await Future.delayed(refreshDelay);
      if (!refreshSucceeds) return _error('INVALID_REFRESH', 401);
      final presented = _json(req)['refresh_token'] as String?;
      if (presented != expectedRefresh) return _error('INVALID_REFRESH', 401);
      // The freshly issued token is now the session token, mirroring a
      // backend that minted it.
      validAccess = rotatedAccess;
      return http.Response(
        '{"access_token":"$rotatedAccess","refresh_token":"$rotatedRefresh"}',
        200,
      );
    }
    if (path.endsWith('/auth/me')) {
      meCalls++;
      if (req.headers['authorization'] != 'Bearer $validAccess') {
        return _error('UNAUTHORIZED', 401);
      }
      return http.Response(_userEnvelope, 200);
    }
    if (path.endsWith('/protected')) {
      protectedCalls++;
      if (req.headers['authorization'] != 'Bearer $validAccess' &&
          req.headers['authorization'] != 'Bearer $rotatedAccess') {
        return _error('UNAUTHORIZED', 401);
      }
      // After rotation the fake accepts the new token as the session token,
      // mirroring a backend that issued it.
      return http.Response('{"ok":true}', 200);
    }
    if (path.endsWith('/auth/logout')) {
      logoutCalls++;
      return http.Response('{"status":"ok"}', 200);
    }
    return http.Response('not found', 404);
  });

  http.Response _error(String code, int status) => http.Response(
    '{"error":{"code":"X","message":"m","details":{"code":"$code","message":"backend said no"}}}',
    status,
  );

  static const _userEnvelope =
      '{"user":{"id":"00000000-0000-0000-0000-000000000001","email":"r@e.com",'
      '"status":"active","email_verified":false,"created_at":"2026-01-01T00:00:00Z",'
      '"last_login_at":null},"profile":{"display_name":"R","first_name":null,'
      '"last_name":null,"avatar_ref":null,"country":null,"city":null,'
      '"preferred_language":"en","timezone":"UTC","measurement_system":"metric",'
      '"cycling_experience":null,"disciplines":[],"training_goal":null,'
      '"profile_visibility":"public","activity_visibility":"friends"}}';
}

Map<String, dynamic> _json(http.Request req) {
  // MockClient exposes the client's already-encoded JSON request body.
  return jsonDecode(req.body) as Map<String, dynamic>;
}
