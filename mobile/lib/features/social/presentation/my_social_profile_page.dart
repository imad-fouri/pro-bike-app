import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// The viewer's own social profile: identity fields plus the privacy posture,
/// with entry points to the edit form and the privacy screen.
///
/// Read-only rendering of the server's profile — there is no local draft here,
/// so this screen can never show a field the backend has not stored. Refresh
/// on pull so an edit made elsewhere is reflected on return.
class MySocialProfilePage extends ConsumerWidget {
  const MySocialProfilePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final profile = ref.watch(mySocialProfileProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('social.myProfile')),
        actions: [
          IconButton(
            key: const Key('social.myProfile.refresh'),
            tooltip: t.get('social.retry'),
            onPressed: () => ref.invalidate(mySocialProfileProvider),
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: profile.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          onRetry: () => ref.invalidate(mySocialProfileProvider),
        ),
        data: (p) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(mySocialProfileProvider),
          child: ListView(
            padding: const EdgeInsets.all(AppSpacing.md),
            children: [
              _IdentityHeader(p),
              const SizedBox(height: AppSpacing.md),
              _PrivacyCard(p),
              const SizedBox(height: AppSpacing.md),
              _LocationNotice(),
              const SizedBox(height: AppSpacing.lg),
              Row(
                children: [
                  Expanded(
                    child: SocialActionButton(
                      buttonKey: const Key('social.editProfile'),
                      label: t.get('social.editProfile'),
                      busyLabel: t.get('social.loading'),
                      busy: false,
                      primary: true,
                      onPressed: () => context.push('/profile/social/edit'),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: SocialActionButton(
                      buttonKey: const Key('social.privacy'),
                      label: t.get('social.privacy'),
                      busyLabel: t.get('social.loading'),
                      busy: false,
                      onPressed: () => context.push('/profile/social/privacy'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.md),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      key: const Key('social.goFriends'),
                      onPressed: () => context.push('/friends'),
                      icon: const Icon(Icons.people_alt_rounded, size: 18),
                      label: Text(t.get('social.friends')),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: OutlinedButton.icon(
                      key: const Key('social.goRequests'),
                      onPressed: () => context.push('/friends/requests'),
                      icon: const Icon(Icons.person_add_alt_rounded, size: 18),
                      label: Text(t.get('social.friendRequests')),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _IdentityHeader extends StatelessWidget {
  final SocialProfile p;
  const _IdentityHeader(this.p);

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SocialAvatar(
                  avatarUrl: p.avatarUrl,
                  name: p.displayName,
                  radius: 32,
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        p.displayName,
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 2),
                      SocialHandle(username: p.username),
                    ],
                  ),
                ),
              ],
            ),
            if (p.bio != null && p.bio!.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              Text(p.bio!),
            ],
            const SizedBox(height: AppSpacing.md),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                if (p.cyclingCategory != null && p.cyclingCategory!.isNotEmpty)
                  _Chip(label: categoryLabel(t, p.cyclingCategory!)),
                _Chip(
                  label: t.get('social.visibility.${p.profileVisibility.wire}'),
                ),
              ],
            ),
            if (p.city != null || p.countryCode != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Row(
                children: [
                  const Icon(
                    Icons.place_outlined,
                    size: 16,
                    color: AppColors.textMuted,
                  ),
                  const SizedBox(width: AppSpacing.xs),
                  Expanded(
                    child: Text(
                      [
                        if (p.city != null && p.city!.isNotEmpty) p.city!,
                        if (p.countryCode != null && p.countryCode!.isNotEmpty)
                          p.countryCode!,
                      ].join(', '),
                      style: const TextStyle(color: AppColors.textMuted),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PrivacyCard extends StatelessWidget {
  final SocialProfile p;
  const _PrivacyCard(this.p);

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              t.get('social.privacy'),
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: AppSpacing.sm),
            _Row(
              t.get('social.visibility'),
              t.get('social.visibility.${p.profileVisibility.wire}'),
            ),
            _Row(
              t.get('social.requestPolicy'),
              t.get('social.requests.${p.allowFriendRequests.wire}'),
            ),
            _Row(
              t.get('social.searchVisibility'),
              t.get('social.search.${p.searchVisibility.wire}'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Standing reminder that friendship is not location access. Phase 8.1 ships
/// no location field at all (ADR-12 §5), so the UI says so plainly instead of
/// letting a rider assume otherwise.
class _LocationNotice extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.location_off_outlined,
            size: 20,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              t.get('social.limitedHint'),
              style: const TextStyle(color: AppColors.textMuted, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  final String label;
  final String value;
  const _Row(this.label, this.value);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: AppColors.textMuted)),
          Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final String label;
  const _Chip({required this.label});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Text(
        label,
        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}
