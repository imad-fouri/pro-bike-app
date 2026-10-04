import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../domain/training_models.dart';
import '../domain/training_units.dart';
import 'training_providers.dart';

/// Workout list. Empty state, create, and a 409 on edit all surface as
/// something the rider can act on — a version conflict never silently wins.
class WorkoutListPage extends ConsumerWidget {
  const WorkoutListPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = AppLocalizations.of(context);
    final workouts = ref.watch(workoutListProvider);
    return Scaffold(
      body: workouts.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Text(
            l10n.get('training.error.load'),
            key: const Key('training.workoutError'),
          ),
        ),
        data: (items) => items.isEmpty
            ? Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    l10n.get('training.workoutsEmpty'),
                    key: const Key('training.workoutsEmpty'),
                    textAlign: TextAlign.center,
                  ),
                ),
              )
            : ListView.builder(
                itemCount: items.length,
                itemBuilder: (context, i) => WorkoutTile(workout: items[i]),
              ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('training.workout.new'),
        onPressed: () => _openForm(context, ref),
        icon: const Icon(Icons.add),
        label: Text(l10n.get('training.workout.new')),
      ),
    );
  }

  void _openForm(BuildContext context, WidgetRef ref) {
    Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => const WorkoutFormPage()));
  }
}

class WorkoutTile extends ConsumerWidget {
  final Workout workout;
  const WorkoutTile({super.key, required this.workout});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = AppLocalizations.of(context);
    return ListTile(
      key: Key('training.workout.${workout.id}'),
      title: Text(workout.name),
      subtitle: Text(
        [
          l10n.get(workout.status.l10nKey),
          formatTrainingDuration(workout.plannedSeconds),
          '${l10n.get('training.version')} ${workout.version}',
        ].join(' · '),
      ),
      trailing: IconButton(
        key: Key('training.workout.delete.${workout.id}'),
        icon: const Icon(Icons.delete_outline),
        tooltip: l10n.get('training.workout.delete'),
        onPressed: () async {
          final messenger = ScaffoldMessenger.of(context);
          await ref.read(workoutListProvider.notifier).remove(workout.id);
          messenger.showSnackBar(
            SnackBar(content: Text(l10n.get('training.workout.deleted'))),
          );
        },
      ),
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => WorkoutFormPage(existing: workout),
        ),
      ),
    );
  }
}

/// Create or edit. On edit the read [Workout.version] travels with the save,
/// so a concurrent change is rejected rather than overwritten.
class WorkoutFormPage extends ConsumerStatefulWidget {
  final Workout? existing;
  const WorkoutFormPage({super.key, this.existing});

  @override
  ConsumerState<WorkoutFormPage> createState() => _WorkoutFormPageState();
}

class _WorkoutFormPageState extends ConsumerState<WorkoutFormPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _name = TextEditingController(
    text: widget.existing?.name ?? '',
  );
  late final TextEditingController _goal = TextEditingController(
    text: widget.existing?.goal ?? '',
  );
  late final List<WorkoutStepInput> _steps = [
    if (widget.existing != null)
      for (final s in widget.existing!.steps)
        WorkoutStepInput(
          stepType: s.stepType,
          label: s.label,
          durationS: s.durationS,
          repeatCount: s.repeatCount,
          targetZone: s.targetZone,
          targetPowerLowW: s.targetPowerLowW,
          targetPowerHighW: s.targetPowerHighW,
        ),
  ];
  bool _saving = false;
  String? _conflict;

  @override
  void dispose() {
    _name.dispose();
    _goal.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    if (_name.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _conflict = null;
    });
    final notifier = ref.read(workoutListProvider.notifier);
    final input = WorkoutInput(
      name: _name.text.trim(),
      goal: _goal.text.trim().isEmpty ? null : _goal.text.trim(),
      steps: _steps,
    );
    try {
      if (widget.existing == null) {
        await notifier.create(input);
      } else {
        await notifier.saveWorkout(
          widget.existing!.id,
          expectedVersion: widget.existing!.version,
          input: input,
        );
      }
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      // A 409 surfaces as a reload prompt, not a silent last-write-wins. Any
      // other failure is reported as a plain save error, so a network problem
      // is never mislabelled as someone else editing the workout.
      if (mounted) {
        setState(() {
          _saving = false;
          _conflict = AppLocalizations.of(context).get(
            e.code == 'VERSION_CONFLICT'
                ? 'training.workout.conflict'
                : 'training.error.save',
          );
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _saving = false;
          _conflict = AppLocalizations.of(context).get('training.error.save');
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final plannedSeconds = _steps.fold(
      0,
      (sum, s) => sum + s.durationS * s.repeatCount,
    );
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.existing == null
              ? l10n.get('training.workout.new')
              : l10n.get('training.workout.name'),
        ),
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_conflict != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(
                  _conflict!,
                  key: const Key('training.workoutConflict'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            TextFormField(
              key: const Key('training.workout.name'),
              controller: _name,
              decoration: InputDecoration(
                labelText: l10n.get('training.workout.name'),
              ),
              validator: (v) => (v ?? '').trim().isEmpty
                  ? l10n.get('training.workout.name')
                  : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const Key('training.workout.goal'),
              controller: _goal,
              decoration: InputDecoration(
                labelText: l10n.get('training.workout.goal'),
              ),
            ),
            const Divider(height: 32),
            Text(
              l10n.get('training.workout.steps'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            for (var i = 0; i < _steps.length; i++)
              _StepEditor(
                key: Key('training.workout.step.$i'),
                step: _steps[i],
                onChanged: (next) => setState(() => _steps[i] = next),
                onRemove: () => setState(() => _steps.removeAt(i)),
              ),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                key: const Key('training.workout.addStep'),
                onPressed: () => setState(
                  () => _steps.add(
                    const WorkoutStepInput(label: 'Step', durationS: 600),
                  ),
                ),
                icon: const Icon(Icons.add),
                label: Text(l10n.get('training.workout.addStep')),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              '${l10n.get('training.workout.plannedTotal')}: '
              '${formatTrainingDuration(plannedSeconds)}',
              key: const Key('training.workout.plannedTotal'),
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const SizedBox(height: 20),
            FilledButton(
              key: const Key('training.workout.save'),
              onPressed: _saving ? null : _save,
              child: Text(l10n.get('training.save')),
            ),
          ],
        ),
      ),
    );
  }
}

class _StepEditor extends StatefulWidget {
  final WorkoutStepInput step;
  final void Function(WorkoutStepInput) onChanged;
  final VoidCallback onRemove;

  const _StepEditor({
    super.key,
    required this.step,
    required this.onChanged,
    required this.onRemove,
  });

  @override
  State<_StepEditor> createState() => _StepEditorState();
}

class _StepEditorState extends State<_StepEditor> {
  late final TextEditingController _label = TextEditingController(
    text: widget.step.label,
  );
  late final TextEditingController _duration = TextEditingController(
    text: '${widget.step.durationS}',
  );
  late final TextEditingController _repeats = TextEditingController(
    text: '${widget.step.repeatCount}',
  );
  late WorkoutStepType _type = widget.step.stepType;

  @override
  void dispose() {
    _label.dispose();
    _duration.dispose();
    _repeats.dispose();
    super.dispose();
  }

  void _emit() {
    widget.onChanged(
      WorkoutStepInput(
        stepType: _type,
        label: _label.text.trim().isEmpty ? 'Step' : _label.text.trim(),
        durationS: int.tryParse(_duration.text.trim()) ?? widget.step.durationS,
        repeatCount: int.tryParse(_repeats.text.trim()) ?? 1,
        targetZone: widget.step.targetZone,
        targetPowerLowW: widget.step.targetPowerLowW,
        targetPowerHighW: widget.step.targetPowerHighW,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            TextFormField(
              controller: _label,
              decoration: InputDecoration(
                labelText: l10n.get('training.workout.stepLabel'),
              ),
              onChanged: (_) => _emit(),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<WorkoutStepType>(
                    initialValue: _type,
                    decoration: InputDecoration(
                      labelText: l10n.get('training.workout.stepType'),
                    ),
                    items: [
                      for (final t in WorkoutStepType.values)
                        DropdownMenuItem(
                          value: t,
                          child: Text(l10n.get(t.l10nKey)),
                        ),
                    ],
                    onChanged: (v) {
                      setState(() => _type = v ?? WorkoutStepType.steady);
                      _emit();
                    },
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: TextFormField(
                    controller: _duration,
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                      labelText: l10n.get('training.workout.duration'),
                    ),
                    onChanged: (_) => _emit(),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextFormField(
                    controller: _repeats,
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                      labelText: l10n.get('training.workout.repeats'),
                    ),
                    onChanged: (_) => _emit(),
                  ),
                ),
                IconButton(
                  onPressed: widget.onRemove,
                  icon: const Icon(Icons.remove_circle_outline),
                  tooltip: l10n.get('training.workout.removeStep'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
