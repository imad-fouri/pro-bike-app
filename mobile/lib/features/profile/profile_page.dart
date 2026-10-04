import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../auth/presentation/auth_state.dart';
import '../auth/presentation/login_page.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';

/// Phase 2 profile: renders /me data (display name, email, verification).
/// Editing arrives with onboarding polish later; PATCH already supported by API.
class ProfilePage extends ConsumerWidget {
  const ProfilePage({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final user = ref.watch(authProvider).user;
    return AuthScaffold(
      titleKey: 'profile.title',
      children: [
        if (user != null) ...[
          Row(
            children: [
              CircleAvatar(
                radius: 32,
                backgroundColor: AppColors.primary,
                child: Text(
                  user.displayName.characters.first.toUpperCase(),
                  style: const TextStyle(
                    fontSize: 26,
                    fontWeight: FontWeight.w800,
                    color: AppColors.background,
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      user.displayName,
                      style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      user.email,
                      style: const TextStyle(color: AppColors.textMuted),
                    ),
                    const SizedBox(height: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: user.emailVerified
                            ? AppColors.primary.withValues(alpha: 0.16)
                            : AppColors.gold.withValues(alpha: 0.16),
                        borderRadius: BorderRadius.circular(AppRadius.pill),
                      ),
                      child: Text(
                        user.emailVerified
                            ? t.get('profile.verified')
                            : t.get('profile.unverified'),
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                          color: user.emailVerified
                              ? AppColors.primary
                              : AppColors.gold,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: const Icon(Icons.directions_bike_rounded),
                  title: Text(t.get('bikes.myBikes')),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () => context.go('/bikes'),
                ),
                const Divider(height: 1, indent: 16, endIndent: 16),
                ListTile(
                  key: const Key('profile.socialProfile'),
                  leading: const Icon(Icons.badge_outlined),
                  title: Text(t.get('social.myProfile')),
                  subtitle: Text(t.get('social.friends')),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () => context.go('/profile/social'),
                ),
                const Divider(height: 1, indent: 16, endIndent: 16),
                ListTile(
                  leading: const Icon(Icons.settings_outlined),
                  title: Text(t.get('profile.settings')),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () => context.go('/settings'),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
          OutlinedButton.icon(
            onPressed: () async {
              await ref.read(authProvider.notifier).logout();
            },
            icon: const Icon(Icons.logout_rounded, size: 18),
            label: Text(t.get('auth.logout')),
          ),
        ],
      ],
    );
  }
}
