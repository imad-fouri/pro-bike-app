import 'dart:convert';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Central refresh/retry behavior for the single HTTP layer.
///
/// These tests use a fake transport, not production auth. They prove the exact
/// production failure modes: expired access, concurrent 401s, refresh-token
/// rotation, refresh failure, and retry-loop prevention.
void main() {
  test('expired access token refreshes once and retries the request', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('expired-access', 'valid-refresh');
    var protectedCalls = 0;
    var refreshCalls = 0;
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        refreshCalls++;
        return http.Response(
          '{"access_token":"new-access","refresh_token":"new-refresh"}',
          200,
        );
      }
      if (req.url.path.endsWith('/protected')) {
        protectedCalls++;
        if (req.headers['authorization'] == 'Bearer new-access') {
          return http.Response('{"ok":true}', 200);
        }
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
          401,
        );
      }
      return http.Response('not found', 404);
    });
    final raw = ApiClient(baseUrl: 'http://test', client: backend);
    final api = ApiClient(
      baseUrl: 'http://test',
      client: backend,
      accessToken: tokens.readAccess,
      refreshAccessToken: () async {
        final pair = await raw.post('/api/v1/auth/refresh', {
          'refresh_token': (await tokens.readRefresh())!,
        });
        await tokens.save(
          pair['access_token'] as String,
          pair['refresh_token'] as String,
        );
      },
    );

    final result = await api.get('/protected', auth: true);

    expect(result, {'ok': true});
    expect(refreshCalls, 1);
    expect(protectedCalls, 2);
    expect(await tokens.readAccess(), 'new-access');
    expect(await tokens.readRefresh(), 'new-refresh');
  });

  test('three simultaneous 401s share one refresh', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('expired-access', 'valid-refresh');
    var protectedCalls = 0;
    var refreshCalls = 0;
    var refreshed = false;
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        refreshCalls++;
        await Future.delayed(const Duration(milliseconds: 50));
        refreshed = true;
        return http.Response(
          '{"access_token":"new-access","refresh_token":"new-refresh"}',
          200,
        );
      }
      if (req.url.path.endsWith('/protected')) {
        protectedCalls++;
        if (refreshed && req.headers['authorization'] == 'Bearer new-access') {
          return http.Response('{"ok":true}', 200);
        }
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
          401,
        );
      }
      return http.Response('not found', 404);
    });
    final raw = ApiClient(baseUrl: 'http://test', client: backend);
    final api = ApiClient(
      baseUrl: 'http://test',
      client: backend,
      accessToken: tokens.readAccess,
      refreshAccessToken: () async {
        final pair = await raw.post('/api/v1/auth/refresh', {
          'refresh_token': (await tokens.readRefresh())!,
        });
        await tokens.save(
          pair['access_token'] as String,
          pair['refresh_token'] as String,
        );
      },
    );

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
    expect(refreshCalls, 1);
    expect(protectedCalls, 6);
  });

  test('rotated refresh token is used for the next refresh', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('expired-access', 'refresh-one');
    var currentRefresh = 'refresh-one';
    final seenRefreshTokens = <String>[];
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        final presented = _json(req)['refresh_token'] as String;
        seenRefreshTokens.add(presented);
        if (presented != currentRefresh) {
          return http.Response(
            '{"error":{"code":"X","message":"m","details":{"code":"INVALID_REFRESH","message":"Invalid refresh token."}}}',
            401,
          );
        }
        currentRefresh = 'refresh-two';
        return http.Response(
          '{"access_token":"access-two","refresh_token":"refresh-two"}',
          200,
        );
      }
      if (req.url.path.endsWith('/protected')) {
        if (req.headers['authorization'] == 'Bearer access-two') {
          return http.Response('{"ok":true}', 200);
        }
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
          401,
        );
      }
      return http.Response('not found', 404);
    });
    final raw = ApiClient(baseUrl: 'http://test', client: backend);
    final api = ApiClient(
      baseUrl: 'http://test',
      client: backend,
      accessToken: tokens.readAccess,
      refreshAccessToken: () async {
        final pair = await raw.post('/api/v1/auth/refresh', {
          'refresh_token': (await tokens.readRefresh())!,
        });
        await tokens.save(
          pair['access_token'] as String,
          pair['refresh_token'] as String,
        );
      },
    );

    await api.get('/protected', auth: true);
    await tokens.save('expired-again', 'refresh-two');
    await api.get('/protected', auth: true);

    expect(seenRefreshTokens, ['refresh-one', 'refresh-two']);
    expect(await tokens.readRefresh(), 'refresh-two');
  });

  test('failed refresh clears credentials and does not retry forever', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('expired-access', 'revoked-refresh');
    var protectedCalls = 0;
    var refreshCalls = 0;
    var failures = 0;
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        refreshCalls++;
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"INVALID_REFRESH","message":"Invalid refresh token."}}}',
          401,
        );
      }
      if (req.url.path.endsWith('/protected')) {
        protectedCalls++;
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
          401,
        );
      }
      return http.Response('not found', 404);
    });
    final raw = ApiClient(baseUrl: 'http://test', client: backend);
    final api = ApiClient(
      baseUrl: 'http://test',
      client: backend,
      accessToken: tokens.readAccess,
      refreshAccessToken: () async {
        final pair = await raw.post('/api/v1/auth/refresh', {
          'refresh_token': (await tokens.readRefresh())!,
        });
        await tokens.save(
          pair['access_token'] as String,
          pair['refresh_token'] as String,
        );
      },
      onAuthFailure: () async {
        failures++;
        await tokens.clear();
      },
    );

    await expectLater(
      api.get('/protected', auth: true),
      throwsA(isA<ApiException>()),
    );

    expect(refreshCalls, 1);
    expect(protectedCalls, 1);
    expect(failures, 1);
    expect(await tokens.readAccess(), isNull);
    expect(await tokens.readRefresh(), isNull);
  });

  test('a retry rejected after refresh is terminal, not a loop', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('expired-access', 'valid-refresh');
    var protectedCalls = 0;
    var refreshCalls = 0;
    var failures = 0;
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/refresh')) {
        refreshCalls++;
        return http.Response(
          '{"access_token":"new-access","refresh_token":"new-refresh"}',
          200,
        );
      }
      protectedCalls++;
      return http.Response(
        '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Account inactive."}}}',
        401,
      );
    });
    final raw = ApiClient(baseUrl: 'http://test', client: backend);
    final api = ApiClient(
      baseUrl: 'http://test',
      client: backend,
      accessToken: tokens.readAccess,
      refreshAccessToken: () async {
        final pair = await raw.post('/api/v1/auth/refresh', {
          'refresh_token': (await tokens.readRefresh())!,
        });
        await tokens.save(
          pair['access_token'] as String,
          pair['refresh_token'] as String,
        );
      },
      onAuthFailure: () async {
        failures++;
        await tokens.clear();
      },
    );

    await expectLater(
      api.get('/protected', auth: true),
      throwsA(isA<ApiException>()),
    );

    expect(refreshCalls, 1);
    expect(protectedCalls, 2);
    expect(failures, 1);
  });

  test('session expiry clears credentials and requires re-login', () async {
    final tokens = MemoryTokenStorage();
    await tokens.save('expired-access', 'revoked-refresh');
    final backend = MockClient((req) async {
      if (req.url.path.endsWith('/auth/logout')) {
        return http.Response('{"status":"ok"}', 200);
      }
      return http.Response('not found', 404);
    });
    final container = ProviderContainer(
      overrides: [
        tokenStorageProvider.overrideWithValue(tokens),
        apiClientProvider.overrideWithValue(
          ApiClient(baseUrl: 'http://test', client: backend),
        ),
      ],
    );

    await container.read(authProvider.notifier).handleSessionExpired();

    expect(container.read(authProvider).status, AuthStatus.unauthenticated);
    expect(await container.read(tokenStorageProvider).readAccess(), isNull);
    expect(await container.read(tokenStorageProvider).readRefresh(), isNull);
    container.dispose();
  });
}

Map<String, dynamic> _json(http.Request req) {
  // MockClient exposes the client's already-encoded JSON request body.
  return jsonDecode(req.body) as Map<String, dynamic>;
}
