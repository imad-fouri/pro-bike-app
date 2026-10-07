import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../teams/presentation/team_providers.dart';
import '../domain/competition_models.dart';
import 'competition_providers.dart';

/// Create-a-challenge form.
///
/// Only *targets* are collected here — metric, target value, points, scope,
/// window. Progress, completion and rank are never input fields because the
/// server never accepts them (WS-RC server-authority law).
class ChallengeFormPage extends ConsumerStatefulWidget {
  const ChallengeFormPage({super.key});

  @override
  ConsumerState<ChallengeFormPage> createState() => _ChallengeFormPageState();
}

class _ChallengeFormPageState extends ConsumerState<ChallengeFormPage> {
  static const _maxDays = 366;

  final _title = TextEditingController();
  final _description = TextEditingController();
  final _target = TextEditingController();
  final _points = TextEditingController();
  ChallengeMetric _metric = ChallengeMetric.distance;
  ChallengeScope _scope = ChallengeScope.global;
  ChallengeVisibility _visibility = ChallengeVisibility.public_;
  String? _teamId;
  DateTime _start = DateTime.now();
  DateTime _end = DateTime.now().add(const Duration(days: 7));
  bool _publish = true;
  String? _error;

  @override
  void dispose() {
    _title.dispose();
    _description.dispose();
    _target.dispose();
    _points.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final teams = ref.watch(myTeamsProvider(false)).value;
    final busyIds = ref.watch(challengeActionsProvider);
    final busy = busyIds.any((k) => k.startsWith('create:'));

    return Scaffold(
      appBar: AppBar(title: Text(t.get('competition.createTitle'))),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          TextField(
            key: const Key('challenge.field.title'),
            controller: _title,
            maxLength: 120,
            decoration: InputDecoration(
              labelText: t.get('competition.title'),
              border: const OutlineInputBorder(),
            ),
          ),
          TextField(
            key: const Key('challenge.field.description'),
            controller: _description,
            maxLength: 1000,
            maxLines: 3,
            decoration: InputDecoration(
              labelText: t.get('competition.description'),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          DropdownButtonFormField<ChallengeMetric>(
            key: const Key('challenge.field.metric'),
            initialValue: _metric,
            items: [
              for (final m in ChallengeMetric.values)
                DropdownMenuItem(value: m, child: Text(t.get(m.labelKey))),
            ],
            onChanged: (v) => setState(() => _metric = v ?? _metric),
            decoration: InputDecoration(
              labelText: t.get('competition.metric'),
              border: const OutlineInputBorder(),
            ),
          ),
          TextField(
            key: const Key('challenge.field.target'),
            controller: _target,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: t.get('competition.targetValue'),
              helperText: t.get('competition.targetHint'),
              border: const OutlineInputBorder(),
            ),
          ),
          TextField(
            key: const Key('challenge.field.points'),
            controller: _points,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: t.get('competition.pointsOptional'),
              helperText: t.get('competition.pointsHint'),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          DropdownButtonFormField<ChallengeScope>(
            key: const Key('challenge.field.scope'),
            initialValue: _scope,
            items: [
              for (final s in ChallengeScope.values)
                DropdownMenuItem(value: s, child: Text(t.get(s.labelKey))),
            ],
            onChanged: (v) => setState(() => _scope = v ?? _scope),
            decoration: InputDecoration(
              labelText: t.get('competition.scope'),
              border: const OutlineInputBorder(),
            ),
          ),
          if (_scope == ChallengeScope.team) ...[
            const SizedBox(height: AppSpacing.sm),
            if (teams == null || teams.items.isEmpty)
              Text(
                t.get('competition.noTeamToSelect'),
                style: const TextStyle(color: AppColors.textMuted),
              )
            else
              DropdownButtonFormField<String>(
                key: const Key('challenge.field.team'),
                initialValue: _teamId ?? teams.items.first.id,
                items: [
                  for (final team in teams.items)
                    DropdownMenuItem(value: team.id, child: Text(team.name)),
                ],
                onChanged: (v) => setState(() => _teamId = v),
                decoration: InputDecoration(
                  labelText: t.get('competition.team'),
                  border: const OutlineInputBorder(),
                ),
              ),
          ],
          const SizedBox(height: AppSpacing.md),
          DropdownButtonFormField<ChallengeVisibility>(
            key: const Key('challenge.field.visibility'),
            initialValue: _visibility,
            items: [
              for (final v in ChallengeVisibility.values)
                DropdownMenuItem(value: v, child: Text(t.get(v.labelKey))),
            ],
            onChanged: (v) => setState(() => _visibility = v ?? _visibility),
            decoration: InputDecoration(
              labelText: t.get('competition.visibility'),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(t.get('competition.visibilityHint')),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Expanded(
                child: _DateField(
                  key: const Key('challenge.field.start'),
                  label: t.get('competition.start'),
                  date: _start,
                  onPick: () async => _pickDate(isStart: true),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: _DateField(
                  key: const Key('challenge.field.end'),
                  label: t.get('competition.end'),
                  date: _end,
                  onPick: () async => _pickDate(isStart: false),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          SwitchListTile(
            key: const Key('challenge.field.publish'),
            title: Text(t.get('competition.publishNow')),
            subtitle: Text(t.get('competition.publishHint')),
            value: _publish,
            onChanged: (v) => setState(() => _publish = v),
          ),
          if (_error != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(_error!, style: const TextStyle(color: AppColors.zone5)),
          ],
          const SizedBox(height: AppSpacing.md),
          FilledButton(
            key: const Key('challenge.submit'),
            onPressed: busy ? null : _submit,
            child: Text(
              busy ? t.get('competition.saving') : t.get('competition.create'),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _pickDate({required bool isStart}) async {
    final now = DateTime.now();
    final initial = isStart ? _start : _end;
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: now.subtract(const Duration(days: 1)),
      lastDate: now.add(const Duration(days: 370)),
    );
    if (picked == null) return;
    setState(() {
      final dateOnly = DateTime(picked.year, picked.month, picked.day);
      if (isStart) {
        _start = dateOnly;
        if (_end.isBefore(_start)) _end = _start.add(const Duration(days: 7));
      } else {
        _end = dateOnly;
      }
    });
  }

  Future<void> _submit() async {
    final t = context.l10n;
    final key = _validate();
    if (key != null) {
      setState(() => _error = t.get(key));
      return;
    }
    final target = double.tryParse(_target.text.trim()) ?? 0;
    final points = _points.text.trim().isEmpty
        ? null
        : int.tryParse(_points.text.trim());
    final teamId = _scope == ChallengeScope.team ? _teamId : null;
    setState(() => _error = null);
    try {
      final created = await ref
          .read(challengeActionsProvider.notifier)
          .create(
            title: _title.text.trim(),
            description: _description.text.trim().isEmpty
                ? null
                : _description.text.trim(),
            metric: _metric,
            target: target,
            points: points,
            scope: _scope,
            visibility: _visibility,
            teamId: teamId,
            startAt: _start,
            endAt: _end,
            publish: _publish,
          );
      if (!mounted) return;
      if (created != null) {
        context.go('/challenges/${created.id}');
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  String? _validate() {
    if (_title.text.trim().isEmpty || _title.text.trim().length > 120) {
      return 'competition.invalidTitle';
    }
    if (_description.text.trim().length > 1000) {
      return 'competition.invalidDescription';
    }
    final target = double.tryParse(_target.text.trim());
    if (target == null || target <= 0) return 'competition.invalidTarget';
    if (target > 99999999999999.99) return 'competition.invalidTarget';
    final pointsText = _points.text.trim();
    if (pointsText.isNotEmpty) {
      final points = int.tryParse(pointsText);
      if (points == null || points < 10 || points > 1000) {
        return 'competition.invalidPoints';
      }
    }
    if (_end.isBefore(_start) || _end.isAtSameMomentAs(_start)) {
      return 'competition.invalidWindow';
    }
    if (!_end.isAfter(DateTime.now())) return 'competition.invalidWindow';
    if (_end.difference(_start).inDays > _maxDays) {
      return 'competition.windowTooLong';
    }
    if (_scope == ChallengeScope.team &&
        (_teamId == null || _teamId!.isEmpty)) {
      return 'competition.teamRequired';
    }
    return null;
  }
}

class _DateField extends StatelessWidget {
  final String label;
  final DateTime date;
  final Future<void> Function() onPick;

  const _DateField({
    super.key,
    required this.label,
    required this.date,
    required this.onPick,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onPick,
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          suffixIcon: const Icon(Icons.calendar_today_outlined, size: 18),
        ),
        child: Text(_ymd(date)),
      ),
    );
  }
}

String _ymd(DateTime d) {
  final m = d.month.toString().padLeft(2, '0');
  final day = d.day.toString().padLeft(2, '0');
  return '${d.year}-$m-$day';
}
