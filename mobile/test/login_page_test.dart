import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/storage/token_storage.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/auth/presentation/login_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// The mockup's screen: glass card, white fields, blue pill button, text
/// links. No AppBar — the card is the whole screen.
MockClient backend() {
  return MockClient((req) async {
    if (req.url.path.endsWith('/auth/login')) {
      return http.Response(
        '{"error":{"code":"X","message":"m",'
        '"details":{"code":"INVALID_CREDENTIALS","message":"bad"}}}',
        401,
      );
    }
    return http.Response('not found', 404);
  });
}

ProviderContainer container() {
  final tokens = MemoryTokenStorage();
  return ProviderContainer(
    overrides: [
      tokenStorageProvider.overrideWithValue(tokens),
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: backend(),
          accessToken: tokens.readAccess,
        ),
      ),
    ],
  );
}

Widget harness(ProviderContainer c, {Locale locale = const Locale('en')}) {
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
      home: const LoginPage(),
    ),
  );
}

void main() {
  testWidgets('renders the mockup: card fields, pill button, links, no bar', (
    t,
  ) async {
    final c = container();
    addTearDown(c.dispose);
    await t.pumpWidget(harness(c));
    await t.pumpAndSettle();

    expect(find.byType(AppBar), findsNothing);
    expect(find.byKey(const Key('login.email')), findsOneWidget);
    expect(find.byKey(const Key('login.password')), findsOneWidget);
    expect(find.byKey(const Key('login.submit')), findsOneWidget);
    expect(find.byKey(const Key('login.forgot')), findsOneWidget);
    expect(find.text('Email'), findsOneWidget);
    expect(find.text('Password'), findsOneWidget);
    expect(find.text('Log in'), findsOneWidget);
    expect(find.text('Forgot password?'), findsOneWidget);
    expect(
      find.textContaining("Don't have an account?", findRichText: true),
      findsOneWidget,
    );
    expect(find.textContaining('Sign Up', findRichText: true), findsOneWidget);
  });

  testWidgets('empty submit shows validation, sends nothing', (t) async {
    final c = container();
    addTearDown(c.dispose);
    await t.pumpWidget(harness(c));
    await t.pumpAndSettle();

    await t.tap(find.byKey(const Key('login.submit')));
    await t.pumpAndSettle();

    expect(find.text('Enter a valid email.'), findsOneWidget);
    expect(find.text('Required'), findsOneWidget);
    // The form refused to submit: no error state, no backend error shown.
    expect(find.byKey(const Key('login.error')), findsNothing);
    expect(c.read(authProvider).status, isNot(AuthStatus.error));
  });

  testWidgets('bad credentials show the backend error on the card', (t) async {
    final c = container();
    addTearDown(c.dispose);
    await t.pumpWidget(harness(c));
    await t.pumpAndSettle();

    await t.enterText(find.byKey(const Key('login.email')), 'r@e.com');
    await t.enterText(
      find.descendant(
        of: find.byKey(const Key('login.password')),
        matching: find.byType(EditableText),
      ),
      'WrongPass999',
    );
    await t.tap(find.byKey(const Key('login.submit')));
    await t.pumpAndSettle();

    expect(find.byKey(const Key('login.error')), findsOneWidget);
    expect(
      find.text('Something went wrong. (INVALID_CREDENTIALS)'),
      findsOneWidget,
    );
  });

  testWidgets('arabic renders RTL with translated card', (t) async {
    final c = container();
    addTearDown(c.dispose);
    await t.pumpWidget(harness(c, locale: const Locale('ar')));
    await t.pumpAndSettle();

    expect(find.text('تسجيل الدخول'), findsOneWidget);
    final ctx = t.element(find.byKey(const Key('login.submit')));
    expect(Directionality.of(ctx), TextDirection.rtl);
  });
}
