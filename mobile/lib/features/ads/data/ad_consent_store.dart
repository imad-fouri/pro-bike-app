import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../domain/ad_policy.dart';

/// Advertising-consent storage contract: one choice per account.
///
/// Keys are namespaced by account id (`ad_consent.<accountId>`), which is
/// what makes cross-account leakage structurally impossible: there is no
/// device-global consent value to inherit, only per-account rows. A rider
/// who never chose stores nothing and reads back null (unknown).
abstract interface class AdConsentStore {
  /// The stored choice for [accountId], or null when none was recorded or
  /// the stored value no longer parses.
  Future<AdConsentState?> load(String accountId);

  /// Persist [state] for [accountId]. Storing `unknown` is allowed and
  /// simply records "no choice".
  Future<void> save(String accountId, AdConsentState state);
}

/// OS-backed consent storage (iOS Keychain, Android
/// EncryptedSharedPreferences/Keystore), reusing the vetted token-storage
/// mechanism. Consent is not a credential, but it is per-account private
/// state, and the secure store is the existing private-storage answer.
class SecureAdConsentStore implements AdConsentStore {
  final FlutterSecureStorage _store;

  SecureAdConsentStore([FlutterSecureStorage? store])
    : _store = store ?? const FlutterSecureStorage();

  static String _key(String accountId) => 'cc_ad_consent.$accountId';

  @override
  Future<AdConsentState?> load(String accountId) async {
    return decodeStored(await _store.read(key: _key(accountId)));
  }

  @override
  Future<void> save(String accountId, AdConsentState state) {
    return _store.write(key: _key(accountId), value: state.wire);
  }
}

/// One decode rule for every store implementation. A missing row reads as
/// absent; an unparseable row (future enum value, corruption) reads as absent
/// rather than crashing the session or inventing a choice. Two copies of
/// this rule would eventually disagree about what "no choice" means.
AdConsentState? decodeStored(String? raw) {
  if (raw == null) return null;
  final state = AdConsentState.parse(raw);
  return state == AdConsentState.unknown && raw != 'unknown' ? null : state;
}

/// In-memory double for widget tests. Never used in production code.
class MemoryAdConsentStore implements AdConsentStore {
  final Map<String, String> values = {};

  @override
  Future<AdConsentState?> load(String accountId) async {
    return decodeStored(values[accountId]);
  }

  @override
  Future<void> save(String accountId, AdConsentState state) async {
    values[accountId] = state.wire;
  }
}
