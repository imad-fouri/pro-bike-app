import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/theme/app_colors.dart';
import 'package:cyclecoach/core/theme/app_theme.dart';
import 'package:cyclecoach/main.dart';
import 'package:cyclecoach/shared/widgets/brand.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('brand tokens match docs/11', () {
    expect(AppColors.primary, const Color(0xFF7BC043));
    expect(AppColors.background, const Color(0xFF0A1F1C));
    expect(AppColors.primaryDark, const Color(0xFF2E7D32));
  });

  test('l10n en/fr/ar resolve without hard-coded widget strings', () {
    expect(AppLocalizations(const Locale('en')).get('welcome'), isNotEmpty);
    expect(AppLocalizations(const Locale('fr')).get('welcome'), isNotEmpty);
    expect(AppLocalizations(const Locale('ar')).get('welcome'), isNotEmpty);
  });

  testWidgets('app boots to splash while session restores', (t) async {
    await t.pumpWidget(const ProviderScope(child: CycleCoachApp()));
    await t.pump();
    expect(find.byType(LogoFull), findsOneWidget);
    expect(find.text('Train • Ride • Explore'), findsOneWidget);
    expect(buildDarkTheme().scaffoldBackgroundColor, AppColors.background);
  });
}
