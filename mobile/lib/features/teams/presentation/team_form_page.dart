import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import '../domain/team_validators.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// Create and edit share one form, because they submit the same fields.
///
/// Only identity fields appear — `owner`, roles, `member_count`, and every
/// manager counter are server-owned and absent from this form by construction,
/// so there is no code path that could submit them (ADR-13 §4).
///
/// Visibility is editable here only when creating. An admin may reach this
/// screen for description/logo only; the owner-only fields are not rendered for
/// an admin, rather than rendered and rejected by the server.
class TeamFormPage extends ConsumerStatefulWidget {
  /// null → create; non-null → edit that team.
  final String? teamId;

  const TeamFormPage({super.key, this.teamId});

  bool get isCreate => teamId == null;

  @override
  ConsumerState<TeamFormPage> createState() => _TeamFormPageState();
}

class _TeamFormPageState extends ConsumerState<TeamFormPage> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _handle = TextEditingController();
  final _description = TextEditingController();
  final _category = TextEditingController();
  final _avatar = TextEditingController();

  TeamVisibility _visibility = TeamVisibility.public_;
  bool _hydrated = false;
  bool _handleCleared = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _handle.dispose();
    _description.dispose();
    _category.dispose();
    _avatar.dispose();
    super.dispose();
  }

  void _hydrate(Team team) {
    if (_hydrated) return;
    _hydrated = true;
    _name.text = team.name;
    _handle.text = team.handle ?? '';
    _description.text = team.description ?? '';
    _category.text = team.category ?? '';
    _avatar.text = team.avatarUrl ?? '';
    _visibility = team.visibility;
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final busy = ref
        .watch(teamActionsProvider)
        .contains(
          widget.teamId == null
              ? 'create'
              : TeamActions.teamKey('update', widget.teamId!),
        );

    if (widget.isCreate) {
      return _formShell(t, busy, null);
    }

    final detail = ref.watch(teamDetailProvider(widget.teamId!));
    return detail.when(
      loading: () => Center(
        child: Text(
          t.get('social.loading'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      ),
      error: (e, _) => SocialErrorView(
        error: e,
        message: friendlyTeamError(t, e),
        onRetry: () => ref.invalidate(teamDetailProvider(widget.teamId!)),
      ),
      data: (team) {
        _hydrate(team);
        // An admin gets the limited settings the server allows; the owner-only
        // fields are simply not rendered.
        final isOwner = team.myRole?.isOwner ?? false;
        return _formShell(t, busy, team, isOwner: isOwner);
      },
    );
  }

  Widget _formShell(
    AppLocalizations t,
    bool busy,
    Team? team, {
    bool isOwner = true,
  }) {
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.isCreate ? t.get('team.createTeam') : t.get('team.editTeam'),
        ),
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.md),
          children: [
            if (_error != null) ...[
              Container(
                key: const Key('team.form.error'),
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
            TextFormField(
              key: const Key('team.field.name'),
              controller: _name,
              enabled: !busy,
              maxLength: 80,
              decoration: InputDecoration(labelText: t.get('team.name')),
              validator: (v) {
                final key = TeamValidators.name(v ?? '');
                return key == null ? null : t.get(key);
              },
            ),
            const SizedBox(height: AppSpacing.sm),
            TextFormField(
              key: const Key('team.field.handle'),
              controller: _handle,
              enabled: !busy && (widget.isCreate || isOwner),
              maxLength: 30,
              decoration: InputDecoration(
                labelText: t.get('team.handle'),
                helperText: t.get('team.handleHint'),
                helperMaxLines: 2,
              ),
              onChanged: (_) => setState(() => _handleCleared = false),
              validator: (v) {
                final key = TeamValidators.handle(v ?? '');
                return key == null ? null : t.get(key);
              },
            ),
            if (!widget.isCreate && (team?.handle ?? '').isNotEmpty)
              TextButton(
                key: const Key('team.clearHandle'),
                onPressed: busy || !isOwner
                    ? null
                    : () => setState(() {
                        _handle.clear();
                        _handleCleared = true;
                      }),
                child: Text(t.get('team.noHandle')),
              ),
            const SizedBox(height: AppSpacing.sm),
            TextFormField(
              key: const Key('team.field.description'),
              controller: _description,
              enabled: !busy,
              maxLength: 500,
              maxLines: 3,
              decoration: InputDecoration(labelText: t.get('team.description')),
              validator: (v) {
                final key = TeamValidators.description(v ?? '');
                return key == null ? null : t.get(key);
              },
            ),
            const SizedBox(height: AppSpacing.md),
            TextFormField(
              key: const Key('team.field.category'),
              controller: _category,
              enabled: !busy,
              maxLength: 32,
              decoration: InputDecoration(
                labelText: t.get('team.category'),
                helperText: t.get('team.categoryHint'),
              ),
              validator: (v) {
                final key = TeamValidators.category(v ?? '');
                return key == null ? null : t.get(key);
              },
            ),
            const SizedBox(height: AppSpacing.md),
            TextFormField(
              key: const Key('team.field.avatar'),
              controller: _avatar,
              enabled: !busy,
              maxLength: 512,
              decoration: InputDecoration(
                labelText: t.get('team.avatar'),
                helperText: t.get('team.avatarHint'),
              ),
              validator: (v) {
                final key = TeamValidators.avatarUrl(v ?? '');
                return key == null ? null : t.get(key);
              },
            ),
            // Visibility changes who can find the team, so it is owner-only and
            // hidden from an admin rather than shown and refused.
            if (widget.isCreate || isOwner) ...[
              const SizedBox(height: AppSpacing.md),
              Text(
                t.get('team.visibility'),
                style: Theme.of(
                  context,
                ).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700),
              ),
              RadioGroup<TeamVisibility>(
                groupValue: _visibility,
                onChanged: busy
                    ? (TeamVisibility? _) {}
                    : (TeamVisibility? v) {
                        if (v != null) setState(() => _visibility = v);
                      },
                child: Column(
                  children: [
                    for (final v in TeamValidators.visibilities)
                      RadioListTile<TeamVisibility>(
                        key: Key('team.visibility.${v.wire}'),
                        value: v,
                        contentPadding: EdgeInsets.zero,
                        title: Text(t.get(v.labelKey)),
                      ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            SocialActionButton(
              buttonKey: const Key('team.form.save'),
              label: widget.isCreate
                  ? t.get('team.createTeam')
                  : t.get('team.editTeam'),
              busyLabel: t.get('social.loading'),
              busy: busy,
              primary: true,
              onPressed: busy ? null : _submit,
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _submit() async {
    final t = context.l10n;
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() => _error = null);
    final clearHandle = _handleCleared && _handle.text.trim().isEmpty;
    try {
      if (widget.isCreate) {
        await ref
            .read(teamActionsProvider.notifier)
            .createTeam(
              name: _name.text.trim(),
              handle: _handle.text.trim(),
              description: _description.text.trim(),
              avatarUrl: _avatar.text.trim(),
              category: _category.text.trim(),
              visibility: _visibility,
            );
      } else {
        await ref
            .read(teamActionsProvider.notifier)
            .updateTeam(
              teamId: widget.teamId!,
              name: _name.text.trim(),
              handle: _handle.text.trim(),
              description: _description.text.trim(),
              avatarUrl: _avatar.text.trim(),
              category: _category.text.trim(),
              visibility: _visibility,
              clearHandle: clearHandle,
            );
      }
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            widget.isCreate
                ? t.get('team.teamCreated')
                : t.get('team.teamSaved'),
          ),
        ),
      );
      context.pop();
    } on Object catch (e) {
      if (!mounted) return;
      // The server's verdict wins; stay on the form so the rider can fix it.
      setState(() => _error = friendlyTeamError(t, e));
    }
  }
}
