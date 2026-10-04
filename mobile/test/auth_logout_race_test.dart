import 'dart:async';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/data/auth_repository.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Logout / refresh session-integrity tests.
///
/// These drive the real [AuthRepository] against a controllable fake transport.
/// Each race is resolved with explicit gates (completers), not sleeps, so the
/// interleaving under test is the only one that can happen.
void main() {
  test(
    'logout during an in-flight refresh does not resurrect the session',
    () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('access-one', 'refresh-one');
      final refreshGate = Completer<void>();
      var logoutCalls = 0;
      final backend = MockClient((req) async {
        if (req.url.path.endsWith('/auth/refresh')) {
          await refreshGate.future;
          return http.Response(
            '{"access_token":"access-two","refresh_token":"refresh-two"}',
            200,
          );
        }
        if (req.url.path.endsWith('/auth/logout')) {
          logoutCalls++;
          return http.Response('{"status":"ok"}', 200);
        }
        return http.Response('not found', 404);
      });
      final repo = AuthRepository(
        ApiClient(baseUrl: 'http://test', client: backend),
        tokens,
      );

      final refreshing = repo.refreshSession();
      // The user signs out while the refresh request is still open.
      await repo.logout();
      refreshGate.complete();
      await expectLater(
        refreshing,
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'SESSION_ENDED'),
        ),
      );

      expect(await tokens.readAccess(), isNull);
      expect(await tokens.readRefresh(), isNull);
      expect(logoutCalls, 1);
    },
  );

  test('refresh that finished before logout still ends up cleared', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('access-one', 'refresh-one');
    var backendSawLogout = false;
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        return http.Response(
          '{"access_token":"access-two","refresh_token":"refresh-two"}',
          200,
        );
      }
      if (req.url.path.endsWith('/auth/logout')) {
        backendSawLogout = true;
        return http.Response('{"status":"ok"}', 200);
      }
      return http.Response('not found', 404);
    });
    final repo = AuthRepository(
      ApiClient(baseUrl: 'http://test', client: backend),
      tokens,
    );

    await repo.refreshSession();
    expect(await tokens.readAccess(), 'access-two');
    await repo.logout();

    expect(await tokens.readAccess(), isNull);
    expect(await tokens.readRefresh(), isNull);
    expect(backendSawLogout, true);
  });

  test('signing in a new account discards a stale in-flight refresh', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('access-one', 'refresh-one');
    final refreshGate = Completer<void>();
    var loginCalls = 0;
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        await refreshGate.future;
        return http.Response(
          '{"access_token":"stale-access","refresh_token":"stale-refresh"}',
          200,
        );
      }
      if (req.url.path.endsWith('/auth/login')) {
        loginCalls++;
        return http.Response(
          '{"access_token":"new-access","refresh_token":"new-refresh"}',
          200,
        );
      }
      if (req.url.path.endsWith('/auth/me')) {
        final header = req.headers['authorization'];
        if (header != 'Bearer new-access') {
          return http.Response(
            '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
            401,
          );
        }
        return http.Response(
          '{"user":{"id":"u-2","email":"b@example.com","email_verified":true},'
          '"profile":{"display_name":"B"}}',
          200,
        );
      }
      return http.Response('not found', 404);
    });
    final repo = AuthRepository(
      ApiClient(
        baseUrl: 'http://test',
        client: backend,
        accessToken: tokens.readAccess,
      ),
      tokens,
    );

    final refreshing = repo.refreshSession();
    await repo.login(email: 'b@example.com', password: 'StrongPass123');
    refreshGate.complete();
    await expectLater(
      refreshing,
      throwsA(
        isA<ApiException>().having((e) => e.code, 'code', 'SESSION_ENDED'),
      ),
    );

    expect(loginCalls, 1);
    // The new account's tokens survive: the stale pair never overwrote them.
    expect(await tokens.readAccess(), 'new-access');
    expect(await tokens.readRefresh(), 'new-refresh');
  });

  test(
    'a refresh after logout reports no session without calling the server',
    () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('access-one', 'refresh-one');
      var refreshCalls = 0;
      final backend = MockClient((req) async {
        if (req.url.path.endsWith('/auth/refresh')) refreshCalls++;
        return http.Response('not found', 404);
      });
      final repo = AuthRepository(
        ApiClient(baseUrl: 'http://test', client: backend),
        tokens,
      );

      await repo.logout();
      await expectLater(
        repo.refreshSession(),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'UNAUTHORIZED'),
        ),
      );
      expect(refreshCalls, 0);
    },
  );

  test(
    'logout survives an unreachable server and still clears tokens',
    () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('access-one', 'refresh-one');
      final backend = MockClient((req) async => throw const SocketishFailure());
      final repo = AuthRepository(
        ApiClient(baseUrl: 'http://test', client: backend),
        tokens,
      );

      await repo.logout();

      expect(await tokens.readAccess(), isNull);
      expect(await tokens.readRefresh(), isNull);
    },
  );

  test('concurrent logouts never leave credentials behind', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('access-one', 'refresh-one');
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/logout')) {
        return http.Response('{"status":"ok"}', 200);
      }
      return http.Response('not found', 404);
    });
    final repo = AuthRepository(
      ApiClient(baseUrl: 'http://test', client: backend),
      tokens,
    );

    await Future.wait([repo.logout(), repo.logout(), repo.logout()]);

    expect(await tokens.readAccess(), isNull);
    expect(await tokens.readRefresh(), isNull);
  });
}

class SocketishFailure implements Exception {
  const SocketishFailure();
  @override
  String toString() => 'SocketishFailure';
}
