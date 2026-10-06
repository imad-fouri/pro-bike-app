/// The purchase lifecycle as the app understands it.
///
/// These states describe what happened with a store interaction, never what
/// the rider is entitled to. There is deliberately no `pro` or `premium`
/// value here and no field that could hold one: plan state lives only in
/// `EntitlementState`, fetched from the server after verification. A state
/// machine that cannot name "pro" cannot grant it.
library;

enum PurchaseOutcome {
  /// Nothing attempted yet, or the session ended (logout rebuilds here).
  idle,

  /// A store or server round-trip is in flight.
  pending,

  /// The store reported the rider cancelled. Terminal for this attempt;
  /// reachable only through a real store SDK, which does not exist yet.
  cancelled,

  /// The attempt failed (network, server refusal, malformed answer).
  /// Entitlements are untouched — failure never implies a plan either way.
  failed,

  /// No purchase path exists (no store SDK, provider unavailable server-
  /// side). Distinct from `failed`: retrying is pointless until the
  /// integration lands, and the UI must say so rather than spinning.
  unavailable,

  /// The server processed the purchase. This means reconciliation ran —
  /// not that the rider is Pro. The plan is read from the refreshed
  /// `EntitlementState`, never from this value.
  verified,
}
