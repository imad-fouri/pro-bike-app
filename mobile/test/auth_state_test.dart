import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Fake backend speaking the real envelope. No fake auth in prod code —
/// only here, as a test double for state behavior.
MockClient fakeBackend({bool failLogin = false}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (path.endsWith('/auth/login')) {
      if (failLogin) {
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"INVALID_CREDENTIALS","message":"bad"}}}',
          401,
        );
      }
      return http.Response('{"access_token":"a","refresh_token":"r"}', 200);
    }
    if (path.endsWith('/auth/me')) {
      return http.Response(
        '{"user":{"id":"00000000-0000-0000-0000-000000000001","email":"r@e.com",'
        '"status":"active","email_verified":false,"created_at":"2026-01-01T00:00:00Z",'
        '"last_login_at":null},"profile":{"display_name":"R","first_name":null,'
        '"last_name":null,"avatar_ref":null,"country":null,"city":null,'
        '"preferred_language":"en","timezone":"UTC","measurement_system":"metric",'
        '"cycling_experience":null,"disciplines":[],"training_goal":null,'
        '"profile_visibility":"public","activity_visibility":"friends"}}',
        200,
      );
    }
    if (path.endsWith('/auth/logout')) {
      return http.Response('{"status":"ok"}', 200);
    }
    return http.Response('not found', 404);
  });
}

ProviderContainer containerWith(MockClient backend) {
  final tokens = MemoryTokenStorage();
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: backend,
          accessToken: tokens.readAccess,
        ),
      ),
    ],
  );
}

void main() {
  test('login success → authenticated with user', () async {
    final c = containerWith(fakeBackend());
    final ok = await c
        .read(authProvider.notifier)
        .login('r@e.com', 'StrongPass123');
    expect(ok, isTrue);
    final s = c.read(authProvider);
    expect(s.status, AuthStatus.authenticated);
    expect(s.user!.email, 'r@e.com');
    c.dispose();
  });

  test('login failure → error state with backend code', () async {
    final c = containerWith(fakeBackend(failLogin: true));
    final ok = await c
        .read(authProvider.notifier)
        .login('r@e.com', 'WrongPass999');
    expect(ok, isFalse);
    final s = c.read(authProvider);
    expect(s.status, AuthStatus.error);
    expect(s.errorCode, 'INVALID_CREDENTIALS');
    c.dispose();
  });

  test('logout → unauthenticated and tokens wiped', () async {
    final c = containerWith(fakeBackend());
    await c.read(authProvider.notifier).login('r@e.com', 'StrongPass123');
    await c.read(authProvider.notifier).logout();
    expect(c.read(authProvider).status, AuthStatus.unauthenticated);
    expect(await c.read(tokenStorageProvider).readAccess(), isNull);
    c.dispose();
  });

  test('error envelope parsing maps backend codes', () {
    final e = ApiClient.parseErrorResponse(
      401,
      '{"error":{"code":"X","message":"m","details":{"code":"INVALID_CREDENTIALS","message":"bad"}}}',
    );
    expect(e, isA<ApiException>());
    expect(e.code, 'INVALID_CREDENTIALS');
    final net = ApiClient.parseErrorResponse(0, '');
    expect(net.code, 'ERROR');
  });
}
