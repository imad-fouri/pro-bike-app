import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../../core/config/app_config.dart';
import '../../../core/network/api_client.dart';
import '../../../core/storage/token_storage.dart';
import '../data/auth_repository.dart';
import '../domain/auth_user.dart';

/// Auth states: unknown/loading → unauthenticated | authenticated | error.
enum AuthStatus { unknown, loading, unauthenticated, authenticated, error }

class AuthState {
  final AuthStatus status;
  final AuthUser? user;
  final String? errorCode;
  const AuthState(this.status, {this.user, this.errorCode});

  factory AuthState.unknown() => const AuthState(AuthStatus.unknown);
  bool get isAuthenticated => status == AuthStatus.authenticated;
}

final tokenStorageProvider = Provider<TokenStorage>(
  (_) => SecureTokenStorage(),
);

final Provider<ApiClient> apiClientProvider = Provider<ApiClient>((ref) {
  final tokens = ref.watch(tokenStorageProvider);
  return ApiClient(
    baseUrl: AppConfig.current.apiBase,
    client: http.Client(),
    accessToken: tokens.readAccess,
    refreshAccessToken: () => ref.read(authRepositoryProvider).refreshSession(),
    onAuthFailure: () => ref.read(authProvider.notifier).handleSessionExpired(),
  );
});

final authRepositoryProvider = Provider<AuthRepository>((ref) {
  return AuthRepository(
    ref.watch(apiClientProvider),
    ref.watch(tokenStorageProvider),
  );
});

/// App-start flow: Restore Session → authenticated ⇒ Home, else ⇒ Login.
class AuthNotifier extends Notifier<AuthState> {
  @override
  AuthState build() {
    Future.microtask(restore);
    return AuthState.unknown();
  }

  Future<void> restore() async {
    state = const AuthState(AuthStatus.loading);
    try {
      final user = await ref.read(authRepositoryProvider).me();
      state = AuthState(AuthStatus.authenticated, user: user);
    } on Exception {
      state = const AuthState(AuthStatus.unauthenticated);
    }
  }

  Future<bool> login(String email, String password) async {
    state = const AuthState(AuthStatus.loading);
    try {
      final user = await ref
          .read(authRepositoryProvider)
          .login(email: email, password: password);
      state = AuthState(AuthStatus.authenticated, user: user);
      return true;
    } on Exception catch (e) {
      state = AuthState(AuthStatus.error, errorCode: _code(e));
      return false;
    }
  }

  Future<bool> register(
    String email,
    String password,
    String displayName,
  ) async {
    state = const AuthState(AuthStatus.loading);
    try {
      final user = await ref
          .read(authRepositoryProvider)
          .register(email: email, password: password, displayName: displayName);
      state = AuthState(AuthStatus.authenticated, user: user);
      return true;
    } on Exception catch (e) {
      state = AuthState(AuthStatus.error, errorCode: _code(e));
      return false;
    }
  }

  Future<void> logout() async {
    await ref.read(authRepositoryProvider).logout();
    state = const AuthState(AuthStatus.unauthenticated);
  }

  /// Terminal session failure: clear credentials, then require re-login.
  ///
  /// Used when refresh fails or a freshly refreshed access token is still
  /// rejected. Server revocation is best-effort inside [AuthRepository.logout];
  /// local credential removal is mandatory here.
  Future<void> handleSessionExpired() async {
    try {
      await ref.read(authRepositoryProvider).logout();
    } catch (_) {
      try {
        await ref.read(tokenStorageProvider).clear();
      } catch (_) {
        // Local cleanup is best-effort; authentication must still end.
      }
    }
    state = const AuthState(AuthStatus.unauthenticated);
  }

  static String _code(Object e) => e is ApiException ? e.code : 'ERROR';
}

final authProvider = NotifierProvider<AuthNotifier, AuthState>(
  AuthNotifier.new,
);
