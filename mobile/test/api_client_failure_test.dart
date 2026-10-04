import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Transport-level failure taxonomy and retry policy for the single HTTP layer.
///
/// Each test pins a boundary the UI depends on: malformed successes become
/// ApiException instead of FormatException, network failures are distinguishable
/// from HTTP failures, non-401s never trigger a refresh, and multipart/binary
/// paths obey the same retry rules as JSON.
void main() {
  group('success-body parsing', () {
    test('malformed JSON on 200 is an ApiException, not a crash', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient((_) async => http.Response('{not json', 200)),
      );

      await expectLater(
        api.get('/thing'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.status, 'status', 200)
              .having((e) => e.code, 'code', 'INVALID_RESPONSE'),
        ),
      );
    });

    test('JSON array where an object is required is an ApiException', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient((_) async => http.Response('[1,2,3]', 200)),
      );

      await expectLater(
        api.get('/thing'),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'INVALID_RESPONSE'),
        ),
      );
    });

    test('empty 2xx body is an empty map, not an error', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient((_) async => http.Response('', 204)),
      );
      expect(await api.get('/thing'), <String, dynamic>{});
    });

    test('malformed multipart success body is an ApiException', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient(
          (_) async => http.Response('<html>oops</html>', 200),
        ),
      );

      await expectLater(
        api.postMultipart(
          '/upload',
          fileField: 'file',
          filename: 'ride.gpx',
          bytes: [1, 2, 3],
        ),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'INVALID_RESPONSE'),
        ),
      );
    });

    test('non-JSON error body degrades to a generic ApiException', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient((_) async => http.Response('<html>502</html>', 502)),
      );

      await expectLater(
        api.get('/thing'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.status, 'status', 502)
              .having((e) => e.code, 'code', 'ERROR'),
        ),
      );
    });
  });

  group('network taxonomy', () {
    test('a real timeout produces NETWORK_TIMEOUT', () async {
      // Never completes: this exercises the actual timer, so the test asserts the
      // production boundary rather than a hand-thrown TimeoutException.
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient((_) => Completer<http.Response>().future),
      );

      await expectLater(
        api.get('/hang'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.status, 'status', 0)
              .having((e) => e.code, 'code', 'NETWORK_TIMEOUT'),
        ),
      );
    }, timeout: const Timeout(Duration(seconds: 60)));

    test(
      'socket failure is reported as NETWORK_ERROR, not an HTTP error',
      () async {
        final api = ApiClient(
          baseUrl: 'http://test',
          client: MockClient((_) async => throw const _Offline()),
        );

        await expectLater(
          api.get('/thing'),
          throwsA(
            isA<ApiException>()
                .having((e) => e.status, 'status', 0)
                .having((e) => e.code, 'code', 'NETWORK_ERROR'),
          ),
        );
      },
    );

    test('binary download maps a network failure the same way', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient((_) async => throw const _Offline()),
      );

      await expectLater(
        api.getBytes('/export.gpx'),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'NETWORK_ERROR'),
        ),
      );
    });

    test('binary download parses the error envelope', () async {
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient(
          (_) async => http.Response(
            '{"error":{"code":"NOT_FOUND","message":"No ride."}}',
            404,
          ),
        ),
      );

      await expectLater(
        api.getBytes('/export.gpx'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.status, 'status', 404)
              .having((e) => e.message, 'message', 'No ride.'),
        ),
      );
    });
  });

  group('retry policy', () {
    test('non-401 statuses never trigger a refresh', () async {
      for (final status in [400, 403, 404, 409, 422, 429, 500, 503]) {
        var refreshCalls = 0;
        final tokens = MemoryTokenStorage();
        await tokens.save('access', 'refresh');
        final api = ApiClient(
          baseUrl: 'http://test',
          client: MockClient(
            (_) async =>
                http.Response('{"error":{"code":"X","message":"m"}}', status),
          ),
          accessToken: tokens.readAccess,
          refreshAccessToken: () async => refreshCalls++,
        );

        await expectLater(
          api.post('/thing', {}, auth: true),
          throwsA(
            isA<ApiException>().having((e) => e.status, 'status', status),
          ),
        );
        expect(refreshCalls, 0, reason: 'status $status must not refresh');
      }
    });

    test('unauthenticated requests never refresh even on 401', () async {
      var refreshCalls = 0;
      final api = ApiClient(
        baseUrl: 'http://test',
        client: MockClient(
          (_) async =>
              http.Response('{"error":{"code":"X","message":"m"}}', 401),
        ),
        refreshAccessToken: () async => refreshCalls++,
      );

      await expectLater(
        api.get('/public'),
        throwsA(isA<ApiException>().having((e) => e.status, 'status', 401)),
      );
      expect(refreshCalls, 0);
    });

    test('session endpoints cannot recurse into a refresh', () async {
      for (final path in ['/api/v1/auth/refresh', '/api/v1/auth/logout']) {
        var refreshCalls = 0;
        final tokens = MemoryTokenStorage();
        await tokens.save('access', 'refresh');
        final api = ApiClient(
          baseUrl: 'http://test',
          client: MockClient(
            (_) async =>
                http.Response('{"error":{"code":"X","message":"m"}}', 401),
          ),
          accessToken: tokens.readAccess,
          refreshAccessToken: () async => refreshCalls++,
        );

        await expectLater(
          api.post(path, {}, auth: true),
          throwsA(isA<ApiException>()),
        );
        expect(refreshCalls, 0, reason: '$path must not refresh');
      }
    });

    test(
      'multipart retries once after refresh and preserves the payload',
      () async {
        final tokens = MemoryTokenStorage();
        await tokens.save('expired-access', 'refresh');
        var uploadCalls = 0;
        var refreshCalls = 0;
        final backend = MockClient((req) async {
          if (req.url.path.endsWith('/auth/refresh')) {
            refreshCalls++;
            return http.Response(
              '{"access_token":"access-two","refresh_token":"refresh-two"}',
              200,
            );
          }
          uploadCalls++;
          if (req.headers['authorization'] != 'Bearer access-two') {
            return http.Response(
              '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
              401,
            );
          }
          expect(req.headers['content-type'], contains('multipart/form-data'));
          final body = utf8.decode(req.bodyBytes, allowMalformed: true);
          expect(body, contains('ride.gpx'));
          expect(body, contains('name="name"'));
          return http.Response('{"id":"ride-1"}', 200);
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

        final result = await api.postMultipart(
          '/api/v1/rides/import',
          fileField: 'file',
          filename: 'ride.gpx',
          bytes: [60, 120, 109, 108],
          fields: {'name': 'bike'},
          auth: true,
        );

        expect(result, {'id': 'ride-1'});
        expect(uploadCalls, 2);
        expect(refreshCalls, 1);
      },
    );

    test('binary download retries once after refresh', () async {
      final tokens = MemoryTokenStorage();
      await tokens.save('expired-access', 'refresh');
      var exportCalls = 0;
      var refreshCalls = 0;
      final backend = MockClient((req) async {
        if (req.url.path.endsWith('/auth/refresh')) {
          refreshCalls++;
          return http.Response(
            '{"access_token":"access-two","refresh_token":"refresh-two"}',
            200,
          );
        }
        exportCalls++;
        if (req.headers['authorization'] != 'Bearer access-two') {
          return http.Response(
            '{"error":{"code":"X","message":"m","details":{"code":"UNAUTHORIZED","message":"Token expired."}}}',
            401,
          );
        }
        return http.Response.bytes(
          [0x3c, 0x67, 0x70, 0x78],
          200,
          headers: {'content-disposition': 'attachment; filename="ride.gpx"'},
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
      );

      final result = await api.getBytes('/api/v1/rides/r1/export', auth: true);

      expect(result.bytes, [0x3c, 0x67, 0x70, 0x78]);
      expect(result.headers['content-disposition'], contains('ride.gpx'));
      expect(exportCalls, 2);
      expect(refreshCalls, 1);
    });

    test(
      'a rejected multipart retry surfaces the error, not a silent success',
      () async {
        final tokens = MemoryTokenStorage();
        await tokens.save('expired-access', 'refresh');
        var uploadCalls = 0;
        final backend = MockClient((req) async {
          if (req.url.path.endsWith('/auth/refresh')) {
            return http.Response('{}', 200);
          }
          uploadCalls++;
          return http.Response(
            '{"error":{"code":"BAD_GPX","message":"Bad file."}}',
            422,
          );
        });
        final api = ApiClient(
          baseUrl: 'http://test',
          client: backend,
          accessToken: tokens.readAccess,
          refreshAccessToken: () async {},
        );

        await expectLater(
          api.postMultipart(
            '/api/v1/rides/import',
            fileField: 'file',
            filename: 'bad.gpx',
            bytes: [1],
            auth: true,
          ),
          throwsA(
            isA<ApiException>()
                .having((e) => e.status, 'status', 422)
                .having((e) => e.message, 'message', 'Bad file.'),
          ),
        );
        expect(uploadCalls, 1);
      },
    );
  });
}

class _Offline implements Exception {
  const _Offline();
}
