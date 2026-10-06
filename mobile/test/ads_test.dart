import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/ads/data/ad_consent_store.dart';
import 'package:cyclecoach/features/ads/data/ad_provider.dart';
import 'package:cyclecoach/features/ads/domain/ad_policy.dart';
import 'package:cyclecoach/features/ads/presentation/ad_policy_providers.dart';
import 'package:cyclecoach/features/auth/domain/auth_user.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/ride/data/ride_recorder.dart';
import 'package:cyclecoach/features/ride/domain/gps_processor.dart';
import 'package:cyclecoach/features/ride/presentation/ride_providers.dart';
import 'package:cyclecoach/features/subscriptions/domain/entitlement.dart';
import 'package:cyclecoach/features/subscriptions/domain/store_products.dart';
import 'package:cyclecoach/features/subscriptions/presentation/entitlement_providers.dart';
import 'package:cyclecoach/shared/widgets/ad_slot_widget.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _now = '2026-10-06T06:00:00Z';

Map<String, dynamic> _grant(
  String feature, {
  String status = 'active',
  String startsAt = '2026-09-06T06:00:00Z',
  String expiresAt = '2026-11-06T06:00:00Z',
}) => {
  'feature': feature,
  'status': status,
  'source': 'subscription',
  'starts_at': startsAt,
  'expires_at': expiresAt,
  'effective': status == 'active',
};

Map<String, dynamic> _stateJson({
  String plan = 'free',
  List<Map<String, dynamic>> entitlements = const [],
}) => {
  'plan': plan,
  'free_capabilities': const ['core_ride_recording'],
  'entitlements': entitlements,
  'evaluated_at': _now,
};

EntitlementState _state(Map<String, dynamic> json) =>
    EntitlementState.fromJson(json);

final _moment = DateTime.parse(_now);

/// Policy states built with a deterministic fetch instant. `fromJson` stamps
/// real wall-clock time, which would couple every staleness assertion to the
/// machine's clock; here the fetch instant IS the evaluation instant unless
/// a test says otherwise.
EntitlementState _policyState({
  String plan = 'free',
  List<Map<String, dynamic>> grants = const [],
  DateTime? fetchedAt,
}) => EntitlementState(
  plan: SubscriptionPlan.parse(plan),
  freeCapabilities: const ['core_ride_recording'],
  entitlements: [for (final g in grants) UserEntitlement.fromJson(g)],
  fetchedAt: (fetchedAt ?? _moment).toUtc(),
);

/// Fixed authentication. Mirrors the harness in coach_test.dart: no fake
/// auth in prod code, only here as a test double for state behavior.
class _FixedAuth extends AuthNotifier {
  final AuthState fixed;
  _FixedAuth(this.fixed);
  @override
  AuthState build() => fixed;
}

const _user = AuthUser(
  id: 'u1',
  email: 'r@e.com',
  displayName: 'R',
  emailVerified: false,
);

/// Fixed entitlement fetch: pure JSON in, no HTTP, so policy tests never
/// depend on the network or the backend.
class _FixedEntitlements extends EntitlementNotifier {
  final Future<EntitlementState?> Function() load;
  _FixedEntitlements(this.load);
  @override
  Future<EntitlementState?> build() => load();
}

/// A provider test double. Filling, throwing, and counting variants cover
/// every widget path; none of them is or resembles a real ad network.
class _FakeProvider implements AdProvider {
  bool available;
  bool shouldThrow;
  int loads = 0;
  AdContext? lastContext;

  _FakeProvider({this.available = true, this.shouldThrow = false});

  @override
  Future<void> initialize() async {}

  @override
  bool get isAvailable => available;

  @override
  Future<AdLoadResult> load(AdContext context) async {
    if (shouldThrow) throw StateError('provider exploded');
    loads++;
    lastContext = context;
    return const _FakeFill();
  }

  @override
  Widget? renderSlot(BuildContext context, AdSlot slot) =>
      const SizedBox(key: Key('fake.ad'), width: 320, height: 50);

  @override
  Future<void> dispose() async {}
}

class _FakeFill extends AdLoadResult {
  const _FakeFill();
  @override
  bool get filled => true;
}

/// Policy container with fixed auth + entitlements. Consent starts unknown;
/// tests grant it explicitly, which is also what proves the default refuses.
ProviderContainer policyContainer({
  AuthState auth = const AuthState(AuthStatus.authenticated, user: _user),
  Future<EntitlementState?> Function()? entitlements,
  AdProvider? provider,
  Duration? ttl,
  AdConsentStore? consentStore,
}) {
  return ProviderContainer(
    overrides: [
      authProvider.overrideWith(() => _FixedAuth(auth)),
      entitlementProvider.overrideWith(
        () => _FixedEntitlements(
          entitlements ?? () async => _state(_stateJson()),
        ),
      ),
      if (provider != null) adProviderProvider.overrideWithValue(provider),
      if (ttl != null) entitlementCacheTtlProvider.overrideWithValue(ttl),
      adConsentStoreProvider.overrideWithValue(
        consentStore ?? MemoryAdConsentStore(),
      ),
    ],
  );
}

/// Reads a policy decision after letting the entitlement fetch settle.
/// Without the await, the read races the fetch and every NO_ADS test would
/// pass or fail on microtask timing instead of on policy.
Future<AdEligibility> decide(ProviderContainer c, AdSlot slot) async {
  try {
    await c.read(entitlementProvider.future);
  } catch (_) {
    // Failed fetches are a policy input (AsyncError → NO_ADS absent),
    // not a test failure.
  }
  final inputs = c.read(adPolicyServiceProvider);
  return inputs.decisionFor(slot, DateTime.parse(_now));
}

/// Fake backend with per-account entitlement states, driving the REAL auth
/// lifecycle (login/logout) instead of override tricks. The entitlement
/// answer follows the currently authenticated email, so A-to-B switches are
/// genuine session transitions.
class _SessionBackend {
  final Map<String, Map<String, dynamic>> plans;
  String? currentEmail;

  _SessionBackend(this.plans);

  MockClient get client => MockClient((req) async {
    final path = req.url.path;
    if (path.endsWith('/auth/login')) {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      currentEmail = '${body['email']}';
      return http.Response(
        '{"access_token":"a-$currentEmail","refresh_token":"r-$currentEmail"}',
        200,
      );
    }
    if (path.endsWith('/auth/me')) {
      return http.Response(
        jsonEncode({
          'user': {
            'id': '00000000-0000-0000-0000-000000000001',
            'email': currentEmail,
            'status': 'active',
            'email_verified': false,
            'created_at': '2026-01-01T00:00:00Z',
            'last_login_at': null,
          },
          'profile': {
            'display_name': 'R',
            'first_name': null,
            'last_name': null,
            'avatar_ref': null,
            'country': null,
            'city': null,
            'preferred_language': 'en',
            'timezone': 'UTC',
            'measurement_system': 'metric',
            'cycling_experience': null,
            'disciplines': [],
            'training_goal': null,
            'profile_visibility': 'public',
            'activity_visibility': 'friends',
          },
        }),
        200,
      );
    }
    if (path.endsWith('/auth/logout')) {
      return http.Response('{"status":"ok"}', 200);
    }
    if (path.endsWith('/me/entitlements')) {
      return http.Response(
        jsonEncode(plans[currentEmail] ?? _stateJson()),
        200,
      );
    }
    return http.Response('not found', 404);
  });
}

ProviderContainer sessionContainer(
  Map<String, Map<String, dynamic>> plans,
  AdProvider provider,
) {
  final backend = _SessionBackend(plans);
  final tokens = MemoryTokenStorage();
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: backend.client,
          accessToken: tokens.readAccess,
        ),
      ),
      adProviderProvider.overrideWithValue(provider),
      // Session tests exercise transitions, not staleness: a year-long TTL
      // keeps the HTTP-stamped fetch fresh against the fixed test clock.
      // Staleness itself is covered deterministically by the stale test.
      entitlementCacheTtlProvider.overrideWithValue(const Duration(days: 365)),
      adConsentStoreProvider.overrideWithValue(MemoryAdConsentStore()),
    ],
  );
}

Widget localized(Widget child) => MaterialApp(
  locale: const Locale('en'),
  supportedLocales: AppLocalizations.supported,
  localizationsDelegates: const [
    AppLocalizationsDelegate(),
    GlobalMaterialLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
  ],
  home: Scaffold(body: child),
);

const _fakeLive = LiveRide(
  localId: 'local-1',
  bikeId: 'bike-1',
  bikeName: 'Road',
  phase: RecordingPhase.recording,
  metrics: RideMetrics(
    distanceM: 1000,
    gainM: 10,
    lossM: 5,
    movingS: 300,
    elapsedS: 320,
    avgSpeedMS: 3.1,
    maxSpeedMS: 9.4,
  ),
  gpsQuality: 'good',
  syncState: 'idle',
);

void main() {
  group('slot taxonomy', () {
    test('the allowed set is exactly the five approved slots', () {
      expect(AdSlot.allowed, hasLength(5));
      expect(
        AdSlot.allowed.map((s) => s.wire),
        containsAll([
          'home',
          'ride_summary',
          'training_summary',
          'route_discovery',
          'social_feed',
        ]),
      );
      // Adding a slot without updating this test, the prohibited-surface
      // audit, and the slot's screen must fail loudly here first.
      expect(AdSlot.allowed, isNot(contains(AdSlot.unknown)));
    });

    test('unknown wire values degrade to unknown, never to a real slot', () {
      expect(AdSlot.parse('home'), AdSlot.home);
      expect(AdSlot.parse('ride_summary'), AdSlot.rideSummary);
      expect(AdSlot.parse('interstitial'), AdSlot.unknown);
      expect(AdSlot.parse(null), AdSlot.unknown);
    });
  });

  group('consent', () {
    test('only granted and not-required permit ads', () {
      expect(AdConsentState.unknown.permitsAds, isFalse);
      expect(AdConsentState.required.permitsAds, isFalse);
      expect(AdConsentState.denied.permitsAds, isFalse);
      expect(AdConsentState.granted.permitsAds, isTrue);
      expect(AdConsentState.notRequired.permitsAds, isTrue);
    });

    test('the enum makes no legal claim by existing', () {
      // There is deliberately no `isCompliant` getter, no region mapping,
      // and no consent-string builder. If one appears, this test names the
      // place that must justify it.
      expect(AdConsentState.values, hasLength(5));
    });
  });

  group('ad context privacy', () {
    test('the wire form carries exactly the approved keys', () {
      final map = const AdContext(
        slot: AdSlot.home,
        locale: 'fr',
        appVersion: '1.0.0+1',
      ).toSafeMap();
      expect(map.keys, {'slot', 'locale', 'app_version'});
      expect(map['slot'], 'home');
    });

    test('an optional coarse category is the only permitted extra', () {
      final map = const AdContext(
        slot: AdSlot.socialFeed,
        locale: 'ar',
        appVersion: '1.0.0+1',
        contentCategory: 'cycling',
      ).toSafeMap();
      expect(map.keys, {'slot', 'locale', 'app_version', 'content_category'});
    });

    test('there is no field for identity, location, or secrets', () {
      // Structural: AdContext has four constructor parameters, and toSafeMap
      // is the complete serialization. A fifth parameter carrying user data
      // would have to appear here to reach a provider.
      const context = AdContext(
        slot: AdSlot.routeDiscovery,
        locale: 'en',
        appVersion: 'dev',
      );
      final blob = '${context.slot}:${context.locale}:${context.appVersion}';
      for (final forbidden in [
        'user_id',
        'email',
        'latitude',
        'ride_id',
        'route_id',
        'friend',
        'token',
        'subscription',
      ]) {
        expect(blob, isNot(contains(forbidden)));
      }
      expect(context.toSafeMap().keys, hasLength(3));
    });
  });

  group('deferred provider', () {
    test('never fills, never renders, never throws', () async {
      final provider = NoOpAdProvider();
      expect(provider.isAvailable, isFalse);
      await provider.initialize();
      final result = await provider.load(
        const AdContext(slot: AdSlot.home, locale: 'en', appVersion: 'dev'),
      );
      expect(result, isA<AdEmpty>());
      expect(result.filled, isFalse);
      await provider.dispose();
      expect(provider.isDisposed, isTrue);
    });

    test('the empty result cannot carry an impression, click, or revenue', () {
      // There is no field to assert on — that is the assertion. Analytics
      // interfaces do not exist in this workstream by design.
      const empty = AdEmpty();
      expect(empty.filled, isFalse);
      expect(empty, isA<AdLoadResult>());
    });
  });

  group('policy decisions', () {
    ProviderContainer permissive({
      Future<EntitlementState?> Function()? entitlements,
    }) {
      final c = policyContainer(
        entitlements: entitlements,
        provider: _FakeProvider(),
      );
      addTearDown(c.dispose);
      c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      return c;
    }

    test(
      'an unknown slot is prohibited even when everything permits',
      () async {
        final c = permissive();
        final d = await decide(c, AdSlot.unknown);
        expect(d.eligible, isFalse);
        expect(d.reason, AdIneligibilityReason.slotProhibited);
      },
    );

    test('an active ride suppresses every slot', () async {
      final c = permissive();
      c.read(rideSessionProvider.notifier).state = _fakeLive;
      for (final slot in AdSlot.allowed) {
        final d = await decide(c, slot);
        expect(d.eligible, isFalse, reason: '${slot.wire} must be dark');
        expect(d.reason, AdIneligibilityReason.rideActive);
      }
    });

    test('no session means no ads', () async {
      final c = policyContainer(
        auth: const AuthState(AuthStatus.unauthenticated),
      );
      addTearDown(c.dispose);
      final d = await decide(c, AdSlot.home);
      expect(d.eligible, isFalse);
      expect(d.reason, AdIneligibilityReason.noSession);
    });

    test('free + consent + provider means eligible', () async {
      final provider = _FakeProvider();
      final c = policyContainer(provider: provider);
      addTearDown(c.dispose);
      c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      final d = await decide(c, AdSlot.home);
      expect(d.eligible, isTrue);
      expect(d.reason, AdIneligibilityReason.eligible);
    });

    test(
      'effective NO_ADS suppresses even with consent and provider',
      () async {
        final c = permissive(
          entitlements: () async =>
              _policyState(plan: 'pro', grants: [_grant('no_ads')]),
        );
        final d = await decide(c, AdSlot.home);
        expect(d.eligible, isFalse);
        expect(d.reason, AdIneligibilityReason.noAdsEntitlement);
      },
    );

    test('expired, revoked, and future NO_ADS do not suppress', () async {
      final cases = {
        'expired': _grant(
          'no_ads',
          status: 'inactive',
          startsAt: '2026-08-06T06:00:00Z',
          expiresAt: '2026-09-06T06:00:00Z',
        ),
        'revoked': _grant('no_ads', status: 'revoked'),
        'future': _grant(
          'no_ads',
          startsAt: '2026-11-06T06:00:00Z',
          expiresAt: '2026-12-06T06:00:00Z',
        ),
      };
      for (final entry in cases.entries) {
        final c = permissive(
          entitlements: () async =>
              _policyState(plan: 'free', grants: [entry.value]),
        );
        final d = await decide(c, AdSlot.home);
        expect(d.eligible, isTrue, reason: entry.key);
      }
    });

    test('denied and unknown consent refuse', () async {
      for (final consent in [AdConsentState.denied, AdConsentState.unknown]) {
        final c = policyContainer(provider: _FakeProvider());
        addTearDown(c.dispose);
        if (consent == AdConsentState.denied) {
          c.read(adConsentProvider.notifier).recordChoice(consent);
        }
        final d = await decide(c, AdSlot.home);
        expect(d.eligible, isFalse, reason: '$consent');
        expect(d.reason, AdIneligibilityReason.consentNotPermitted);
      }
    });

    test(
      'an unavailable provider refuses after every other rule passes',
      () async {
        final c = policyContainer();
        addTearDown(c.dispose);
        c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
        final d = await decide(c, AdSlot.home);
        expect(d.eligible, isFalse);
        expect(d.reason, AdIneligibilityReason.providerUnavailable);
      },
    );

    test('a stale PRO grant lapses instead of suppressing forever', () async {
      final c = policyContainer(
        entitlements: () async =>
            _policyState(plan: 'pro', grants: [_grant('no_ads')]),
        provider: _FakeProvider(),
        // Always stale, independent of clock granularity.
        ttl: const Duration(microseconds: -1),
      );
      addTearDown(c.dispose);
      c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      final d = await decide(c, AdSlot.home);
      expect(d.eligible, isTrue);
    });

    test('missing or failed entitlement fetch never assumes Pro', () async {
      for (final loader in <Future<EntitlementState?> Function()>[
        () async => null,
        () async => throw StateError('entitlement store unreachable'),
      ]) {
        final c = policyContainer(
          entitlements: loader,
          provider: _FakeProvider(),
        );
        addTearDown(c.dispose);
        c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
        final d = await decide(c, AdSlot.home);
        expect(d.eligible, isTrue);
      }
    });

    test('logout clears consent back to unknown', () async {
      final c = sessionContainer({
        'a@example.com': _stateJson(
          plan: 'pro',
          entitlements: [_grant('no_ads')],
        ),
      }, _FakeProvider());
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      expect(
        (await decide(c, AdSlot.home)).reason,
        AdIneligibilityReason.noAdsEntitlement,
      );

      // A real logout ends the session: consent rebuilds to unknown and the
      // slot goes dark on session, not just on consent.
      await c.read(authProvider.notifier).logout();
      expect(c.read(adConsentProvider), AdConsentState.unknown);
      final d = await decide(c, AdSlot.home);
      expect(d.eligible, isFalse);
      expect(d.reason, AdIneligibilityReason.noSession);
    });

    test('account switch replaces the previous NO_ADS state', () async {
      final c = sessionContainer({
        'a@example.com': _stateJson(
          plan: 'pro',
          entitlements: [_grant('no_ads')],
        ),
        'b@example.com': _stateJson(),
      }, _FakeProvider());
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      expect(
        (await decide(c, AdSlot.home)).reason,
        AdIneligibilityReason.noAdsEntitlement,
      );

      // User B signs in on the same container: the login itself must not
      // inherit A's consent, and B's free fetch must not inherit A's grants.
      await c.read(authProvider.notifier).logout();
      await c
          .read(authProvider.notifier)
          .login('b@example.com', 'StrongPass123');
      expect(c.read(adConsentProvider), AdConsentState.unknown);
      c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      final d = await decide(c, AdSlot.home);
      expect(d.eligible, isTrue);
    });
  });

  group('slot widget', () {
    ProviderContainer widgetContainer({
      AuthState auth = const AuthState(AuthStatus.authenticated, user: _user),
      AdProvider? provider,
      bool grantConsent = true,
    }) {
      final backend = MockClient((req) async {
        if (req.url.path.endsWith('/me/entitlements')) {
          return http.Response(jsonEncode(_stateJson()), 200);
        }
        return http.Response('not found', 404);
      });
      final c = ProviderContainer(
        overrides: [
          authProvider.overrideWith(() => _FixedAuth(auth)),
          apiClientProvider.overrideWithValue(
            ApiClient(
              baseUrl: 'http://test',
              client: backend,
              // Fixed auth bypasses login, so no token is stored: supply
              // one, as coach_test.dart does, so authed fetches reach the
              // mock instead of 401ing in the client.
              accessToken: () async => 'test-token',
            ),
          ),
          if (provider != null) adProviderProvider.overrideWithValue(provider),
          adConsentStoreProvider.overrideWithValue(MemoryAdConsentStore()),
        ],
      );
      if (grantConsent) {
        c.read(adConsentProvider.notifier).recordChoice(AdConsentState.granted);
      }
      return c;
    }

    Widget harness(ProviderContainer c, AdSlot slot) {
      return UncontrolledProviderScope(
        container: c,
        child: localized(AdSlotWidget(slot: slot)),
      );
    }

    testWidgets('without a session the slot stays dark and loads nothing', (
      t,
    ) async {
      final provider = _FakeProvider();
      final c = widgetContainer(
        auth: const AuthState(AuthStatus.unauthenticated),
        provider: provider,
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, AdSlot.home));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('ad.slot.empty')), findsOneWidget);
      expect(find.byKey(const Key('ad.slot.filled')), findsNothing);
      expect(provider.loads, 0);
    });

    testWidgets('an eligible slot loads once and renders provider pixels', (
      t,
    ) async {
      final provider = _FakeProvider();
      final c = widgetContainer(provider: provider);
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, AdSlot.home));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('ad.slot.filled')), findsOneWidget);
      expect(find.byKey(const Key('fake.ad')), findsOneWidget);
      expect(provider.loads, 1);
      expect(provider.lastContext!.slot, AdSlot.home);
      // The context carries nothing identifying: slot, locale, version, and
      // the slot's coarse category — four keys, no more.
      expect(provider.lastContext!.toSafeMap().keys, hasLength(4));
      expect(provider.lastContext!.toSafeMap()['content_category'], 'cycling');
    });

    testWidgets('a throwing provider fails to nothing, never a crash', (
      t,
    ) async {
      final provider = _FakeProvider(shouldThrow: true);
      final c = widgetContainer(provider: provider);
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, AdSlot.home));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('ad.slot.empty')), findsOneWidget);
      expect(find.byKey(const Key('ad.slot.filled')), findsNothing);
      // The exception escaped nowhere: the test harness would fail on an
      // uncaught async error, and the tree is intact.
      expect(t.takeException(), isNull);
    });

    testWidgets('the deferred provider keeps every slot dark', (t) async {
      final c = widgetContainer();
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, AdSlot.home));
      await t.pumpAndSettle();
      // Policy refuses on provider availability before the widget ever loads.
      expect(find.byKey(const Key('ad.slot.empty')), findsOneWidget);
    });

    testWidgets('an unavailable provider is never asked to load', (t) async {
      final provider = _FakeProvider(available: false);
      final c = widgetContainer(provider: provider);
      addTearDown(c.dispose);
      // Policy refuses on availability first, so the widget stays dark and
      // the provider hears nothing — belt and suspenders with the widget's
      // own isAvailable re-check.
      await t.pumpWidget(harness(c, AdSlot.home));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('ad.slot.empty')), findsOneWidget);
      expect(provider.loads, 0);
    });

    testWidgets('an unknown slot never loads, whatever permits', (t) async {
      final provider = _FakeProvider();
      final c = widgetContainer(provider: provider);
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, AdSlot.unknown));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('ad.slot.empty')), findsOneWidget);
      expect(provider.loads, 0);
    });
  });

  group('content taxonomy', () {
    test('the taxonomy is exactly the four approved categories', () {
      expect(AdContentCategory.values.map((c) => c.wire), [
        'cycling',
        'training',
        'routes',
        'equipment',
        'unknown',
      ]);
      expect(AdContentCategory.parse('training'), AdContentCategory.training);
      expect(AdContentCategory.parse('per-ride'), AdContentCategory.unknown);
      expect(AdContentCategory.parse(null), AdContentCategory.unknown);
    });

    test('every allowed slot has a screen-level default category', () {
      expect(AdSlot.home.defaultContentCategory, AdContentCategory.cycling);
      expect(
        AdSlot.rideSummary.defaultContentCategory,
        AdContentCategory.cycling,
      );
      expect(
        AdSlot.trainingSummary.defaultContentCategory,
        AdContentCategory.training,
      );
      expect(
        AdSlot.routeDiscovery.defaultContentCategory,
        AdContentCategory.routes,
      );
      expect(
        AdSlot.socialFeed.defaultContentCategory,
        AdContentCategory.cycling,
      );
      expect(AdSlot.unknown.defaultContentCategory, AdContentCategory.unknown);
    });
  });

  group('provider adapter boundary', () {
    test('a disabled config is unavailable with no units', () {
      const config = AdProviderConfig.disabled();
      expect(config.enabled, isFalse);
      expect(config.unitIds, isEmpty);
      expect(config.unitIdFor(AdSlot.home), isNull);
    });

    test('an enabled config resolves per-slot unit ids', () {
      const config = AdProviderConfig(
        enabled: true,
        unitIds: {AdSlot.home: 'ca-app-pub-test/1'},
      );
      expect(config.enabled, isTrue);
      expect(config.unitIdFor(AdSlot.home), 'ca-app-pub-test/1');
      expect(config.unitIdFor(AdSlot.rideSummary), isNull);
    });

    test('the environment config defaults to disabled with no units', () {
      // No --dart-define flags are passed in tests, so this asserts the
      // fail-closed default: an unconfigured build stays dark.
      final config = AdProviderConfig.fromEnvironment();
      expect(config.enabled, isFalse);
      expect(config.unitIds, isEmpty);
    });

    test('the deferred provider is an unavailable adapter', () {
      final provider = NoOpAdProvider();
      expect(provider, isA<AdProviderAdapter>());
      expect(provider, isA<AdProvider>());
      expect(provider.isAvailable, isFalse);
      expect(provider.config.enabled, isFalse);
    });

    test('an enabled adapter base narrows availability honestly', () {
      final adapter = _EnabledAdapter();
      expect(adapter.isAvailable, isTrue);
    });
  });

  group('store catalog', () {
    test('the catalog holds exactly the two provisional products', () {
      expect(StoreCatalog.products.map((p) => p.id), [
        'cyclecoach_pro_monthly',
        'cyclecoach_pro_yearly',
      ]);
      for (final product in StoreCatalog.products) {
        expect(product.plan, SubscriptionPlan.pro);
        expect(product.available, isFalse);
      }
      expect(StoreCatalog.products.map((p) => p.billingPeriod), [
        BillingPeriod.monthly,
        BillingPeriod.yearly,
      ]);
    });

    test('lookup is the single normalization point', () {
      final monthly = StoreCatalog.byId('cyclecoach_pro_monthly');
      expect(monthly, isNotNull);
      expect(monthly!.plan, SubscriptionPlan.pro);
      expect(StoreCatalog.byId('not_ours'), isNull);
      expect(StoreCatalog.byId(''), isNull);
    });

    test('nothing is offerable while no store is integrated', () {
      expect(StoreCatalog.availableFor(SubscriptionPlan.pro), isEmpty);
      expect(StoreCatalog.availableFor(SubscriptionPlan.free), isEmpty);
    });
  });
}

/// An adapter with configuration switched on, proving the base class
/// narrows (never widens) availability from config. Test-only.
class _EnabledAdapter extends AdProviderAdapter {
  _EnabledAdapter() : super(const AdProviderConfig(enabled: true, unitIds: {}));

  @override
  Future<AdLoadResult> load(AdContext context) async => const AdEmpty();

  @override
  Widget? renderSlot(BuildContext context, AdSlot slot) => null;
}
