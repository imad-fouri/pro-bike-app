import '../../../core/network/api_client.dart';
import '../../../core/storage/token_storage.dart';
import '../domain/auth_user.dart';

/// Talks to the real FastAPI backend. No fake auth: every method hits /api/v1.
class AuthRepository {
  final ApiClient api;
  final TokenStorage tokens;
  static const prefix = '/api/v1';

  const AuthRepository(this.api, this.tokens);

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
    final refresh = await tokens.readRefresh();
    if (refresh == null || refresh.isEmpty) {
      throw const ApiException(401, 'UNAUTHORIZED', 'No session.');
    }
    // This is the only production call that transmits the refresh token, and
    // it goes only to the existing refresh endpoint.
    final pair = await api.post('$prefix/auth/refresh', {
      'refresh_token': refresh,
    });
    await _savePair(pair);
  }

  Future<void> _savePair(Map<String, dynamic> pair) async {
    final access = pair['access_token'];
    final refresh = pair['refresh_token'];
    if (access is! String ||
        access.isEmpty ||
        refresh is! String ||
        refresh.isEmpty) {
      // Never persist a malformed credential such as the string "null".
      throw const ApiException(401, 'UNAUTHORIZED', 'Invalid session.');
    }
    await tokens.save(access, refresh);
  }

  Future<void> logout() async {
    final refresh = await tokens.readRefresh();
    if (refresh != null) {
      try {
        await api.post('$prefix/auth/logout', {'refresh_token': refresh});
      } on ApiException {
        // Server revocation is best-effort; local wipe is mandatory.
      }
    }
    await tokens.clear();
  }

  Future<void> requestPasswordReset(String email) async {
    await api.post('$prefix/auth/password-reset/request', {'email': email});
  }
}
