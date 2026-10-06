import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../../ride/presentation/ride_providers.dart';
import '../../subscriptions/domain/entitlement.dart';
import '../../subscriptions/presentation/entitlement_providers.dart';
import '../data/ad_consent_store.dart';
import '../data/ad_provider.dart';
import '../domain/ad_policy.dart';

/// Coarse build label for [AdContext]. Sourced from `--dart-define`, never
/// from device or user data. Overridable in tests; defaults to `dev` rather
/// than a fabricated version number.
final adAppVersionProvider = Provider<String>(
  (_) => const String.fromEnvironment('APP_VERSION', defaultValue: 'dev'),
);

/// The one provider implementation this build ships: the deferred one.
///
/// Overriding this provider is the entire future integration: a real adapter
/// implements [AdProvider] and replaces this line. No screen, policy rule, or
/// widget changes, because none of them names a provider.
final adProviderProvider = Provider<AdProvider>((_) => NoOpAdProvider());

/// Where consent choices live. Secure per-account storage in production;
/// overridden with [MemoryAdConsentStore] in tests.
final adConsentStoreProvider = Provider<AdConsentStore>(
  (_) => SecureAdConsentStore(),
);

/// Advertising consent, session-tied with per-account persistence.
///
/// Starts [AdConsentState.unknown] — which refuses ads — restores the
/// account's stored choice when one exists, and returns to unknown the
/// moment authentication ends. Restoration is keyed by account id, so one
/// rider's choice never follows the device into the next session, while a
/// returning rider finds their own choice intact.
///
/// [recordChoice] is the hook the consent screen calls. Persistence is
/// best-effort: the in-memory state is set first and is authoritative for
/// the session, so a storage failure loses the choice on restart (failing
/// closed back to unknown) but never loses it mid-session.
class AdConsentNotifier extends Notifier<AdConsentState> {
  @override
  AdConsentState build() {
    final auth = ref.watch(authProvider);
    final userId = auth.user?.id;
    if (!auth.isAuthenticated || userId == null) {
      return AdConsentState.unknown;
    }
    unawaited(restore(userId));
    return AdConsentState.unknown;
  }

  /// Reload the stored choice for [userId], defaulting to the current
  /// session's rider. Public so tests and refresh flows can await it;
  /// [build] already calls it for every new session.
  Future<void> restore([String? userId]) async {
    userId ??= ref.read(authProvider).user?.id;
    if (userId == null) return;
    AdConsentState? stored;
    try {
      stored = await ref.read(adConsentStoreProvider).load(userId);
    } catch (_) {
      return;
    }
    if (stored == null) return;
    // The rider may have switched while the read was in flight: only apply
    // a choice that still belongs to the current session.
    if (ref.read(authProvider).user?.id != userId) return;
    state = stored;
  }

  Future<void> recordChoice(AdConsentState choice) async {
    final userId = ref.read(authProvider).user?.id;
    if (userId == null) return;
    state = choice;
    try {
      await ref.read(adConsentStoreProvider).save(userId, choice);
    } catch (_) {
      // Best-effort persistence (see class docs): the session keeps the
      // choice; a restart forgets it rather than crashing.
    }
  }
}

final adConsentProvider = NotifierProvider<AdConsentNotifier, AdConsentState>(
  AdConsentNotifier.new,
);

/// The inputs one policy decision reads. A snapshot, not a live query: the
/// service watches every input, so any change rebuilds dependents, and
/// [decisionFor] is a pure function of the snapshot plus an explicit `now`
/// (explicit so tests never depend on the wall clock).
class AdPolicyInputs {
  final bool isAuthenticated;
  final AsyncValue<EntitlementState?> entitlements;
  final Duration entitlementTtl;
  final AdConsentState consent;
  final bool rideActive;
  final bool providerAvailable;

  const AdPolicyInputs({
    required this.isAuthenticated,
    required this.entitlements,
    required this.entitlementTtl,
    required this.consent,
    required this.rideActive,
    required this.providerAvailable,
  });

  /// The centralized rule, in evaluation order. The order is the design:
  ///
  /// 1. Unknown slots are prohibited — there is no allowlist entry to check.
  /// 2. An active ride suppresses everything, before any other question.
  /// 3. Without a session there is no entitlement and no consent to read.
  /// 4. A fresh, effective NO_ADS suppresses. Stale or absent state does
  ///    not — a stale Pro grant must lapse, never persist (see
  ///    `docs/ads-foundation.md` §9).
  /// 5. Consent must affirmatively permit.
  /// 6. The provider must report availability.
  /// 7. Otherwise the slot is eligible — which authorizes an attempt, never
  ///    a display.
  AdEligibility decisionFor(AdSlot slot, DateTime now) {
    final moment = now.toUtc();
    if (!AdSlot.allowed.contains(slot)) {
      return const AdEligibility.refused(AdIneligibilityReason.slotProhibited);
    }
    if (rideActive) {
      return const AdEligibility.refused(AdIneligibilityReason.rideActive);
    }
    if (!isAuthenticated) {
      return const AdEligibility.refused(AdIneligibilityReason.noSession);
    }
    if (_noAdsApplies(moment)) {
      return const AdEligibility.refused(
        AdIneligibilityReason.noAdsEntitlement,
      );
    }
    if (!consent.permitsAds) {
      return const AdEligibility.refused(
        AdIneligibilityReason.consentNotPermitted,
      );
    }
    if (!providerAvailable) {
      return const AdEligibility.refused(
        AdIneligibilityReason.providerUnavailable,
      );
    }
    return const AdEligibility.eligible();
  }

  bool _noAdsApplies(DateTime moment) {
    final state = switch (entitlements) {
      AsyncData(:final value) => value,
      _ => null,
    };
    if (state == null) return false;
    if (moment.difference(state.fetchedAt) > entitlementTtl) return false;
    return state.hasFeature(EntitlementFeature.noAds, moment);
  }
}

/// The centralized policy. Screens never read entitlements, consent, ride
/// state, or the provider directly for advertising questions — they read
/// this, call [AdPolicyInputs.decisionFor], and render the answer.
class AdPolicyService extends Notifier<AdPolicyInputs> {
  @override
  AdPolicyInputs build() {
    return AdPolicyInputs(
      isAuthenticated: ref.watch(authProvider).isAuthenticated,
      entitlements: ref.watch(entitlementProvider),
      entitlementTtl: ref.watch(entitlementCacheTtlProvider),
      consent: ref.watch(adConsentProvider),
      rideActive: ref.watch(rideSessionProvider) != null,
      providerAvailable: ref.watch(adProviderProvider).isAvailable,
    );
  }
}

final adPolicyServiceProvider =
    NotifierProvider<AdPolicyService, AdPolicyInputs>(AdPolicyService.new);
