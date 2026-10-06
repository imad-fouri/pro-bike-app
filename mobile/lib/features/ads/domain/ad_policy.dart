/// Advertising policy domain: slots, consent, context, decisions.
///
/// Everything here is product vocabulary, never provider vocabulary. There is
/// no ad-network name, no ad-unit id, and no advertising identifier anywhere
/// in this file — a future provider adapter will map these slots onto its own
/// inventory, and nothing here will change.
///
/// The closed [AdSlot] enum is itself a safety control: there is no slot
/// value for an active ride, a chat thread, an auth screen, or a consent
/// screen, so no call site can request an ad there. Prohibition is by
/// construction, not by convention.
library;

/// Surfaces where an ad may be considered. Five values, deliberately few.
///
/// Each slot maps to an existing screen: home → `/home`, rideSummary →
/// `/ride/summary`, trainingSummary → `/training`, routeDiscovery →
/// `/routes`, socialFeed → `/friends`. Adding a slot is a product decision
/// that must update the taxonomy test, the prohibited-surface audit in
/// `docs/ads-foundation.md`, and the slot's screen — never just the enum.
enum AdSlot {
  home('home'),
  rideSummary('ride_summary'),
  trainingSummary('training_summary'),
  routeDiscovery('route_discovery'),
  socialFeed('social_feed'),
  unknown('unknown');

  const AdSlot(this.wire);
  final String wire;

  static AdSlot parse(String? raw) {
    for (final slot in AdSlot.values) {
      if (slot.wire == raw) return slot;
    }
    // Forward compatibility with the same rule as entitlements: an unknown
    // value degrades to explicitly-unknown — and unknown slots are never
    // eligible — rather than breaking the caller.
    return AdSlot.unknown;
  }

  /// Only the five real slots. [unknown] is intentionally excluded: policy
  /// treats it as prohibited.
  static const allowed = [
    AdSlot.home,
    AdSlot.rideSummary,
    AdSlot.trainingSummary,
    AdSlot.routeDiscovery,
    AdSlot.socialFeed,
  ];
}

/// Advertising-consent state machine.
///
/// These states describe the product's knowledge, not a legal finding. The
/// existence of this enum claims no GDPR/ATT compliance; the actual
/// legal/provider consent flows are a later workstream. Today only [unknown],
/// [granted], and [denied] are reachable (test and future-consent-UI hooks);
/// [notRequired] and [required] exist so the future flow has states to drive
/// toward rather than inventing them under schedule pressure.
enum AdConsentState {
  unknown,
  notRequired,
  required,
  granted,
  denied;

  /// Consent permits advertising only when explicitly granted or explicitly
  /// not required. Unknown, required-but-unanswered, and denied all refuse.
  /// Fail-closed is the only safe default for a state that starts unknown.
  bool get permitsAds => this == granted || this == notRequired;
}

/// The only information an ad provider may ever receive about a request.
///
/// Deliberately coarse: slot, locale, app version, and an optional coarse
/// content category. There is deliberately no field for a user id, an email,
/// a location, a ride or route id, a friend id, message content, telemetry,
/// tokens, or subscription state — [toSafeMap] is the complete serialization,
/// so a future provider cannot receive what this model cannot carry.
class AdContext {
  final AdSlot slot;
  final String locale;
  final String appVersion;
  final String? contentCategory;

  const AdContext({
    required this.slot,
    required this.locale,
    required this.appVersion,
    this.contentCategory,
  });

  /// The complete wire form. Any key not present here can never reach a
  /// provider, which is what makes the privacy boundary structural rather
  /// than conventional.
  Map<String, String> toSafeMap() => {
    'slot': slot.wire,
    'locale': locale,
    'app_version': appVersion,
    if (contentCategory != null && contentCategory!.isNotEmpty)
      'content_category': contentCategory!,
  };
}

/// Why a slot decision came out the way it did. Every refusal reason is a
/// distinct value so dashboards, tests, and future debugging can tell "no ads
/// because Pro" from "no ads because the ride is recording" — collapsing
/// them would hide exactly the distinction that matters in an incident.
enum AdIneligibilityReason {
  eligible,
  slotProhibited,
  rideActive,
  noAdsEntitlement,
  consentNotPermitted,
  providerUnavailable,
  noSession,
}

/// One centralized policy answer for one slot at one instant.
///
/// `eligible` never means "an ad must display" — the provider may return no
/// fill. It means every centralized rule passed and a load may be attempted.
class AdEligibility {
  final bool eligible;
  final AdIneligibilityReason reason;

  const AdEligibility._(this.eligible, this.reason);

  const AdEligibility.eligible() : this._(true, AdIneligibilityReason.eligible);

  const AdEligibility.refused(AdIneligibilityReason refusalReason)
    : assert(refusalReason != AdIneligibilityReason.eligible),
      eligible = false,
      reason = refusalReason;
}
