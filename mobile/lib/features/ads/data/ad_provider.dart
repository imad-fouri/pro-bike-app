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

/// Runtime configuration for a provider adapter.
///
/// Values arrive via `--dart-define`, never from committed source: unit ids
/// are deployment configuration, and any future credential-shaped value
/// belongs in platform secret storage, not here. An absent define means
/// "not configured", which adapters treat as unavailable rather than
/// inventing an identity.
class AdProviderConfig {
  final bool enabled;
  final Map<AdSlot, String> unitIds;

  const AdProviderConfig({required this.enabled, this.unitIds = const {}});

  const AdProviderConfig.disabled() : enabled = false, unitIds = const {};

  /// Reads `ADS_ENABLED` and per-slot `AD_UNIT_<SLOT>` defines
  /// (e.g. `AD_UNIT_HOME`). Empty strings count as absent: an empty unit id
  /// is a misconfiguration, and misconfigurations stay dark.
  ///
  /// Each define is spelled out because `String.fromEnvironment` requires a
  /// compile-time-constant name — a loop cannot generate them. Adding a slot
  /// means adding its line here, which keeps unit-id inventory reviewable.
  factory AdProviderConfig.fromEnvironment() {
    const home = String.fromEnvironment('AD_UNIT_HOME', defaultValue: '');
    const rideSummary = String.fromEnvironment(
      'AD_UNIT_RIDE_SUMMARY',
      defaultValue: '',
    );
    const trainingSummary = String.fromEnvironment(
      'AD_UNIT_TRAINING_SUMMARY',
      defaultValue: '',
    );
    const routeDiscovery = String.fromEnvironment(
      'AD_UNIT_ROUTE_DISCOVERY',
      defaultValue: '',
    );
    const socialFeed = String.fromEnvironment(
      'AD_UNIT_SOCIAL_FEED',
      defaultValue: '',
    );
    return AdProviderConfig(
      enabled: const bool.fromEnvironment('ADS_ENABLED'),
      unitIds: {
        if (home.isNotEmpty) AdSlot.home: home,
        if (rideSummary.isNotEmpty) AdSlot.rideSummary: rideSummary,
        if (trainingSummary.isNotEmpty) AdSlot.trainingSummary: trainingSummary,
        if (routeDiscovery.isNotEmpty) AdSlot.routeDiscovery: routeDiscovery,
        if (socialFeed.isNotEmpty) AdSlot.socialFeed: socialFeed,
      },
    );
  }

  String? unitIdFor(AdSlot slot) => unitIds[slot];
}

/// The adapter seam every real provider will extend.
///
/// `AdProvider` is the contract the app speaks; `AdProviderAdapter` is the
/// base a concrete SDK adapter builds on. It contributes the shared,
/// provider-independent machinery — config gating, unit-id lookup, and safe
/// lifecycle defaults — so a future adapter contains only SDK calls and no
/// policy, storage, or entitlement logic. Authorization-adjacent code can
/// never creep into an adapter because there is nothing here to attach it
/// to: no session, no entitlements, no user data.
abstract class AdProviderAdapter implements AdProvider {
  final AdProviderConfig config;

  AdProviderAdapter([this.config = const AdProviderConfig.disabled()]);

  /// A disabled or unconfigured adapter is unavailable, full stop. Individual
  /// adapters may narrow this further (SDK not initialized, no fill); they
  /// may never widen it.
  @override
  bool get isAvailable => config.enabled;

  @override
  Future<void> initialize() async {}

  @override
  Future<void> dispose() async {}
}

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
class NoOpAdProvider extends AdProviderAdapter {
  bool _disposed = false;

  NoOpAdProvider() : super(const AdProviderConfig.disabled());

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
