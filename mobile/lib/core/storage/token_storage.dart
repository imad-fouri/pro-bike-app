import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Token storage contract. Implementations must use OS-backed secure storage
/// (iOS Keychain, Android EncryptedSharedPreferences/Keystore).
/// Never shared_preferences — tokens are credentials, not prefs.
abstract class TokenStorage {
  Future<String?> readAccess();
  Future<String?> readRefresh();
  Future<void> save(String access, String refresh);
  Future<void> clear();
}

class SecureTokenStorage implements TokenStorage {
  final FlutterSecureStorage _store;
  static const _accessKey = 'cc_access_token';
  static const _refreshKey = 'cc_refresh_token';

  SecureTokenStorage([FlutterSecureStorage? store])
    : _store = store ?? const FlutterSecureStorage();

  @override
  Future<String?> readAccess() => _store.read(key: _accessKey);

  @override
  Future<String?> readRefresh() => _store.read(key: _refreshKey);

  @override
  Future<void> save(String access, String refresh) async {
    await _store.write(key: _accessKey, value: access);
    await _store.write(key: _refreshKey, value: refresh);
  }

  @override
  Future<void> clear() async {
    await _store.delete(key: _accessKey);
    await _store.delete(key: _refreshKey);
  }
}

/// In-memory double for widget tests. Never used in production code.
class MemoryTokenStorage implements TokenStorage {
  String? access;
  String? refresh;
  @override
  Future<String?> readAccess() async => access;
  @override
  Future<String?> readRefresh() async => refresh;
  @override
  Future<void> save(String a, String r) async {
    access = a;
    refresh = r;
  }

  @override
  Future<void> clear() async {
    access = null;
    refresh = null;
  }
}
