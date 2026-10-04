import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import '../domain/social_validators.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Edit the fields the backend exposes on PATCH /social/profile.
///
/// Three rules hold here:
/// - Only identity fields are editable. `user_id`, relationship state, and
///   every other server-owned column are absent from this form by
///   construction, so there is no code path that could submit them.
/// - Client validation is UX only ([SocialValidators]). The server stays
///   authoritative: its 409/422 is rendered verbatim through
///   [friendlyError] and the form stays open.
/// - Username clearing is explicit. A blank username means "unclaimed", so
///   the form must distinguish "leave it alone" from "release it".
class EditSocialProfilePage extends ConsumerStatefulWidget {
  const EditSocialProfilePage({super.key});

  @override
  ConsumerState<EditSocialProfilePage> createState() =>
      _EditSocialProfilePageState();
}

class _EditSocialProfilePageState extends ConsumerState<EditSocialProfilePage> {
  final _formKey = GlobalKey<FormState>();
  final _username = TextEditingController();
  final _displayName = TextEditingController();
  final _bio = TextEditingController();
  final _category = TextEditingController();
  final _country = TextEditingController();
  final _city = TextEditingController();

  bool _hydrated = false;
  String? _error;
  String? _usernameCleared = 'no'; // sentinel: not yet compared

  @override
  void dispose() {
    _username.dispose();
    _displayName.dispose();
    _bio.dispose();
    _category.dispose();
    _country.dispose();
    _city.dispose();
    super.dispose();
  }

  /// Seed the form from the server profile exactly once per load.
  void _hydrate(SocialProfile p) {
    if (_hydrated) return;
    _hydrated = true;
    _username.text = p.username ?? '';
    _displayName.text = p.displayName;
    _bio.text = p.bio ?? '';
    _category.text = p.cyclingCategory ?? '';
    _country.text = p.countryCode ?? '';
    _city.text = p.city ?? '';
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final profile = ref.watch(mySocialProfileProvider);
    final busy = ref.watch(socialActionsProvider).contains('profile');

    return Scaffold(
      appBar: AppBar(title: Text(t.get('social.editProfile'))),
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
          _hydrate(p);
          final clearUsername =
              _usernameCleared == 'yes' && _username.text.trim().isEmpty;
          return Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                if (_error != null) ...[
                  Container(
                    key: const Key('social.edit.error'),
                    padding: const EdgeInsets.all(AppSpacing.md),
                    decoration: BoxDecoration(
                      color: Theme.of(
                        context,
                      ).colorScheme.error.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(AppRadius.card),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.error_outline_rounded,
                          color: Theme.of(context).colorScheme.error,
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Expanded(child: Text(_error!)),
                      ],
                    ),
                  ),
                  const SizedBox(height: AppSpacing.md),
                ],
                TextFormField(
                  key: const Key('social.field.username'),
                  controller: _username,
                  enabled: !busy,
                  maxLength: 30,
                  decoration: InputDecoration(
                    labelText: t.get('social.username'),
                    helperText: t.get('social.usernameHint'),
                    helperMaxLines: 2,
                  ),
                  onChanged: (_) => setState(() => _usernameCleared = 'no'),
                  validator: (v) {
                    final key = SocialValidators.username(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                if (p.username != null)
                  TextButton(
                    key: const Key('social.clearUsername'),
                    onPressed: busy
                        ? null
                        : () => setState(() {
                            _username.clear();
                            _usernameCleared = 'yes';
                          }),
                    child: Text(t.get('social.noUsername')),
                  ),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  key: const Key('social.field.displayName'),
                  controller: _displayName,
                  enabled: !busy,
                  maxLength: 80,
                  decoration: InputDecoration(
                    labelText: t.get('social.displayName'),
                  ),
                  validator: (v) {
                    final key = SocialValidators.displayName(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  key: const Key('social.field.bio'),
                  controller: _bio,
                  enabled: !busy,
                  maxLength: 500,
                  maxLines: 4,
                  decoration: InputDecoration(
                    labelText: t.get('social.bio'),
                    helperText: t.get('social.bioHint'),
                    helperMaxLines: 2,
                  ),
                  validator: (v) {
                    final key = SocialValidators.bio(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  key: const Key('social.field.category'),
                  controller: _category,
                  enabled: !busy,
                  maxLength: 32,
                  decoration: InputDecoration(
                    labelText: t.get('social.cyclingCategory'),
                  ),
                  validator: (v) {
                    final key = SocialValidators.cyclingCategory(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  key: const Key('social.field.country'),
                  controller: _country,
                  enabled: !busy,
                  maxLength: 2,
                  textCapitalization: TextCapitalization.characters,
                  decoration: InputDecoration(
                    labelText: t.get('social.country'),
                    helperText: t.get('social.countryHint'),
                  ),
                  validator: (v) {
                    final key = SocialValidators.countryCode(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  key: const Key('social.field.city'),
                  controller: _city,
                  enabled: !busy,
                  maxLength: 120,
                  decoration: InputDecoration(
                    labelText: t.get('social.city'),
                    helperText: t.get('social.cityHint'),
                  ),
                  validator: (v) {
                    final key = SocialValidators.city(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                const SizedBox(height: AppSpacing.lg),
                SocialActionButton(
                  buttonKey: const Key('social.edit.save'),
                  label: t.get('social.editProfile'),
                  busyLabel: t.get('social.loading'),
                  busy: busy,
                  primary: true,
                  onPressed: busy ? null : () => _submit(clearUsername),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Future<void> _submit(bool clearUsername) async {
    final t = context.l10n;
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() => _error = null);
    try {
      await ref
          .read(socialActionsProvider.notifier)
          .saveProfile(
            username: _username.text.trim().toLowerCase(),
            displayName: _displayName.text.trim(),
            bio: _bio.text.trim(),
            cyclingCategory: _category.text.trim(),
            countryCode: _country.text.trim(),
            city: _city.text.trim(),
            clearUsername: clearUsername,
          );
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(t.get('social.profileSaved'))));
      context.pop();
    } on Object catch (e) {
      if (!mounted) return;
      // The server's verdict wins. Stay on the form so the rider can fix it.
      setState(() => _error = friendlyError(t, e));
    }
  }
}
