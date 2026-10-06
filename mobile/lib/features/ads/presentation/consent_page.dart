import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/ad_policy.dart';
import 'ad_policy_providers.dart';

/// The advertising-choices screen: one explainer, three equal actions.
///
/// Anti-dark-pattern rules, all tested:
/// - Allow and Don't allow are the same button style and weight — neither
///   is visually promoted.
/// - Nothing is pre-selected; the current stored choice is shown as text,
///   not as a highlighted default.
/// - "Decide later" leaves without recording anything (state stays unknown,
///   which refuses ads).
/// - The copy states what the choice does and does not do, and makes no
///   legal claim: no "GDPR compliant", no jurisdiction promise.
class ConsentPage extends ConsumerWidget {
  const ConsentPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final consent = ref.watch(adConsentProvider);
    return Scaffold(
      key: const Key('consent.page'),
      appBar: AppBar(title: Text(t.get('consent.title'))),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          const Icon(Icons.ads_click_rounded, size: 40, color: AppColors.gold),
          const SizedBox(height: AppSpacing.md),
          Text(
            t.get('consent.body'),
            key: const Key('consent.body'),
            style: const TextStyle(fontSize: 14, height: 1.5),
          ),
          const SizedBox(height: AppSpacing.lg),
          _StatusRow(consent: consent),
          const SizedBox(height: AppSpacing.lg),
          FilledButton.tonal(
            key: const Key('consent.allow'),
            onPressed: () => ref
                .read(adConsentProvider.notifier)
                .recordChoice(AdConsentState.granted),
            child: Text(t.get('consent.allow')),
          ),
          const SizedBox(height: AppSpacing.sm),
          FilledButton.tonal(
            key: const Key('consent.deny'),
            onPressed: () => ref
                .read(adConsentProvider.notifier)
                .recordChoice(AdConsentState.denied),
            child: Text(t.get('consent.deny')),
          ),
          const SizedBox(height: AppSpacing.sm),
          TextButton(
            key: const Key('consent.later'),
            // Guarded: a deep link straight to this page has nothing
            // beneath it, and a "decide later" that crashes is worse than
            // one that simply stays.
            onPressed: () {
              final router = GoRouter.of(context);
              if (router.canPop()) router.pop();
            },
            child: Text(t.get('consent.later')),
          ),
        ],
      ),
    );
  }
}

/// The stored choice in words. Text, not selection state: showing the choice
/// as a highlighted button would read as a recommendation.
class _StatusRow extends StatelessWidget {
  final AdConsentState consent;
  const _StatusRow({required this.consent});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final key = switch (consent) {
      AdConsentState.granted => 'consent.status.granted',
      AdConsentState.denied => 'consent.status.denied',
      _ => 'consent.status.unknown',
    };
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Row(
        children: [
          Icon(
            consent == AdConsentState.granted
                ? Icons.check_circle_outline_rounded
                : Icons.help_outline_rounded,
            size: 18,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              t.get(key),
              key: const Key('consent.status'),
              style: const TextStyle(fontSize: 13, color: AppColors.textMuted),
            ),
          ),
        ],
      ),
    );
  }
}
