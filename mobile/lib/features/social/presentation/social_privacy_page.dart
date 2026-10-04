import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import '../domain/social_validators.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Privacy controls, one section per backend setting.
///
/// The three settings map 1:1 onto `PATCH /social/profile/privacy` and use the
/// server's own wire values. Nothing is invented: there is no "hide city" or
/// "limit audience" knob the backend does not have, and adding one client-side
/// would be a control that does nothing.
///
/// The page is a draft until Save. Unsaved changes are discarded on leave so
/// the profile never shows a setting the server has not stored.
class SocialPrivacyPage extends ConsumerStatefulWidget {
  const SocialPrivacyPage({super.key});

  @override
  ConsumerState<SocialPrivacyPage> createState() => _SocialPrivacyPageState();
}

class _SocialPrivacyPageState extends ConsumerState<SocialPrivacyPage> {
  ProfileVisibility? _visibility;
  FriendRequestsPolicy? _requests;
  SearchVisibility? _search;
  bool _hydrated = false;
  String? _error;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final profile = ref.watch(mySocialProfileProvider);
    final busy = ref.watch(socialActionsProvider).contains('privacy');

    return Scaffold(
      appBar: AppBar(title: Text(t.get('social.privacy'))),
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
        data: (p) {
          if (!_hydrated) {
            _hydrated = true;
            _visibility = p.profileVisibility;
            _requests = p.allowFriendRequests;
            _search = p.searchVisibility;
          }
          return ListView(
            padding: const EdgeInsets.all(AppSpacing.md),
            children: [
              if (_error != null) ...[
                Container(
                  key: const Key('social.privacy.error'),
                  padding: const EdgeInsets.all(AppSpacing.md),
                  decoration: BoxDecoration(
                    color: Theme.of(
                      context,
                    ).colorScheme.error.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(AppRadius.card),
                  ),
                  child: Text(_error!),
                ),
                const SizedBox(height: AppSpacing.md),
              ],
              _Section(
                title: t.get('social.visibility'),
                options: [
                  for (final v in SocialValidators.visibility)
                    (v.labelKey, v.wire, v),
                ],
                selected: _visibility,
                enabled: !busy,
                testKeyPrefix: 'social.visibility',
                onChanged: (v) =>
                    setState(() => _visibility = v as ProfileVisibility),
              ),
              _Section(
                title: t.get('social.requestPolicy'),
                options: [
                  for (final v in SocialValidators.requestPolicies)
                    (v.labelKey, v.wire, v),
                ],
                selected: _requests,
                enabled: !busy,
                testKeyPrefix: 'social.requests',
                onChanged: (v) =>
                    setState(() => _requests = v as FriendRequestsPolicy),
              ),
              _Section(
                title: t.get('social.searchVisibility'),
                options: [
                  for (final v in SocialValidators.searchVisibilities)
                    (v.labelKey, v.wire, v),
                ],
                selected: _search,
                enabled: !busy,
                testKeyPrefix: 'social.searchVisibility',
                onChanged: (v) =>
                    setState(() => _search = v as SearchVisibility),
              ),
              const SizedBox(height: AppSpacing.sm),
              Container(
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
                        style: const TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 13,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              SocialActionButton(
                buttonKey: const Key('social.privacy.save'),
                label: t.get('social.privacy'),
                busyLabel: t.get('social.loading'),
                busy: busy,
                primary: true,
                onPressed: busy ? null : _save,
              ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _save() async {
    final t = context.l10n;
    setState(() => _error = null);
    try {
      await ref
          .read(socialActionsProvider.notifier)
          .savePrivacy(
            profileVisibility: _visibility,
            allowFriendRequests: _requests,
            searchVisibility: _search,
          );
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(t.get('social.privacySaved'))));
      context.pop();
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyError(t, e));
    }
  }
}

/// Radio group for one enum. Options are generated from the backend's wire
/// values, so adding a value server-side without a translation shows the raw
/// value instead of silently mislabeling it.
class _Section extends StatelessWidget {
  final String title;

  /// One entry per backend value: (l10n key, wire value, enum instance).
  /// The wire value is what the test keys and the PATCH body use, so a key
  /// never has to be derived from an enum's toString.
  final List<(String, String, Object)> options;
  final Object? selected;
  final bool enabled;
  final String testKeyPrefix;
  final ValueChanged<Object> onChanged;

  const _Section({
    required this.title,
    required this.options,
    required this.selected,
    required this.enabled,
    required this.testKeyPrefix,
    required this.onChanged,
  });

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
              title,
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: AppSpacing.sm),
            // RadioGroup owns the selection so one group renders all three options; the
            // value type is Object, so the cast is localized to this one widget.
            RadioGroup<Object>(
              groupValue: selected,
              onChanged: enabled
                  ? (Object? v) {
                      if (v != null) {
                        onChanged(v);
                      }
                    }
                  : (Object? _) {},
              child: Column(
                children: [
                  for (final (labelKey, wire, value) in options)
                    RadioListTile<Object>(
                      key: Key('$testKeyPrefix.$wire'),
                      value: value,
                      contentPadding: EdgeInsets.zero,
                      title: Text(_safeLabel(t, labelKey, wire)),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// An unmapped key falls back to the server's wire value; it never throws
  /// and never renders an empty option.
  String _safeLabel(AppLocalizations t, String key, String wire) {
    try {
      return t.get(key);
    } on Object {
      return wire;
    }
  }
}
