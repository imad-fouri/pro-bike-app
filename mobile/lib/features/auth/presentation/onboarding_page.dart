import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../../shared/widgets/brand.dart';
import 'login_background.dart';

/// Get Started screen: the app's front door. Logo over the alpine backdrop,
/// a green pill into registration, and a quiet link into login for returning
/// riders.
///
/// This is the unauthenticated landing page (see `app_router.dart`), so it
/// must never assume a session: both exits are public routes.
class OnboardingPage extends StatelessWidget {
  const OnboardingPage({super.key});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Scaffold(
      extendBodyBehindAppBar: true,
      body: Stack(
        children: [
          const LoginBackground(),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 32),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Spacer(flex: 3),
                  const LogoFull(height: 140),
                  const SizedBox(height: AppSpacing.sm),
                  Text(
                    t.get('onboarding.tagline'),
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const Spacer(flex: 4),
                  SizedBox(
                    height: 56,
                    child: FilledButton(
                      key: const Key('onboarding.getStarted'),
                      onPressed: () => context.go('/register'),
                      child: Text(t.get('onboarding.getStarted')),
                    ),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Center(
                    child: TextButton(
                      key: const Key('onboarding.haveAccount'),
                      onPressed: () => context.go('/login'),
                      style: TextButton.styleFrom(
                        foregroundColor: Colors.white.withValues(alpha: 0.85),
                      ),
                      child: Text(t.get('onboarding.haveAccount')),
                    ),
                  ),
                  const SizedBox(height: AppSpacing.lg),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
