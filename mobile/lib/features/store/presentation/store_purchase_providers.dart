import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_client.dart';
import '../../auth/presentation/auth_state.dart';
import '../../subscriptions/presentation/entitlement_providers.dart';
import '../data/store_repository.dart';
import '../domain/store_purchase.dart';

final storeRepositoryProvider = Provider<StoreRepository>(
  (ref) => StoreRepository(ref.watch(apiClientProvider)),
);

/// Provider-neutral purchase orchestration. No StoreKit, no Play Billing —
/// those SDKs arrive in a later workstream, and this service is the seam
/// they will plug into.
///
/// What this service never does, by construction:
/// - hold plan or entitlement state (the state enum cannot name "pro");
/// - persist anything (every rebuild starts idle; logout rebuilds);
/// - contact the server without a credential to verify.
///
/// The only mutation this service ever causes is a server-side
/// reconciliation followed by an entitlement-cache refresh: plan state
/// flows server → cache, never service → cache.
class StorePurchaseService extends Notifier<PurchaseOutcome> {
  @override
  PurchaseOutcome build() {
    // Watching auth is the session-safety mechanism: any login, logout, or
    // account switch rebuilds this service to idle, so one rider's pending
    // or verified attempt never survives into another session.
    ref.watch(authProvider);
    return PurchaseOutcome.idle;
  }

  /// Begin a store purchase for [productId].
  ///
  /// Today there is no store SDK, so there is no purchase to begin: this
  /// reports unavailable WITHOUT touching the network. Submitting an empty
  /// or invented credential to the verify endpoint would fabricate a
  /// purchase attempt, which is exactly what this workstream forbids.
  Future<PurchaseOutcome> purchase({required String productId}) async {
    state = PurchaseOutcome.unavailable;
    return state;
  }

  /// Ask the server to reconcile a store purchase, then refresh the
  /// entitlement cache from the server's answer.
  ///
  /// Reconciliation and refresh are one unit: a verified outcome with a
  /// stale cache would show the rider yesterday's plan, and a refreshed
  /// cache without verification would be a local grant. Either half alone
  /// is a bug, so they share one method.
  Future<PurchaseOutcome> restore({
    required String provider,
    required String productId,
    required String purchaseToken,
  }) async {
    if (!ref.read(authProvider).isAuthenticated) {
      state = PurchaseOutcome.idle;
      return state;
    }
    state = PurchaseOutcome.pending;
    try {
      await ref
          .read(storeRepositoryProvider)
          .restore(
            provider: provider,
            productId: productId,
            purchaseToken: purchaseToken,
          );
      await ref.read(entitlementProvider.notifier).refresh();
      state = PurchaseOutcome.verified;
    } on ApiException catch (e) {
      // 503/504: no provider behind the endpoint (or a slow one) — retrying
      // is pointless until the integration lands. Anything else, including
      // network failure, is a plain failure: entitlements untouched.
      state = (e.status == 503 || e.status == 504)
          ? PurchaseOutcome.unavailable
          : PurchaseOutcome.failed;
    }
    return state;
  }
}

final storePurchaseServiceProvider =
    NotifierProvider<StorePurchaseService, PurchaseOutcome>(
      StorePurchaseService.new,
    );
