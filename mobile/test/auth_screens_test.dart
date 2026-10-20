import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_design.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/auth/presentation/forgot_password_page.dart';
import 'package:cyclecoach/features/auth/presentation/login_background.dart';
import 'package:cyclecoach/features/auth/presentation/login_page.dart';
import 'package:cyclecoach/features/auth/presentation/register_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Glass auth redesign: shared card/fields/buttons across sign in, sign up,
/// and recovery; friendly errors (never raw codes); en/fr/ar parity.
void main() {
  group('friendlyAuthErrorKey', () {
    test('maps known codes to friendly keys', () {
      expect(
        friendlyAuthErrorKey('INVALID_CREDENTIALS'),
        'auth.badCredentials',
      );
      expect(friendlyAuthErrorKey('NETWORK_ERROR'), 'auth.networkError');
      expect(friendlyAuthErrorKey('NETWORK_TIMEOUT'), 'auth.networkError');
      expect(friendlyAuthErrorKey('SESSION_ENDED'), 'auth.sessionEnded');
      expect(friendlyAuthErrorKey('UNAUTHORIZED'), 'auth.sessionEnded');
    });

    test('falls back to the generic key for anything else', () {
      expect(friendlyAuthErrorKey('ERROR'), 'auth.genericError');
      expect(friendlyAuthErrorKey('SOME_FUTURE_CODE'), 'auth.genericError');
      expect(friendlyAuthErrorKey(null), 'auth.genericError');
    });

    test('every mapped key resolves in all three locales', () {
      const keys = [
        'auth.badCredentials',
        'auth.networkError',
        'auth.sessionEnded',
        'auth.genericError',
        'auth.createAccountTitle',
        'auth.createAccountSubtitle',
        'auth.haveAccountPrefix',
        'auth.resetTitle',
        'auth.resetSubtitle',
        'auth.sendResetLink',
        'auth.backToSignIn',
        'auth.showPassword',
        'auth.hidePassword',
      ];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = AppLocalizations(Locale(lang));
        for (final key in keys) {
          expect(loc.get(key), isNotEmpty, reason: '$key missing in $lang');
          expect(loc.get(key), isNot(key), reason: '$key untranslated');
        }
      }
    });
  });

  group('register screen', () {
    testWidgets('renders the glass card with all fields and links', (t) async {
      final c = graphContainer(
        MockClient((_) async => http.Response('x', 404)),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const RegisterPage()));
      await t.pumpAndSettle();

      expect(find.byType(AppBar), findsNothing);
      expect(find.byType(AuthBackground), findsOneWidget);
      expect(find.byType(AuthGlassCard), findsOneWidget);
      expect(find.byKey(const Key('register.name')), findsOneWidget);
      expect(find.byKey(const Key('register.email')), findsOneWidget);
      expect(find.byKey(const Key('register.password')), findsOneWidget);
      expect(find.byKey(const Key('register.confirm')), findsOneWidget);
      expect(find.byKey(const Key('register.submit')), findsOneWidget);
      expect(find.byKey(const Key('register.signin')), findsOneWidget);
      expect(find.text('Create your account'), findsOneWidget);
      expect(
        find.text('Start your next ride with CycleCoach.'),
        findsOneWidget,
      );
    });

    testWidgets('empty submit validates without calling the backend', (
      t,
    ) async {
      var calls = 0;
      final c = graphContainer(
        MockClient((_) async {
          calls++;
          return http.Response('x', 404);
        }),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const RegisterPage()));
      await t.pumpAndSettle();

      await t.ensureVisible(find.byKey(const Key('register.submit')));
      await t.tap(find.byKey(const Key('register.submit')));
      await t.pumpAndSettle();

      expect(find.text('Required'), findsNWidgets(4));
      expect(calls, 0);
      expect(find.byKey(const Key('register.error')), findsNothing);
    });

    testWidgets('arabic renders RTL with translated heading', (t) async {
      final c = graphContainer(
        MockClient((_) async => http.Response('x', 404)),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(
        harness(c, const RegisterPage(), locale: const Locale('ar')),
      );
      await t.pumpAndSettle();

      expect(find.text('أنشئ حسابك'), findsOneWidget);
      final ctx = t.element(find.byKey(const Key('register.submit')));
      expect(Directionality.of(ctx), TextDirection.rtl);
    });
  });

  group('forgot password screen', () {
    testWidgets('renders the recovery card', (t) async {
      final c = graphContainer(
        MockClient((_) async => http.Response('x', 404)),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const ForgotPasswordPage()));
      await t.pumpAndSettle();

      expect(find.byType(AppBar), findsNothing);
      expect(find.byType(AuthGlassCard), findsOneWidget);
      expect(find.text('Reset your password'), findsOneWidget);
      expect(find.byKey(const Key('forgot.email')), findsOneWidget);
      expect(find.byKey(const Key('forgot.submit')), findsOneWidget);
      expect(find.byKey(const Key('forgot.back')), findsOneWidget);
      expect(find.text('Send reset link'), findsOneWidget);
    });

    testWidgets('successful request shows the confirmation', (t) async {
      final c = graphContainer(
        MockClient((req) async => http.Response('{"status":"ok"}', 200)),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const ForgotPasswordPage()));
      await t.pumpAndSettle();

      await t.enterText(find.byKey(const Key('forgot.email')), 'r@e.com');
      await t.tap(find.byKey(const Key('forgot.submit')));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('forgot.success')), findsOneWidget);
      expect(
        find.text('If the email exists, a reset was sent.'),
        findsOneWidget,
      );
    });

    testWidgets('unreachable server shows a friendly error, not success', (
      t,
    ) async {
      final c = graphContainer(
        MockClient((_) async => throw const SocketFailure()),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const ForgotPasswordPage()));
      await t.pumpAndSettle();

      await t.enterText(find.byKey(const Key('forgot.email')), 'r@e.com');
      await t.tap(find.byKey(const Key('forgot.submit')));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('forgot.error')), findsOneWidget);
      expect(
        find.text('No connection. Check your internet and try again.'),
        findsOneWidget,
      );
      expect(find.byKey(const Key('forgot.success')), findsNothing);
    });
  });

  group('login friendly errors', () {
    testWidgets('network failure shows a friendly message, never the code', (
      t,
    ) async {
      final c = graphContainer(
        MockClient((_) async => throw const SocketFailure()),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const LoginPage()));
      await t.pumpAndSettle();

      await t.enterText(find.byKey(const Key('login.email')), 'r@e.com');
      await t.enterText(
        find.descendant(
          of: find.byKey(const Key('login.password')),
          matching: find.byType(EditableText),
        ),
        'StrongPass123',
      );
      await t.tap(find.byKey(const Key('login.submit')));
      await t.pumpAndSettle();

      expect(find.byKey(const Key('login.error')), findsOneWidget);
      expect(
        find.text('No connection. Check your internet and try again.'),
        findsOneWidget,
      );
      expect(find.textContaining('NETWORK_ERROR'), findsNothing);
    });

    testWidgets('password visibility toggle reveals the password', (t) async {
      final c = graphContainer(
        MockClient((_) async => http.Response('x', 404)),
      );
      addTearDown(c.dispose);
      await t.pumpWidget(harness(c, const LoginPage()));
      await t.pumpAndSettle();

      Finder editable() => find.descendant(
        of: find.byKey(const Key('login.password')),
        matching: find.byType(EditableText),
      );
      await t.enterText(editable(), 'StrongPass123');
      await t.pump();
      // Obscuring only changes rendering, so assert the flag itself.
      expect(t.widget<EditableText>(editable()).obscureText, isTrue);

      await t.tap(find.byIcon(Icons.visibility_rounded));
      await t.pump();

      expect(t.widget<EditableText>(editable()).obscureText, isFalse);
    });
  });
}

/// Production provider wiring with a fake transport: token storage and HTTP
/// are faked, everything else is real.
ProviderContainer graphContainer(MockClient backend) {
  final tokens = MemoryTokenStorage();
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      httpClientProvider.overrideWithValue(backend),
    ],
  );
}

Widget harness(
  ProviderContainer c,
  Widget page, {
  Locale locale = const Locale('en'),
}) {
  return UncontrolledProviderScope(
    container: c,
    child: MaterialApp(
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: page,
    ),
  );
}

class SocketFailure implements Exception {
  const SocketFailure();
}
