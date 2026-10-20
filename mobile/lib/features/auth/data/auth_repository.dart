import 'dart:async';

import '../../../core/network/api_client.dart';
import '../../../core/storage/token_storage.dart';
import '../domain/auth_user.dart';
import 'session_coordinator.dart';

/// Talks to the real FastAPI backend. No fake auth: every method hits /api/v1.
///
/// Session mechanics (generation guard, serialized credential writes, and the
/// refresh-token rotation) live in [SessionCoordinator], which the repository
/// shares with the API client's refresh callback. The repository itself must
/// stay out of that callback's resolution path: it watches the authenticated
/// client, so resolving the repository back through the client's own ref is a
/// Riverpod dependency cycle.
class AuthRepository {
  final ApiClient api;
  final TokenStorage tokens;
  final SessionCoordinator session;

  AuthRepository(this.api, this.tokens, this.session);

  static const prefix = '/api/v1';

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
    session.beginSession();
    final pair = await api.post('$prefix/auth/login', {
      'email': email,
      'password': password,
    });
    await session.savePair(pair);
    return me();
  }

  Future<AuthUser> me() async {
    final me = await api.get('$prefix/auth/me', auth: true);
    return AuthUser.fromMe(me);
  }

  Future<void> refreshSession() => session.refresh();

  Future<void> logout() async {
    // End the session atomically first (generation bump + wipe in one
    // critical section), then revoke server-side best-effort: the local
    // session is already gone even if the network fails.
    final refresh = await session.endSession();
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
