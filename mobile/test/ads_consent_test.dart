import 'dart:convert';
import 'dart:io';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/ads/data/ad_consent_store.dart';
import 'package:cyclecoach/features/ads/domain/ad_policy.dart';
import 'package:cyclecoach/features/ads/presentation/ad_policy_providers.dart';
import 'package:cyclecoach/features/ads/presentation/consent_page.dart';
import 'package:cyclecoach/features/auth/domain/auth_user.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

final _userA = AuthUser(
  id: 'user-a',
  email: 'a@example.com',
  displayName: 'A',
  emailVerified: false,
);

final _userB = AuthUser(
  id: 'user-b',
  email: 'b@example.com',
  displayName: 'B',
  emailVerified: false,
);

class _FixedAuth extends AuthNotifier {
  final AuthState fixed;
  _FixedAuth(this.fixed);
  @override
  AuthState build() => fixed;
}

ProviderContainer containerWith({AuthState? auth, AdConsentStore? store}) {
  auth ??= AuthState(AuthStatus.authenticated, user: _userA);
  final resolved = auth;
  return ProviderContainer(
    overrides: [
      authProvider.overrideWith(() => _FixedAuth(resolved)),
      adConsentStoreProvider.overrideWithValue(store ?? MemoryAdConsentStore()),
    ],
  );
}

Widget localized(Widget child, {Locale locale = const Locale('en')}) =>
    MaterialApp(
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Scaffold(body: child),
    );

void main() {
  group('consent storage', () {
    test('round-trips every state per account', () async {
      final store = MemoryAdConsentStore();
      for (final state in AdConsentState.values) {
        await store.save('u1', state);
        expect(await store.load('u1'), state);
      }
    });

    test('unknown accounts read as absent, never as a choice', () async {
      final store = MemoryAdConsentStore();
      expect(await store.load('nobody'), isNull);
    });

    test('accounts are isolated by key', () async {
      final store = MemoryAdConsentStore();
      await store.save('user-a', AdConsentState.granted);
      await store.save('user-b', AdConsentState.denied);
      expect(await store.load('user-a'), AdConsentState.granted);
      expect(await store.load('user-b'), AdConsentState.denied);
    });

    test('corrupt rows decode as absent, not as invented consent', () {
      expect(decodeStored(null), isNull);
      expect(decodeStored('granted'), AdConsentState.granted);
      expect(decodeStored('total-legit-consent'), isNull);
      expect(decodeStored(''), isNull);
    });

    test('wire values are stable machine strings', () {
      expect(AdConsentState.granted.wire, 'granted');
      expect(AdConsentState.denied.wire, 'denied');
      expect(AdConsentState.unknown.wire, 'unknown');
      expect(AdConsentState.notRequired.wire, 'not_required');
      expect(AdConsentState.required.wire, 'required');
    });
  });

  group('consent persistence across sessions', () {
    /// Real login/logout through the auth notifier: override-swapping auth
    /// mid-test does not rebuild dependents the way a genuine session
    /// transition does, so session tests drive the genuine path.
    ProviderContainer sessionContainer(MemoryAdConsentStore store) {
      final tokens = MemoryTokenStorage();
      final backend = MockClient((req) async {
        final path = req.url.path;
        if (path.endsWith('/auth/login')) {
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          final email = '${body['email']}';
          return http.Response(
            '{"access_token":"a-$email","refresh_token":"r-$email"}',
            200,
          );
        }
        if (path.endsWith('/auth/me')) {
          final token = '${req.headers['authorization']}';
          final email = token.startsWith('Bearer a-')
              ? token.substring('Bearer a-'.length)
              : 'unknown@example.com';
          final id = email.startsWith('a@') ? 'user-a' : 'user-b';
          return http.Response(
            jsonEncode({
              'user': {
                'id': id,
                'email': email,
                'status': 'active',
                'email_verified': false,
                'created_at': '2026-01-01T00:00:00Z',
                'last_login_at': null,
              },
              'profile': {
                'display_name': id == 'user-a' ? 'A' : 'B',
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
            jsonEncode({
              'plan': 'free',
              'free_capabilities': const ['core_ride_recording'],
              'entitlements': const [],
              'evaluated_at': '2026-10-06T06:00:00Z',
            }),
            200,
          );
        }
        return http.Response('not found', 404);
      });
      return ProviderContainer(
        overrides: [
          tokenStorageProvider.overrideWithValue(tokens),
          apiClientProvider.overrideWithValue(
            ApiClient(
              baseUrl: 'http://test',
              client: backend,
              accessToken: tokens.readAccess,
            ),
          ),
          adConsentStoreProvider.overrideWithValue(store),
        ],
      );
    }

    test('a choice survives logout and login of the same account', () async {
      final store = MemoryAdConsentStore();
      final c = sessionContainer(store);
      addTearDown(c.dispose);

      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      await c
          .read(adConsentProvider.notifier)
          .recordChoice(AdConsentState.granted);
      expect(c.read(adConsentProvider), AdConsentState.granted);

      // Ended session: memory clears to unknown...
      await c.read(authProvider.notifier).logout();
      expect(c.read(adConsentProvider), AdConsentState.unknown);

      // ...and the returning account restores its own stored choice.
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');
      await c.read(adConsentProvider.notifier).restore();
      expect(c.read(adConsentProvider), AdConsentState.granted);
    });

    test('user B never inherits user A choice', () async {
      final store = MemoryAdConsentStore();
      await store.save('user-a', AdConsentState.granted);
      final c = containerWith(
        auth: AuthState(AuthStatus.authenticated, user: _userB),
        store: store,
      );
      addTearDown(c.dispose);
      await c.read(adConsentProvider.notifier).restore();
      expect(c.read(adConsentProvider), AdConsentState.unknown);
    });

    test('a mid-restore account switch applies nothing stale', () async {
      final store = MemoryAdConsentStore();
      await store.save('user-a', AdConsentState.granted);
      final c = sessionContainer(store);
      addTearDown(c.dispose);
      await c
          .read(authProvider.notifier)
          .login('a@example.com', 'StrongPass123');

      // Start a restore for A, then switch to B before it lands. The
      // user-id guard in restore() must drop A's answer either way the
      // microtasks interleave.
      final pending = c.read(adConsentProvider.notifier).restore('user-a');
      await c.read(authProvider.notifier).logout();
      await c
          .read(authProvider.notifier)
          .login('b@example.com', 'StrongPass123');
      await pending;
      expect(c.read(adConsentProvider), AdConsentState.unknown);
    });

    test('recording without a session stores nothing', () async {
      final store = MemoryAdConsentStore();
      final c = containerWith(
        auth: const AuthState(AuthStatus.unauthenticated),
        store: store,
      );
      addTearDown(c.dispose);
      await c
          .read(adConsentProvider.notifier)
          .recordChoice(AdConsentState.granted);
      expect(c.read(adConsentProvider), AdConsentState.unknown);
      expect(store.values, isEmpty);
    });
  });

  group('consent screen', () {
    ProviderContainer pageContainer() {
      final c = containerWith();
      addTearDown(c.dispose);
      return c;
    }

    Future<void> pumpPage(
      WidgetTester t,
      ProviderContainer c, {
      Locale locale = const Locale('en'),
    }) async {
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: localized(const ConsentPage(), locale: locale),
        ),
      );
      await t.pumpAndSettle();
    }

    testWidgets('grant records granted and shows it', (t) async {
      final c = pageContainer();
      await pumpPage(t, c);
      await t.tap(find.byKey(const Key('consent.allow')));
      await t.pumpAndSettle();
      expect(c.read(adConsentProvider), AdConsentState.granted);
      expect(
        find.text('Ads are allowed. You can change this anytime.'),
        findsOneWidget,
      );
    });

    testWidgets('deny records denied and shows it', (t) async {
      final c = pageContainer();
      await pumpPage(t, c);
      await t.tap(find.byKey(const Key('consent.deny')));
      await t.pumpAndSettle();
      expect(c.read(adConsentProvider), AdConsentState.denied);
      expect(
        find.text('Ads are not allowed. You can change this anytime.'),
        findsOneWidget,
      );
    });

    testWidgets('decide-later records nothing', (t) async {
      final c = pageContainer();
      // The page is pushed over a dummy home, the way profile links to it:
      // "later" must pop back, recording nothing.
      final router = GoRouter(
        initialLocation: '/',
        routes: [
          GoRoute(
            path: '/',
            builder: (_, _) => const Scaffold(body: Text('dummy home')),
          ),
          GoRoute(path: '/consent', builder: (_, _) => const ConsentPage()),
        ],
      );
      addTearDown(router.dispose);
      await t.pumpWidget(
        UncontrolledProviderScope(
          container: c,
          child: MaterialApp.router(
            locale: const Locale('en'),
            supportedLocales: AppLocalizations.supported,
            localizationsDelegates: const [
              AppLocalizationsDelegate(),
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
            routerConfig: router,
          ),
        ),
      );
      await t.pumpAndSettle();
      router.push('/consent');
      await t.pumpAndSettle();
      expect(find.byKey(const Key('consent.page')), findsOneWidget);
      await t.tap(find.byKey(const Key('consent.later')));
      await t.pumpAndSettle();
      expect(c.read(adConsentProvider), AdConsentState.unknown);
      expect(find.text('dummy home'), findsOneWidget);
    });

    testWidgets('allow and deny carry equal visual weight', (t) async {
      final c = pageContainer();
      await pumpPage(t, c);
      final allow = t.widget<FilledButton>(
        find.byKey(const Key('consent.allow')),
      );
      final deny = t.widget<FilledButton>(
        find.byKey(const Key('consent.deny')),
      );
      // Same widget type, same style source: neither action is promoted.
      expect(allow.runtimeType, deny.runtimeType);
      expect(allow.style, deny.style);
    });

    testWidgets('french copy renders', (t) async {
      final c = pageContainer();
      await pumpPage(t, c, locale: const Locale('fr'));
      expect(find.text('Choix publicitaires'), findsWidgets);
      expect(find.text('Autoriser les publicités'), findsOneWidget);
      expect(find.text('Ne pas autoriser'), findsOneWidget);
      expect(find.text('Décider plus tard'), findsOneWidget);
    });

    testWidgets('arabic renders RTL with translated copy', (t) async {
      final c = pageContainer();
      await pumpPage(t, c, locale: const Locale('ar'));
      expect(find.text('خيارات الإعلانات'), findsWidgets);
      expect(find.text('السماح بالإعلانات'), findsOneWidget);
      final ctx = t.element(find.byKey(const Key('consent.allow')));
      expect(Directionality.of(ctx), TextDirection.rtl);
    });

    testWidgets('the copy makes no legal claim', (t) async {
      final c = pageContainer();
      await pumpPage(t, c);
      final body = t.widget<Text>(find.byKey(const Key('consent.body')));
      final text = body.data ?? '';
      for (final claim in ['GDPR', 'compliant', 'guarantee', 'jurisdiction']) {
        expect(text, isNot(contains(claim)));
      }
    });
  });

  group('mounted slots', () {
    Map<String, String> mountedSlots() {
      final found = <String, List<String>>{};
      for (final entry in {
        'home': 'lib/features/home/home_page.dart',
        'rideSummary': 'lib/features/ride/presentation/ride_summary_page.dart',
      }.entries) {
        final source = File(entry.value).readAsStringSync();
        if (source.contains('AdSlotWidget')) {
          found[entry.key] = [
            for (final slot in AdSlot.allowed)
              if (source.contains('AdSlot.${slot.name}')) slot.wire,
          ];
        }
      }
      return found.map((k, v) => MapEntry(k, v.join(',')));
    }

    test('exactly the approved screens mount exactly the approved slots', () {
      final mounted = mountedSlots();
      expect(mounted.keys, {'home', 'rideSummary'});
      expect(mounted['home'], 'home');
      expect(mounted['rideSummary'], 'ride_summary');
    });

    test('prohibited surfaces mount no slot widget', () {
      const prohibited = [
        // Active recording and ride start. (ride_summary is mounted
        // deliberately post-finish; it is asserted in the test above, not
        // here.)
        'lib/features/ride/presentation/ride_recording_page.dart',
        'lib/features/ride/presentation/ride_start_page.dart',
        // Chat and DMs.
        'lib/features/chat/presentation/chat_inbox_page.dart',
        'lib/features/chat/presentation/conversation_page.dart',
        // Auth and account security.
        'lib/features/auth/presentation/login_page.dart',
        'lib/features/auth/presentation/register_page.dart',
        'lib/features/auth/presentation/forgot_password_page.dart',
        // Consent, profile security, and entitlement screens decide or
        // display authorization — an ad beside them is a category error.
        'lib/features/ads/presentation/consent_page.dart',
        'lib/features/profile/profile_page.dart',
        'lib/features/coach/presentation/coach_page.dart',
      ];
      for (final path in prohibited) {
        final source = File(path).readAsStringSync();
        expect(
          source,
          isNot(contains('AdSlotWidget')),
          reason: '$path must stay ad-free',
        );
      }
    });
  });
}
