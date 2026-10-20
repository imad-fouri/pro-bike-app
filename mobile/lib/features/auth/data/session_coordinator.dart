import 'dart:async';

import '../../../core/network/api_client.dart';
import '../../../core/storage/token_storage.dart';

/// Owns the current session's credentials across concurrent
/// login / refresh / logout calls.
///
/// This is the dedicated, non-recursive refresh path. It performs the
/// refresh-token rotation over its own callback-free HTTP client, so a
/// refresh can never trigger another refresh: the refresh endpoint answers
/// 401 and the failure propagates immediately instead of recursing.
///
/// It exists as a separate object (rather than logic inside [AuthRepository])
/// because of provider-graph direction: the repository watches the
/// authenticated API client, so resolving the repository back through the
/// client's own ref is a dependency cycle that Riverpod rejects. The
/// coordinator depends only on a plain HTTP client and token storage, so the
/// client's refresh callback can resolve it safely.
///
/// A plain Dart class with constructor-injected dependencies: it never reads
/// a provider itself, which is what keeps the graph acyclic.
class SessionCoordinator {
  SessionCoordinator({required this.http, required this.tokens});

  static const refreshPath = '/api/v1/auth/refresh';

  final ApiClient http;
  final TokenStorage tokens;

  /// Monotonic session generation.
  ///
  /// Incremented whenever a session starts or is deliberately ended. A refresh
  /// records the generation it was issued under and may only persist its
  /// result if the generation is unchanged, so a token pair landing after
  /// logout (or after a different account signed in) is discarded instead of
  /// resurrecting or overwriting a session the user already ended.
  int _generation = 0;

  /// Serializes credential writes so a generation check and the write it
  /// guards cannot interleave with logout's generation bump + clear.
  Future<void> _credentialWrites = Future<void>.value();

  /// Runs [action] after every previously queued credential write, appending
  /// this one to the queue. Errors are contained so one failed write cannot
  /// stall every later one.
  Future<T> _serialized<T>(Future<T> Function() action) {
    final previous = _credentialWrites;
    final completer = Completer<T>();
    _credentialWrites = completer.future.then<void>((_) {}, onError: (_) {});
    previous.then((_) async {
      try {
        completer.complete(await action());
      } catch (error, stack) {
        completer.completeError(error, stack);
      }
    });
    return completer.future;
  }

  /// Marks the start of a brand-new session (login/register). Any refresh
  /// left over from the previous session must no longer be allowed to write.
  void beginSession() {
    _generation++;
  }

  /// Atomically ends the session and wipes the stored credentials, then
  /// returns the refresh token that was stored so the caller can revoke it
  /// server-side on a best-effort basis.
  Future<String?> endSession() async {
    final refresh = await tokens.readRefresh();
    await _serialized(() async {
      _generation++;
      await tokens.clear();
    });
    return refresh;
  }

  /// Validates and persists a token pair.
  ///
  /// [expectedGeneration] is null for a session-establishing write (login).
  /// A refresh passes the generation it started under and is rejected if the
  /// session was ended or replaced while its request was in flight.
  Future<void> savePair(
    Map<String, dynamic> pair, {
    int? expectedGeneration,
  }) async {
    final access = pair['access_token'];
    final refresh = pair['refresh_token'];
    if (access is! String ||
        access.isEmpty ||
        refresh is! String ||
        refresh.isEmpty) {
      // Never persist a malformed credential such as the string "null".
      throw const ApiException(401, 'UNAUTHORIZED', 'Invalid session.');
    }
    await _serialized(() async {
      if (expectedGeneration != null && expectedGeneration != _generation) {
        throw const ApiException(401, 'SESSION_ENDED', 'Session ended.');
      }
      await tokens.save(access, refresh);
    });
  }

  /// Performs one refresh-token rotation: reads the stored refresh token,
  /// posts it to the refresh endpoint, and persists the rotated pair under
  /// the generation guard. This is the only production call that transmits
  /// the refresh token, and it goes only to the existing refresh endpoint.
  Future<void> refresh() async {
    final generation = _generation;
    final refresh = await tokens.readRefresh();
    if (refresh == null || refresh.isEmpty) {
      throw const ApiException(401, 'UNAUTHORIZED', 'No session.');
    }
    final pair = await http.post(refreshPath, {'refresh_token': refresh});
    await savePair(pair, expectedGeneration: generation);
  }
}
