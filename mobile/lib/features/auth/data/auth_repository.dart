import 'dart:async';

import '../../../core/network/api_client.dart';
import '../../../core/storage/token_storage.dart';
import '../domain/auth_user.dart';

/// Talks to the real FastAPI backend. No fake auth: every method hits /api/v1.
class AuthRepository {
  final ApiClient api;
  final TokenStorage tokens;
  static const prefix = '/api/v1';

  /// Monotonic session generation.
  ///
  /// Incremented whenever a session starts or is deliberately ended. A refresh
  /// records the generation it was issued under and may only persist its result
  /// if the generation is unchanged, so a token pair landing after logout (or
  /// after a different account signed in) is discarded instead of resurrecting
  /// or overwriting a session the user already ended.
  int _generation = 0;

  /// Serializes credential writes so a generation check and the write it guards
  /// cannot interleave with logout's generation bump + clear.
  Future<void> _credentialWrites = Future<void>.value();

  AuthRepository(this.api, this.tokens);

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

  Future<AuthUser> register({
    required String email,
    required String password,
    required String displayName,
  }) async {
    final me = await api.post('$prefix/auth/register', {
      'email': email,
      'password': password,
      'password_confirm': password,
      'display_name': displayName,
    });
    // Backend register returns MeOut; immediately log in to mint tokens.
    await login(email: email, password: password);
    return AuthUser.fromMe(me);
  }

  Future<AuthUser> login({
    required String email,
    required String password,
  }) async {
    // Signing in starts a new session: any refresh left over from the previous
    // one must no longer be allowed to write.
    _generation++;
    final pair = await api.post('$prefix/auth/login', {
      'email': email,
      'password': password,
    });
    await _savePair(pair);
    return me();
  }

  Future<AuthUser> me() async {
    final me = await api.get('$prefix/auth/me', auth: true);
    return AuthUser.fromMe(me);
  }

  Future<void> refreshSession() async {
    final generation = _generation;
    final refresh = await tokens.readRefresh();
    if (refresh == null || refresh.isEmpty) {
      throw const ApiException(401, 'UNAUTHORIZED', 'No session.');
    }
    // This is the only production call that transmits the refresh token, and
    // it goes only to the existing refresh endpoint.
    final pair = await api.post('$prefix/auth/refresh', {
      'refresh_token': refresh,
    });
    await _savePair(pair, expectedGeneration: generation);
  }

  /// [expectedGeneration] is null for a session-establishing write (login).
  /// A refresh passes the generation it started under and is rejected if the
  /// session was ended or replaced while its request was in flight.
  Future<void> _savePair(
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

  Future<void> logout() async {
    final refresh = await tokens.readRefresh();
    // End the session atomically: bumping the generation and wiping the tokens
    // in one critical section leaves no window in which a late refresh pair can
    // be persisted after the clear.
    await _serialized(() async {
      _generation++;
      await tokens.clear();
    });
    if (refresh != null) {
      try {
        await api.post('$prefix/auth/logout', {'refresh_token': refresh});
      } on ApiException {
        // Server revocation is best-effort; the local session is already gone.
      }
    }
  }

  Future<void> requestPasswordReset(String email) async {
    await api.post('$prefix/auth/password-reset/request', {'email': email});
  }
}
