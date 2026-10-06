import '../../../core/network/api_client.dart';
import '../../subscriptions/domain/entitlement.dart';

/// Server purchase reconciliation via the shared [ApiClient].
///
/// Two calls, both authenticated, both returning the rider's freshly
/// resolved entitlement state — never a secret, never another rider:
///
/// - `verify`: submit one store purchase credential for verification.
/// - `restore`: ask the server to reconcile (re-verify) a purchase.
///
/// The repository parses the state into the typed model immediately and
/// returns it; deciding what the state *means* belongs to the entitlement
/// cache, not here. A non-2xx surfaces as [ApiException] with the backend's
/// code (`UNKNOWN_PROVIDER`, `PROVIDER_UNAVAILABLE`, …), which the service
/// maps to outcomes.
class StoreRepository {
  final ApiClient api;
  static const verifyPath = '/api/v1/store/purchases/verify';
  static const restorePath = '/api/v1/store/purchases/restore';

  const StoreRepository(this.api);

  Future<EntitlementState> verify({
    required String provider,
    required String productId,
    required String purchaseToken,
  }) async {
    final body = await api.post(verifyPath, {
      'provider': provider,
      'product_id': productId,
      'purchase_token': purchaseToken,
    }, auth: true);
    return EntitlementState.fromJson(body);
  }

  Future<EntitlementState> restore({
    required String provider,
    required String productId,
    required String purchaseToken,
  }) async {
    final body = await api.post(restorePath, {
      'provider': provider,
      'product_id': productId,
      'purchase_token': purchaseToken,
    }, auth: true);
    return EntitlementState.fromJson(body);
  }
}
