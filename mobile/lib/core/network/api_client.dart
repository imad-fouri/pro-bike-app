import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// Parsed API error. Matches backend envelope {error:{code,message,details}}.
class ApiException implements Exception {
  final int status;
  final String code;
  final String message;
  const ApiException(this.status, this.code, this.message);
  @override
  String toString() => 'ApiException($status $code: $message)';
}

/// Single reusable HTTP layer. No screen talks to http directly.
/// Token attach + error parsing in one place; replaceable in tests via [client].
///
/// When [refreshAccessToken] is supplied, authenticated requests recover from an
/// expired access token centrally: a 401 triggers one serialized refresh, then
/// retries the original request exactly once. Concurrent 401s share the same
/// in-flight refresh instead of rotating the refresh token independently.
class ApiClient {
  final String baseUrl;
  final http.Client client;
  final Future<String?> Function()? accessToken;
  final Future<void> Function()? refreshAccessToken;
  final Future<void> Function()? onAuthFailure;
  Future<void>? _refreshInFlight;
  static const timeout = Duration(seconds: 15);

  ApiClient({
    required this.baseUrl,
    required this.client,
    this.accessToken,
    this.refreshAccessToken,
    this.onAuthFailure,
  });

  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body, {
    bool auth = false,
  }) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _send('POST', path, body: body, auth: auth),
    );
  }

  Future<Map<String, dynamic>> get(String path, {bool auth = false}) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _send('GET', path, auth: auth),
    );
  }

  Future<Map<String, dynamic>> patch(
    String path,
    Map<String, dynamic> body, {
    bool auth = false,
  }) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _send('PATCH', path, body: body, auth: auth),
    );
  }

  /// PUT for whole-resource replacement (a partial update still sends only the
  /// fields it means to change).
  Future<Map<String, dynamic>> put(
    String path,
    Map<String, dynamic> body, {
    bool auth = false,
  }) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _send('PUT', path, body: body, auth: auth),
    );
  }

  Future<Map<String, dynamic>> delete(String path, {bool auth = false}) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _send('DELETE', path, auth: auth),
    );
  }

  /// Multipart upload (GPX import). One file part + plain text fields.
  Future<Map<String, dynamic>> postMultipart(
    String path, {
    required String fileField,
    required String filename,
    required List<int> bytes,
    Map<String, String> fields = const {},
    bool auth = false,
  }) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _postMultipartOnce(
        path,
        fileField: fileField,
        filename: filename,
        bytes: bytes,
        fields: fields,
        auth: auth,
      ),
    );
  }

  Future<Map<String, dynamic>> _postMultipartOnce(
    String path, {
    required String fileField,
    required String filename,
    required List<int> bytes,
    Map<String, String> fields = const {},
    bool auth = false,
  }) async {
    final headers = <String, String>{};
    if (auth) {
      headers['Authorization'] = 'Bearer ${await _token()}';
    }
    final request = http.MultipartRequest('POST', Uri.parse('$baseUrl$path'))
      ..headers.addAll(headers)
      ..fields.addAll(fields)
      ..files.add(
        http.MultipartFile.fromBytes(fileField, bytes, filename: filename),
      );
    final streamed = await client.send(request).timeout(timeout);
    final body = await streamed.stream.bytesToString();
    final res = http.Response(
      body,
      streamed.statusCode,
      headers: streamed.headers,
    );
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return body.isEmpty
          ? <String, dynamic>{}
          : _decodeObject(res.statusCode, body);
    }
    throw _parseError(res.statusCode, body);
  }

  /// Binary GET (GPX export): payload + headers (content-disposition).
  Future<({List<int> bytes, Map<String, String> headers})> getBytes(
    String path, {
    bool auth = false,
  }) async {
    return _withAuthRefresh(
      auth: auth,
      path: path,
      attempt: () => _getBytesOnce(path, auth: auth),
    );
  }

  Future<({List<int> bytes, Map<String, String> headers})> _getBytesOnce(
    String path, {
    bool auth = false,
  }) async {
    final headers = <String, String>{};
    if (auth) {
      headers['Authorization'] = 'Bearer ${await _token()}';
    }
    final uri = Uri.parse('$baseUrl$path');
    late http.Response res;
    try {
      res = await client.get(uri, headers: headers).timeout(timeout);
    } on TimeoutException {
      throw const ApiException(0, 'NETWORK_TIMEOUT', 'Request timed out.');
    } on Exception {
      throw const ApiException(0, 'NETWORK_ERROR', 'Network error.');
    }
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return (bytes: res.bodyBytes, headers: res.headers);
    }
    throw _parseError(res.statusCode, res.body);
  }

  Future<String> _token() async {
    final token = await accessToken?.call();
    if (token == null) {
      throw const ApiException(401, 'UNAUTHORIZED', 'Not authenticated.');
    }
    return token;
  }

  Future<Map<String, dynamic>> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = false,
  }) async {
    final headers = {'Content-Type': 'application/json'};
    if (auth) {
      headers['Authorization'] = 'Bearer ${await _token()}';
    }
    late http.Response res;
    try {
      final uri = Uri.parse('$baseUrl$path');
      final encoded = body == null ? null : jsonEncode(body);
      switch (method) {
        case 'POST':
          res = await client
              .post(uri, headers: headers, body: encoded)
              .timeout(timeout);
        case 'PATCH':
          res = await client
              .patch(uri, headers: headers, body: encoded)
              .timeout(timeout);
        case 'PUT':
          res = await client
              .put(uri, headers: headers, body: encoded)
              .timeout(timeout);
        case 'DELETE':
          res = await client.delete(uri, headers: headers).timeout(timeout);
        default:
          res = await client.get(uri, headers: headers).timeout(timeout);
      }
    } on TimeoutException {
      throw const ApiException(0, 'NETWORK_TIMEOUT', 'Request timed out.');
    } on Exception {
      throw const ApiException(0, 'NETWORK_ERROR', 'Network error.');
    }
    if (res.statusCode >= 200 && res.statusCode < 300) {
      if (res.body.isEmpty) return {};
      return _decodeObject(res.statusCode, res.body);
    }
    throw _parseError(res.statusCode, res.body);
  }

  /// Decodes a success body that must be a JSON object.
  ///
  /// A 2xx carrying HTML, a bare array, or truncated JSON is a contract
  /// violation, not a programming error: it must surface as an ApiException so
  /// callers' `catch (ApiException)` handles it instead of a raw
  /// FormatException/TypeError escaping through the UI.
  Map<String, dynamic> _decodeObject(int status, String body) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic>) return decoded;
      throw const FormatException('Expected a JSON object.');
    } on ApiException {
      rethrow;
    } on Exception {
      throw ApiException(
        status,
        'INVALID_RESPONSE',
        'Unexpected response from server.',
      );
    }
  }

  /// Central authenticated-request recovery.
  ///
  /// Only authenticated requests are eligible, and the authentication endpoints
  /// themselves are excluded so a failed refresh cannot trigger another refresh.
  /// On 401, one serialized refresh is attempted and the original request is
  /// retried exactly once. A second 401 is terminal for that request.
  Future<T> _withAuthRefresh<T>({
    required bool auth,
    required String path,
    required Future<T> Function() attempt,
  }) async {
    try {
      return await attempt();
    } on ApiException catch (e) {
      if (!auth || e.status != 401 || _isSessionEndpoint(path)) rethrow;
      final refreshed = await _refreshOnce();
      if (!refreshed) rethrow;
      try {
        return await attempt();
      } on ApiException catch (retryError) {
        if (retryError.status == 401) {
          try {
            await onAuthFailure?.call();
          } catch (_) {
            // Session cleanup is best-effort; preserve the auth error.
          }
        }
        rethrow;
      }
    }
  }

  bool _isSessionEndpoint(String path) {
    // The refresh call itself must use auth: false, but exclude these paths
    // defensively so session management can never recurse.
    return path.endsWith('/auth/refresh') || path.endsWith('/auth/logout');
  }

  Future<bool> _refreshOnce() async {
    final refresh = refreshAccessToken;
    if (refresh == null) return false;
    final inFlight = _refreshInFlight;
    if (inFlight != null) {
      try {
        await inFlight;
        return true;
      } catch (_) {
        return false;
      }
    }
    final future = refresh();
    _refreshInFlight = future;
    try {
      await future;
      return true;
    } catch (_) {
      try {
        await onAuthFailure?.call();
      } catch (_) {
        // Session cleanup is best-effort; preserve the auth error.
      }
      return false;
    } finally {
      _refreshInFlight = null;
    }
  }

  /// Exported for unit tests: envelope → ApiException mapping.
  /// Never constructs http.Response (status 0 = network failure is valid here).
  static ApiException parseErrorResponse(int status, String body) {
    return _parseError(status, body);
  }

  static ApiException _parseError(int status, String body) {
    try {
      final decoded = jsonDecode(body) as Map<String, dynamic>;
      final err = decoded['error'] as Map<String, dynamic>?;
      if (err != null) {
        final detail = err['details'];
        // Auth endpoints use HTTPException(detail={code,message}).
        if (detail is Map && detail['message'] is String) {
          return ApiException(
            status,
            '${detail['code'] ?? 'ERROR'}',
            '${detail['message']}',
          );
        }
        return ApiException(status, 'ERROR', '${err['message']}');
      }
      // FastAPI HTTPException serializes as {detail: ...}.
      final detail = decoded['detail'];
      if (detail is Map && detail['message'] is String) {
        return ApiException(
          status,
          '${detail['code'] ?? 'ERROR'}',
          '${detail['message']}',
        );
      }
      return ApiException(status, 'ERROR', '$detail');
    } on ApiException {
      rethrow;
    } on Exception {
      return ApiException(status, 'ERROR', 'Request failed.');
    }
  }
}
