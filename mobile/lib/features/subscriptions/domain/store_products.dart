library;

import 'entitlement.dart';

/// Provider-neutral store products. Display and mapping vocabulary only:
/// the server remains authoritative for what a rider may actually use.
///
/// The ids below are PROVISIONAL. They are internal names chosen to be
/// plausible on both stores, not reserved App Store or Play product ids.
/// Claiming a store id requires the store's own registration flow, which has
/// not happened. When it does, these constants change to the registered ids
/// and nothing else changes — adapters map store products through
/// [StoreCatalog.byId], never by string comparison scattered in UI code.
///
/// There are deliberately no prices here. Prices are store-owned, localized,
/// and changeable without notice; storing them client-side would make the
/// client authoritative about money, which it must never be.

/// Billing cadence. Monthly/yearly are the two rhythms the product intends
/// to offer; anything else degrades to unknown rather than guessing.
enum BillingPeriod {
  monthly,
  yearly,
  unknown;

  String get wire => switch (this) {
    BillingPeriod.monthly => 'monthly',
    BillingPeriod.yearly => 'yearly',
    BillingPeriod.unknown => 'unknown',
  };
}

/// One purchasable product as the app understands it.
class StoreProduct {
  /// Provisional provider-neutral id (see library docs).
  final String id;
  final SubscriptionPlan plan;
  final BillingPeriod billingPeriod;

  /// Whether this product can currently be offered. False for everything
  /// today: there is no store integration, so offering a product would be a
  /// fake purchase affordance — the same rule that keeps checkout buttons
  /// off the consent and lock screens.
  final bool available;

  const StoreProduct({
    required this.id,
    required this.plan,
    required this.billingPeriod,
    this.available = false,
  });
}

/// The catalog: every product the app knows, in one reviewable list.
class StoreCatalog {
  static const products = [
    StoreProduct(
      id: 'cyclecoach_pro_monthly',
      plan: SubscriptionPlan.pro,
      billingPeriod: BillingPeriod.monthly,
    ),
    StoreProduct(
      id: 'cyclecoach_pro_yearly',
      plan: SubscriptionPlan.pro,
      billingPeriod: BillingPeriod.yearly,
    ),
  ];

  /// The single normalization point for "store product → internal plan".
  /// Unknown ids return null so callers must handle "not ours" explicitly
  /// rather than defaulting to a plan.
  static StoreProduct? byId(String id) {
    for (final product in products) {
      if (product.id == id) return product;
    }
    return null;
  }

  /// Products currently offerable for [plan]. Empty today, by design.
  static List<StoreProduct> availableFor(SubscriptionPlan plan) => [
    for (final product in products)
      if (product.plan == plan && product.available) product,
  ];
}
