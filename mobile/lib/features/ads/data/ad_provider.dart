import 'package:flutter/widgets.dart';

import '../domain/ad_policy.dart';

/// The result of asking a provider for an ad.
///
/// This type deliberately has no field for ad content, no creative payload,
/// no click URL, and no impression id. A result is either "nothing to show"
/// or, for a future real provider, a handle the provider itself renders. The
/// absence of a content field is what makes fake ads structurally impossible:
/// there is nowhere to put one.
///
/// A real provider will define its own richer result type; this base stays
/// content-free so the no-op path can never be mistaken for inventory.
abstract class AdLoadResult {
  const AdLoadResult();

  /// Whether the provider filled the slot. The no-op provider always answers
  /// false. A `true` here still does not mean pixels appeared — rendering is
  /// the provider's job, and the widget treats any provider failure as empty.
  bool get filled => false;
}

/// The empty result. The only result the deferred provider ever produces.
class AdEmpty extends AdLoadResult {
  const AdEmpty();
}

/// Provider-neutral advertising mechanics.
///
/// The provider knows nothing about subscriptions, entitlements, accounts,
/// GPS, or training data. It receives an [AdContext] — slot, locale, app
/// version, coarse category — and nothing else. Eligibility was already
/// decided by [AdPolicyService] before any provider method runs; calling
/// [load] directly, bypassing policy, is a programming error the widget
/// never commits.
///
/// Lifecycle mirrors the existing repository pattern: [initialize] once,
/// [load] per slot display, [render] only after a fill, [dispose] on
/// teardown. All pixels come from [render]: the widget never draws an ad
/// itself, so it cannot draw a fake one.
abstract interface class AdProvider {
  /// Prepare the underlying SDK. Must be safe to call more than once and
  /// must never throw for the deferred provider.
  Future<void> initialize();

  /// Whether this provider can currently fill a slot. The deferred provider
  /// always answers false, which is what keeps every slot dark.
  bool get isAvailable;

  /// Attempt one fill for [context]. Returns empty — never null, never an
  /// exception for the deferred provider — so callers have exactly one shape
  /// to handle.
  Future<AdLoadResult> load(AdContext context);

  /// Release provider resources. Must be safe to call without initialize.
  Future<void> dispose();

  /// The provider's own rendering for a filled slot. Called only after
  /// [load] reported filled, and still guarded by the widget: a throw or a
  /// null here renders nothing. The deferred provider never fills, so this
  /// is unreachable in this build.
  Widget? renderSlot(BuildContext context, AdSlot slot);
}

/// The deferred provider: the honest "not yet".
///
/// - never contacts any external ad service (there is no SDK, no endpoint,
///   no unit id anywhere in this file or its imports);
/// - never generates an impression, a click, or revenue — [AdLoadResult] has
///   no field that could record one;
/// - always reports unavailable and loads empty, so every slot renders
///   nothing while the policy architecture around it is fully exercised.
///
/// Swapping in a real provider means implementing [AdProvider] and overriding
/// [adProviderProvider] — no screen, policy rule, or widget changes.
class NoOpAdProvider implements AdProvider {
  bool _disposed = false;

  @override
  Future<void> initialize() async {
    // Intentionally nothing: there is no SDK to warm up.
  }

  @override
  bool get isAvailable => false;

  @override
  Future<AdLoadResult> load(AdContext context) async {
    // The context is accepted and ignored. Accepting it keeps the call shape
    // identical to a future real provider; ignoring it guarantees no data
    // flows anywhere.
    return const AdEmpty();
  }

  @override
  Future<void> dispose() async {
    _disposed = true;
  }

  /// Test-only visibility: dispose was reached exactly once per lifecycle.
  bool get isDisposed => _disposed;

  @override
  Widget? renderSlot(BuildContext context, AdSlot slot) {
    // Unreachable while isAvailable is false: load never fills, so the
    // widget never calls this. Returning null keeps even a programming
    // error dark rather than inventing pixels.
    return null;
  }
}
