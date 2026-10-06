import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../../shared/widgets/pro_locked_feature.dart';
import '../../subscriptions/domain/entitlement.dart';
import '../../training/domain/training_models.dart';
import '../../training/domain/training_units.dart';
import '../../training/presentation/training_providers.dart';
import '../domain/coach_models.dart';
import 'coach_providers.dart';

/// The Coach screen. One question, one answer, and an honest label on which of
/// the two you got.
///
/// There is no transcript here on purpose: nothing the rider types is kept in
/// the app, and nothing the Coach says is reused for the next question
/// (ADR-11 §6). Asking again replaces the answer.
class CoachPage extends ConsumerStatefulWidget {
  const CoachPage({super.key});

  @override
  ConsumerState<CoachPage> createState() => _CoachPageState();
}

class _CoachPageState extends ConsumerState<CoachPage> {
  CoachIntent _intent = CoachIntent.weeklySummary;
  final _controller = TextEditingController();
  String? _rideId;
  String? _workoutId;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// The server's locale list is en/fr/ar; the app is built for the same three,
  /// so the current UI language is the one to ask in.
  String get _locale => AppLocalizations.of(context).locale.languageCode;

  /// Whether the chosen intent has enough to send. A missing ride or workout
  /// is not an error the server should hear about, so the button stays
  /// disabled instead of producing a 422.
  bool get _canAsk {
    if (_intent == CoachIntent.trainingQuestion) {
      return _controller.text.trim().isNotEmpty;
    }
    if (_intent == CoachIntent.explainRide) return _rideId != null;
    if (_intent == CoachIntent.explainWorkout) return _workoutId != null;
    return true;
  }

  Future<void> _ask() async {
    final intent = _intent;
    final message = _controller.text.trim();
    await ref
        .read(coachAskProvider.notifier)
        .ask(
          intent: intent,
          message: message,
          locale: _locale,
          rideId: intent == CoachIntent.explainRide ? _rideId : null,
          workoutId: intent == CoachIntent.explainWorkout ? _workoutId : null,
        );
  }

  void _selectIntent(CoachIntent intent) {
    setState(() {
      _intent = intent;
      // A pointer for the previous intent must not leak into the next request.
      _rideId = null;
      _workoutId = null;
    });
    ref.invalidate(coachAskProvider);
  }

  /// Quick action: explain the latest ride that has an id to explain. The
  /// pointer comes from the training history the rider already has; no number
  /// is typed, derived, or sent by the client.
  Future<void> _quickLastRide() async {
    final items = ref.read(trainingActivitiesProvider).value?.items ?? const [];
    TrainingActivity? latest;
    for (final a in items) {
      if (a.rideId != null) {
        latest = a;
        break;
      }
    }
    if (latest == null) return;
    setState(() {
      _intent = CoachIntent.explainRide;
      _rideId = latest!.rideId;
      _workoutId = null;
    });
    // No invalidate: `_ask` replaces the answer with loading-then-result, and
    // invalidating first would rebuild into a loading state that the
    // duplicate-submission guard correctly refuses to double-send on.
    await _ask();
  }

  /// Quick action: explain the rider's current workout. Workouts carry no
  /// date, so "today's" means the active plan, falling back to the first
  /// workout when nothing is marked active.
  Future<void> _quickTodaysWorkout() async {
    final workouts = ref.read(workoutListProvider).value ?? const [];
    if (workouts.isEmpty) return;
    Workout todays = workouts.first;
    for (final w in workouts) {
      if (w.status == WorkoutStatus.active) {
        todays = w;
        break;
      }
    }
    setState(() {
      _intent = CoachIntent.explainWorkout;
      _workoutId = todays.id;
      _rideId = null;
    });
    // No invalidate here either: see `_quickLastRide`.
    await _ask();
  }

  /// Quick action: the week needs no pointer, so it asks immediately.
  Future<void> _quickWeek() async {
    setState(() {
      _intent = CoachIntent.weeklySummary;
      _rideId = null;
      _workoutId = null;
    });
    await _ask();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Scaffold(
      appBar: AppBar(title: Text(t.get('coach.title'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _StatusBanner(),
          const SizedBox(height: AppSpacing.lg),
          _QuickActions(
            onLastRide: _quickLastRide,
            onTodaysWorkout: _quickTodaysWorkout,
            onWeek: _quickWeek,
          ),
          const SizedBox(height: AppSpacing.lg),
          _SectionLabel(t.get('coach.pickIntent')),
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final intent in CoachIntent.values)
                ChoiceChip(
                  key: Key('coach.intent.${intent.wire}'),
                  label: Text(t.get(intent.l10nKey)),
                  selected: _intent == intent,
                  onSelected: (_) => _selectIntent(intent),
                ),
            ],
          ),
          if (_intent.needsResourcePointer) ...[
            const SizedBox(height: AppSpacing.lg),
            if (_intent == CoachIntent.explainRide)
              _RidePicker(
                selected: _rideId,
                onChanged: (id) => setState(() => _rideId = id),
              )
            else
              _WorkoutPicker(
                selected: _workoutId,
                onChanged: (id) => setState(() => _workoutId = id),
              ),
          ],
          if (_intent.needsMessage) ...[
            const SizedBox(height: AppSpacing.lg),
            TextField(
              key: const Key('coach.message'),
              controller: _controller,
              maxLines: 4,
              minLines: 2,
              maxLength: 1000,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                labelText: t.get('coach.messageLabel'),
                hintText: t.get('coach.messageHint'),
              ),
            ),
          ],
          const SizedBox(height: AppSpacing.md),
          FilledButton.icon(
            key: const Key('coach.ask'),
            onPressed: _canAsk ? _ask : null,
            icon: const Icon(Icons.auto_awesome_rounded, size: 18),
            label: Text(t.get('coach.ask')),
          ),
          const SizedBox(height: AppSpacing.lg),
          const _Answer(),
        ],
      ),
    );
  }
}

/// Says up front whether the answer will be explained or deterministic. A
/// build with no provider key is a normal state, not a broken screen, so it is
/// stated plainly instead of being hidden.
class _StatusBanner extends ConsumerWidget {
  const _StatusBanner();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final status = ref.watch(coachStatusProvider);
    return status.when(
      loading: () => const LinearProgressIndicator(),
      error: (_, _) => _Banner(
        key: const Key('coach.status.unknown'),
        icon: Icons.help_outline_rounded,
        text: t.get('coach.statusUnknown'),
      ),
      data: (s) => s.fallbackOnly
          ? _Banner(
              key: const Key('coach.status.fallbackOnly'),
              icon: Icons.info_outline_rounded,
              text: t.get('coach.statusFallbackOnly'),
            )
          : _Banner(
              key: const Key('coach.status.live'),
              icon: Icons.auto_awesome_rounded,
              text: '${t.get('coach.statusLive')} · ${s.provider}/${s.model}',
            ),
    );
  }
}

class _Banner extends StatelessWidget {
  final IconData icon;
  final String text;
  const _Banner({super.key, required this.icon, required this.text});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: AppColors.primary),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              text,
              key: const Key('coach.status.text'),
              style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
            ),
          ),
        ],
      ),
    );
  }
}

/// One-tap answers for the three questions riders ask most. Each resolves its
/// pointer from the providers the training screens already use and asks
/// immediately — there is nothing to pick and nothing to type. A button stays
/// disabled while its source is still loading or came back empty, so a quick
/// action can never ask about a ride or workout the app has not seen.
class _QuickActions extends ConsumerWidget {
  final Future<void> Function() onLastRide;
  final Future<void> Function() onTodaysWorkout;
  final Future<void> Function() onWeek;
  const _QuickActions({
    required this.onLastRide,
    required this.onTodaysWorkout,
    required this.onWeek,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(coachAskProvider).isLoading;
    final activities = ref.watch(trainingActivitiesProvider);
    final workouts = ref.watch(workoutListProvider);
    final hasRide =
        activities.value?.items.any((a) => a.rideId != null) ?? false;
    final hasWorkout = (workouts.value ?? const []).isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionLabel(t.get('coach.quickActions')),
        const SizedBox(height: AppSpacing.sm),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.tonalIcon(
              key: const Key('coach.quick.lastRide'),
              onPressed: !busy && hasRide ? () => onLastRide() : null,
              icon: const Icon(Icons.pedal_bike_rounded, size: 18),
              label: Text(t.get('coach.quick.lastRide')),
            ),
            FilledButton.tonalIcon(
              key: const Key('coach.quick.todaysWorkout'),
              onPressed: !busy && hasWorkout ? () => onTodaysWorkout() : null,
              icon: const Icon(Icons.fitness_center_rounded, size: 18),
              label: Text(t.get('coach.quick.todaysWorkout')),
            ),
            FilledButton.tonalIcon(
              key: const Key('coach.quick.week'),
              onPressed: busy ? null : () => onWeek(),
              icon: const Icon(Icons.calendar_view_week_rounded, size: 18),
              label: Text(t.get('coach.quick.week')),
            ),
          ],
        ),
      ],
    );
  }
}

class _SectionLabel extends StatelessWidget {
  final String text;
  const _SectionLabel(this.text);

  @override
  Widget build(BuildContext context) => Text(
    text,
    style: Theme.of(
      context,
    ).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700),
  );
}

/// Rides come from the training history the rider already has. The picker
/// shows the same numbers the history shows — the Coach is an explanation of
/// those, not a second set of them.
class _RidePicker extends ConsumerWidget {
  final String? selected;
  final ValueChanged<String?> onChanged;
  const _RidePicker({required this.selected, required this.onChanged});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final activities = ref.watch(trainingActivitiesProvider);
    return activities.when(
      loading: () => const LinearProgressIndicator(),
      error: (_, _) => Text(
        t.get('coach.pick.error'),
        key: const Key('coach.ridePicker.error'),
      ),
      data: (result) {
        // Only analyzed rides have metrics to explain, and a sensorless ride
        // has a name but nothing to say.
        final items = result.items.where((a) => a.rideId != null).toList();
        if (items.isEmpty) {
          return Text(
            t.get('coach.pick.emptyRides'),
            key: const Key('coach.ridePicker.empty'),
          );
        }
        return DropdownButtonFormField<String>(
          key: const Key('coach.ridePicker'),
          initialValue: selected,
          isExpanded: true,
          decoration: InputDecoration(labelText: t.get('coach.pick.ride')),
          items: [
            for (final a in items)
              DropdownMenuItem(
                value: a.rideId,
                child: Text(
                  '${_date(a.localDate)} · '
                  '${formatTrainingDistance(a.distanceM, imperial: false)} · '
                  '${formatPower(a.normalizedPowerW)}',
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: onChanged,
        );
      },
    );
  }
}

class _WorkoutPicker extends ConsumerWidget {
  final String? selected;
  final ValueChanged<String?> onChanged;
  const _WorkoutPicker({required this.selected, required this.onChanged});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final workouts = ref.watch(workoutListProvider);
    return workouts.when(
      loading: () => const LinearProgressIndicator(),
      error: (_, _) => Text(
        t.get('coach.pick.error'),
        key: const Key('coach.workoutPicker.error'),
      ),
      data: (items) {
        if (items.isEmpty) {
          return Text(
            t.get('coach.pick.emptyWorkouts'),
            key: const Key('coach.workoutPicker.empty'),
          );
        }
        return DropdownButtonFormField<String>(
          key: const Key('coach.workoutPicker'),
          initialValue: selected,
          isExpanded: true,
          decoration: InputDecoration(labelText: t.get('coach.pick.workout')),
          items: [
            for (final w in items)
              DropdownMenuItem(
                value: w.id,
                child: Text(
                  '${w.name} · ${t.get(w.status.l10nKey)}',
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: onChanged,
        );
      },
    );
  }
}

/// One answer, or the reason there is not one yet. A transport error is shown
/// as an error — never as an answer — so a failure can never be mistaken for
/// something the Coach said.
class _Answer extends ConsumerWidget {
  const _Answer();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final ask = ref.watch(coachAskProvider);
    return ask.when(
      loading: () => const Padding(
        padding: EdgeInsets.symmetric(vertical: AppSpacing.xl),
        child: Center(child: CircularProgressIndicator()),
      ),
      // A 403 with ENTITLEMENT_REQUIRED is not a generic failure: the server
      // has authoritatively said this rider's plan does not include the Coach.
      // The lock screen explains that fact; it does not re-decide it.
      error: (e, _) => e is ApiException && e.code == 'ENTITLEMENT_REQUIRED'
          ? const ProLockedFeature(feature: EntitlementFeature.aiCoach)
          : Text(
              t.get('coach.error'),
              key: const Key('coach.answer.error'),
              style: const TextStyle(color: AppColors.zone4),
            ),
      data: (message) => message == null
          ? Text(
              t.get('coach.empty'),
              key: const Key('coach.answer.empty'),
              style: const TextStyle(color: AppColors.textMuted),
            )
          : _AnswerCard(message: message),
    );
  }
}

class _AnswerCard extends StatelessWidget {
  final CoachMessage message;
  const _AnswerCard({required this.message});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      key: const Key('coach.answer'),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                _Tag(
                  key: const Key('coach.answer.intent'),
                  label: t.get(message.intent.l10nKey),
                ),
                _Tag(
                  key: const Key('coach.answer.fallback'),
                  label: message.fallbackUsed
                      ? t.get('coach.fallbackUsed')
                      : t.get('coach.modelAnswer'),
                  highlight: !message.fallbackUsed,
                ),
                if (message.promptVersion != null)
                  _Tag(label: message.promptVersion!),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            Text(
              message.summary,
              key: const Key('coach.answer.summary'),
              style: Theme.of(
                context,
              ).textTheme.bodyLarge?.copyWith(height: 1.4),
            ),
            if (message.observations.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              _Subheading(t.get('coach.observations')),
              for (final o in message.observations)
                _Line(key: Key('coach.observation.${o.metric}'), text: o.text),
            ],
            if (message.recommendations.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              _Subheading(t.get('coach.recommendations')),
              for (final r in message.recommendations)
                _Line(
                  text: r.text,
                  // A prescription's number is only shown when the server sent
                  // one; the client never derives a target of its own.
                  detail: _prescription(r),
                ),
            ],
            // Cautions lead their own block: a limit the Coach must not cross
            // is part of the answer, not a footnote on it.
            for (final c in message.cautions)
              _Line(
                key: Key('coach.caution.${c.code}'),
                text: c.text,
                warning: true,
              ),
            if (message.context.unavailable.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              _Subheading(t.get('coach.notMeasured')),
              for (final u in message.context.unavailable)
                _Line(key: Key('coach.unavailable.$u'), text: u, muted: true),
            ],
            if (message.provenance.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              _Subheading(t.get('coach.provenance')),
              for (final p in message.provenance)
                _Line(
                  key: Key('coach.provenance.${p.metric}'),
                  text:
                      '${p.metric} · ${p.source}'
                      '${p.version == null ? '' : ' · ${p.version}'}',
                  muted: true,
                ),
            ],
          ],
        ),
      ),
    );
  }

  /// e.g. "+8 % of 210 → 227". Shown only when the server range-checked it.
  static String _prescription(CoachRecommendation r) {
    final parts = <String>[];
    if (r.loadChangePct != null) {
      final sign = r.loadChangePct! > 0 ? '+' : '';
      parts.add('$sign${r.loadChangePct!.toStringAsFixed(0)} %');
    }
    if (r.loadTarget != null) {
      parts.add('→ ${r.loadTarget!.toStringAsFixed(0)}');
    }
    return parts.join(' ');
  }
}

class _Subheading extends StatelessWidget {
  final String text;
  const _Subheading(this.text);

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: AppSpacing.xs),
    child: Text(
      text,
      style: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w700,
        color: AppColors.textMuted,
      ),
    ),
  );
}

class _Line extends StatelessWidget {
  final String text;
  final String? detail;
  final bool warning;
  final bool muted;
  const _Line({
    super.key,
    required this.text,
    this.detail,
    this.warning = false,
    this.muted = false,
  });

  @override
  Widget build(BuildContext context) {
    final color = warning
        ? AppColors.zone4
        : muted
        ? AppColors.textMuted
        : AppColors.textOnDark;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 6, right: 8),
            child: Icon(
              warning
                  ? Icons.warning_amber_rounded
                  : muted
                  ? Icons.remove_circle_outline
                  : Icons.circle,
              size: warning ? 14 : 6,
              color: color,
            ),
          ),
          Expanded(
            child: Text(
              text,
              style: TextStyle(fontSize: 13, height: 1.35, color: color),
            ),
          ),
          if (detail != null && detail!.isNotEmpty) ...[
            const SizedBox(width: 8),
            Text(
              detail!,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                color: AppColors.accentLime,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  final String label;
  final bool highlight;
  const _Tag({super.key, required this.label, this.highlight = false});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: highlight
            ? AppColors.primary.withValues(alpha: 0.18)
            : Colors.white.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: highlight ? AppColors.primary : AppColors.textMuted,
        ),
      ),
    );
  }
}

String _date(DateTime value) =>
    '${value.year}-${value.month.toString().padLeft(2, '0')}-'
    '${value.day.toString().padLeft(2, '0')}';
