import '../../../core/network/api_client.dart';
import '../domain/entitlement.dart';

/// Server-resolved entitlement state via the shared [ApiClient].
///
/// One GET, no request body, no client claim. The response is parsed into
/// typed models immediately; no screen keeps a `Map<String, dynamic>` around.
class EntitlementRepository {
  final ApiClient api;
  static const path = '/api/v1/me/entitlements';

  const EntitlementRepository(this.api);

  Future<EntitlementState> fetch() async {
    final body = await api.get(path, auth: true);
    return EntitlementState.fromJson(body);
  }
}
