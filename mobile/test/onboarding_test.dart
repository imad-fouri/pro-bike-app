import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/core/routing/app_router.dart';
import 'package:cyclecoach/features/auth/domain/auth_user.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/auth/presentation/login_page.dart';
import 'package:cyclecoach/features/auth/presentation/onboarding_page.dart';
import 'package:cyclecoach/features/auth/presentation/register_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// The poster's front door: logo over the backdrop, green pill into
/// registration, quiet link into login. Navigation is asserted through the
/// real router, the way the app navigates.
class _FixedAuth extends AuthNotifier {
  final AuthState fixed;
  _FixedAuth(this.fixed);
  @override
  AuthState build() => fixed;
}

Widget shell(AuthState state, {Locale locale = const Locale('en')}) {
  return ProviderScope(
    overrides: [
      authProvider.overrideWith(() => _FixedAuth(state)),
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient((_) async => http.Response('not found', 404)),
          accessToken: () async => 'test-token',
        ),
      ),
    ],
    child: Consumer(
      builder: (context, ref, _) {
        return MaterialApp.router(
          locale: locale,
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          routerConfig: ref.watch(routerProvider),
        );
      },
    ),
  );
}

void main() {
  testWidgets('shows the logo, tagline, pill and account link', (t) async {
    await t.pumpWidget(shell(const AuthState(AuthStatus.unauthenticated)));
    await t.pumpAndSettle();

    expect(find.byType(OnboardingPage), findsOneWidget);
    expect(find.text('Your AI Cycling Coach'), findsOneWidget);
    expect(find.byKey(const Key('onboarding.getStarted')), findsOneWidget);
    expect(find.text('Get Started'), findsOneWidget);
    expect(find.byKey(const Key('onboarding.haveAccount')), findsOneWidget);
    expect(find.text('I already have an account'), findsOneWidget);
  });

  testWidgets('get started → register', (t) async {
    await t.pumpWidget(shell(const AuthState(AuthStatus.unauthenticated)));
    await t.pumpAndSettle();

    await t.tap(find.byKey(const Key('onboarding.getStarted')));
    await t.pumpAndSettle();

    expect(find.byType(RegisterPage), findsOneWidget);
    expect(find.byType(OnboardingPage), findsNothing);
  });

  testWidgets('already have an account → the glass login', (t) async {
    await t.pumpWidget(shell(const AuthState(AuthStatus.unauthenticated)));
    await t.pumpAndSettle();

    await t.tap(find.byKey(const Key('onboarding.haveAccount')));
    await t.pumpAndSettle();

    expect(find.byType(LoginPage), findsOneWidget);
    expect(find.byKey(const Key('login.submit')), findsOneWidget);
  });

  testWidgets('authenticated riders skip it straight to home', (t) async {
    const authed = AuthState(
      AuthStatus.authenticated,
      user: AuthUser(
        id: 'u',
        email: 'r@e.com',
        displayName: 'R',
        emailVerified: false,
      ),
    );
    // The real router bounces an authenticated splash straight to /home.
    await t.pumpWidget(shell(authed));
    await t.pumpAndSettle();

    expect(find.byType(OnboardingPage), findsNothing);
    expect(find.text('R'), findsOneWidget);
  });
}
