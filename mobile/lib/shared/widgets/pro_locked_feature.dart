import 'package:flutter/material.dart';

import '../../core/l10n/app_localizations.dart';
import '../../core/theme/app_colors.dart';
import '../../features/subscriptions/domain/entitlement.dart';

/// A locked premium feature, rendered only after the server says no.
///
/// This widget is display copy, not access control. It appears when a premium
/// request returns `ENTITLEMENT_REQUIRED`; it never decides whether the
/// request may be made. There is deliberately no purchase button: checkout
/// does not exist in this build, and a button that cannot check out would be
/// a fake purchase affordance.
class ProLockedFeature extends StatelessWidget {
  final EntitlementFeature feature;
  const ProLockedFeature({super.key, required this.feature});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Container(
      key: const Key('subscription.locked'),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.lock_outline_rounded,
                size: 18,
                color: AppColors.gold,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  t.get('subscription.lockedTitle'),
                  key: const Key('subscription.locked.title'),
                  style: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w700,
                    color: AppColors.textOnDark,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            t.getWith('subscription.lockedBody', {
              'feature': t.get(feature.l10nKey),
            }),
            key: const Key('subscription.locked.body'),
            style: const TextStyle(
              fontSize: 13,
              height: 1.4,
              color: AppColors.textMuted,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            t.get('subscription.lockedNoCheckout'),
            key: const Key('subscription.locked.no-checkout'),
            style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }
}
