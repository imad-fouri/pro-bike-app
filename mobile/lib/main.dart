import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/config/app_config.dart';
import 'core/l10n/app_localizations.dart';
import 'core/routing/app_router.dart';
import 'core/theme/app_theme.dart';
import 'features/notifications/presentation/notification_providers.dart';

void main() {
  // Fail closed before the first frame. A release build that was not given a
  // production API_BASE would otherwise start up pointing at
  // http://localhost:8000 and carry every token and GPS coordinate there in
  // cleartext, while looking and behaving like a working app.
  AppConfig.current.validate();
  runApp(const ProviderScope(child: CycleCoachApp()));
}

class CycleCoachApp extends ConsumerWidget {
  const CycleCoachApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Watching here (rather than inside the router) is what replays a
    // notification tap that arrived before authentication was restored. See
    // `pendingLinkReplayProvider`.
    ref.watch(pendingLinkReplayProvider);
    return MaterialApp.router(
      title: 'CycleCoach',
      theme: buildLightTheme(),
      darkTheme: buildDarkTheme(),
      themeMode: ThemeMode.dark,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      routerConfig: ref.watch(routerProvider),
    );
  }
}
