import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/routing/app_router.dart';
import 'package:cyclecoach/features/auth/domain/auth_user.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

const _user = AuthUser(
  id: 'u',
  email: 'r@e.com',
  displayName: 'R',
  emailVerified: false,
);

class _FixedAuth extends AuthNotifier {
  final AuthState fixed;
  _FixedAuth(this.fixed);
  @override
  AuthState build() => fixed;
}

Widget shell(AuthState state) {
  return ProviderScope(
    overrides: [authProvider.overrideWith(() => _FixedAuth(state))],
    child: Consumer(
      builder: (context, ref, _) {
        return MaterialApp.router(
          locale: const Locale('ar'),
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
  testWidgets('unauthenticated deep link → onboarding (guard)', (t) async {
    await t.pumpWidget(shell(const AuthState(AuthStatus.unauthenticated)));
    await t.pumpAndSettle();
    // The Get Started screen is the unauthenticated front door; login lives
    // one tap behind it.
    expect(find.byKey(const Key('onboarding.getStarted')), findsOneWidget);
    expect(find.text('ابدأ'), findsOneWidget);
    expect(find.byKey(const Key('onboarding.haveAccount')), findsOneWidget);
  });

  testWidgets('authenticated on splash → home', (t) async {
    await t.pumpWidget(shell(AuthState(AuthStatus.authenticated, user: _user)));
    await t.pumpAndSettle();
    expect(find.text('R'), findsOneWidget);
  });

  testWidgets('arabic locale renders RTL direction', (t) async {
    await t.pumpWidget(shell(const AuthState(AuthStatus.unauthenticated)));
    await t.pumpAndSettle();
    final ctx = t.element(find.text('ابدأ').first);
    expect(Directionality.of(ctx), TextDirection.rtl);
  });
}
