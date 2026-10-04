import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../domain/training_models.dart';
import '../domain/training_units.dart';
import 'training_overview_page.dart';
import 'training_providers.dart';
import 'workout_pages.dart';

/// Training hub. Tabs follow the four things a rider owns here: what their
/// numbers are, what happened, what they plan to ride, and what the engine
/// knows about them.
class TrainingPage extends ConsumerStatefulWidget {
  const TrainingPage({super.key});

  @override
  ConsumerState<TrainingPage> createState() => _TrainingPageState();
}

class _TrainingPageState extends ConsumerState<TrainingPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 4, vsync: this);

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.get('training.title')),
        bottom: TabBar(
          key: const Key('training.tabs'),
          controller: _tabs,
          isScrollable: true,
          tabs: [
            Tab(
              key: const Key('training.tab.overview'),
              text: l10n.get('training.tabOverview'),
            ),
            Tab(
              key: const Key('training.tab.history'),
              text: l10n.get('training.tabHistory'),
            ),
            Tab(
              key: const Key('training.tab.workouts'),
              text: l10n.get('training.tabWorkouts'),
            ),
            Tab(
              key: const Key('training.tab.settings'),
              text: l10n.get('training.tabSettings'),
            ),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: const [
          TrainingOverviewPage(),
          TrainingHistoryPage(),
          WorkoutListPage(),
          TrainingSettingsPage(),
        ],
      ),
    );
  }
}

/// Every analyzed ride, plus the FTP history that produced their numbers.
class TrainingHistoryPage extends ConsumerWidget {
  const TrainingHistoryPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = AppLocalizations.of(context);
    final activities = ref.watch(trainingActivitiesProvider);
    final history = ref.watch(ftpHistoryProvider);
    return activities.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(
        child: Text(
          l10n.get('training.error.load'),
          key: const Key('training.historyError'),
        ),
      ),
      data: (result) => RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(trainingActivitiesProvider);
          ref.invalidate(ftpHistoryProvider);
          await ref.read(trainingActivitiesProvider.future);
        },
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(
              l10n.get('training.ftpHistory'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            history.when(
              loading: () => const LinearProgressIndicator(),
              error: (e, _) => Text(l10n.get('training.unavailable')),
              data: (data) => data.items.isEmpty
                  ? Text(
                      l10n.get('training.ftpHistoryEmpty'),
                      key: const Key('training.ftpHistoryEmpty'),
                    )
                  : Column(
                      children: [
                        for (final record in data.items)
                          _FtpRecordTile(record: record, effective: data),
                      ],
                    ),
            ),
            const Divider(height: 32),
            Text(
              l10n.get('training.activities'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (result.items.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 24),
                child: Text(
                  l10n.get('training.noActivitiesHint'),
                  key: const Key('training.noActivities'),
                  textAlign: TextAlign.center,
                ),
              )
            else
              for (final activity in result.items)
                ActivityTile(
                  activity: activity,
                  onReanalyze: activity.rideId == null
                      ? null
                      : () async {
                          final messenger = ScaffoldMessenger.of(context);
                          final outcome = await ref
                              .read(reanalyzeProvider.notifier)
                              .run(activity.rideId!);
                          messenger.showSnackBar(
                            SnackBar(
                              content: Text(
                                outcome == null
                                    ? l10n.get('training.unavailable')
                                    : l10n.get('training.reanalyzeDone'),
                              ),
                            ),
                          );
                        },
                ),
          ],
        ),
      ),
    );
  }
}

class _FtpRecordTile extends StatelessWidget {
  final FtpRecord record;
  final FtpHistory effective;
  const _FtpRecordTile({required this.record, required this.effective});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final isEffective = effective.effectiveFtpW == record.valueW;
    return ListTile(
      key: Key('training.ftpRecord.${record.id}'),
      dense: true,
      title: Text(formatFtp(record.valueW)),
      subtitle: Text(
        [
          l10n.get(record.l10nKey),
          if (record.approximation) l10n.get('training.approximation'),
          if (!record.confirmed) l10n.get('training.ftpBasis.estimated'),
        ].join(' · '),
      ),
      trailing: isEffective
          ? Chip(
              visualDensity: VisualDensity.compact,
              label: Text(l10n.get('training.ftpUsed')),
            )
          : null,
    );
  }
}

/// Training inputs. Saving FTP appends a record; the note says so, because a
/// silent overwrite of a rider's history would be a lie.
class TrainingSettingsPage extends ConsumerWidget {
  const TrainingSettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = AppLocalizations.of(context);
    final summary = ref.watch(trainingSummaryProvider);
    final saving = ref.watch(trainingProfileEditorProvider);
    return summary.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text(l10n.get('training.error.load'))),
      data: (data) => _SettingsForm(
        key: const Key('training.settingsForm'),
        profile: data.profile,
        saving: saving.isLoading,
        onSave: (values) async {
          final messenger = ScaffoldMessenger.of(context);
          await ref
              .read(trainingProfileEditorProvider.notifier)
              .save(
                ftpW: values.ftpW,
                ftpSource: values.ftpSource,
                maxHrBpm: values.maxHrBpm,
                restingHrBpm: values.restingHrBpm,
                hrZoneModel: values.hrZoneModel,
                timezone: values.timezone,
              );
          // The notifier swallows the error into its state, so the outcome has
          // to be read back — otherwise a failed save reports success.
          final failed = ref.read(trainingProfileEditorProvider).hasError;
          if (!context.mounted) {
            return;
          }
          messenger.showSnackBar(
            SnackBar(
              content: Text(
                l10n.get(failed ? 'training.error.save' : 'training.saved'),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _SettingsForm extends StatefulWidget {
  final TrainingProfile profile;
  final bool saving;
  final void Function(_SettingsValues values) onSave;

  const _SettingsForm({
    super.key,
    required this.profile,
    required this.saving,
    required this.onSave,
  });

  @override
  State<_SettingsForm> createState() => _SettingsFormState();
}

class _SettingsFormState extends State<_SettingsForm> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _ftp = TextEditingController(
    text: widget.profile.ftpW?.round().toString() ?? '',
  );
  late final TextEditingController _maxHr = TextEditingController(
    text: widget.profile.maxHrBpm?.round().toString() ?? '',
  );
  late final TextEditingController _restingHr = TextEditingController(
    text: widget.profile.restingHrBpm?.round().toString() ?? '',
  );
  late final TextEditingController _timezone = TextEditingController(
    text: widget.profile.timezone ?? widget.profile.effectiveTimezone,
  );
  late FtpSource _ftpSource = widget.profile.ftpSource ?? FtpSource.manual;
  late HrZoneModel _hrModel = widget.profile.hrZoneModel;

  @override
  void dispose() {
    _ftp.dispose();
    _maxHr.dispose();
    _restingHr.dispose();
    _timezone.dispose();
    super.dispose();
  }

  void _submit() {
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    // The box is prefilled with the current FTP, so an untouched save would
    // otherwise append a duplicate record every time. Only a genuine change
    // becomes a new record. An empty box means "leave the existing value
    // alone" — never a 0 and never a silent reset.
    final enteredFtp = double.tryParse(_ftp.text.trim());
    final currentFtp = widget.profile.ftpW;
    final ftpChanged =
        enteredFtp != null &&
        (currentFtp == null || enteredFtp != currentFtp.roundToDouble());
    widget.onSave(
      _SettingsValues(
        ftpW: ftpChanged ? enteredFtp : null,
        ftpSource: ftpChanged ? _ftpSource : null,
        maxHrBpm: _changedOrNull(_maxHr, widget.profile.maxHrBpm),
        restingHrBpm: _changedOrNull(_restingHr, widget.profile.restingHrBpm),
        hrZoneModel: _hrModel == widget.profile.hrZoneModel ? null : _hrModel,
        timezone:
            _timezone.text.trim() !=
                (widget.profile.timezone ?? widget.profile.effectiveTimezone)
            ? _timezone.text.trim()
            : null,
      ),
    );
  }

  /// Returns the parsed value only when it differs from what is already stored,
  /// so an unchanged field is not written on every save.
  static double? _changedOrNull(TextEditingController c, double? stored) {
    final entered = double.tryParse(c.text.trim());
    if (entered == null) {
      return null;
    }
    if (stored != null && entered == stored.roundToDouble()) {
      return null;
    }
    return entered;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Form(
      key: _formKey,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          DropdownButtonFormField<FtpSource>(
            key: const Key('training.ftpSource'),
            initialValue: _ftpSource,
            decoration: InputDecoration(
              labelText: l10n.get('training.ftpSource.manual'),
            ),
            items: [
              for (final source in const [FtpSource.manual, FtpSource.imported])
                DropdownMenuItem(
                  value: source,
                  child: Text(l10n.get(source.l10nKey)),
                ),
            ],
            onChanged: (v) =>
                setState(() => _ftpSource = v ?? FtpSource.manual),
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: const Key('training.ftpInput'),
            controller: _ftp,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: l10n.get('training.ftp'),
              helperText: l10n.get('training.ftpHint'),
              suffixText: 'W',
            ),
            validator: (v) {
              final text = v?.trim() ?? '';
              if (text.isEmpty) {
                return null;
              }
              final value = double.tryParse(text);
              if (value == null) {
                return l10n.get('training.unavailable');
              }
              // Same plausible range the API enforces, checked before the round
              // trip so the rider gets an immediate answer.
              if (value < 50 || value > 1500) {
                return l10n.get('training.unavailable');
              }
              return null;
            },
          ),
          const SizedBox(height: 16),
          TextFormField(
            key: const Key('training.maxHrInput'),
            controller: _maxHr,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: l10n.get('training.maxHr'),
              suffixText: 'bpm',
            ),
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: const Key('training.restingHrInput'),
            controller: _restingHr,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: l10n.get('training.restingHr'),
              suffixText: 'bpm',
            ),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<HrZoneModel>(
            key: const Key('training.hrModel'),
            initialValue: _hrModel,
            decoration: InputDecoration(
              labelText: l10n.get('training.hrZoneModel'),
            ),
            items: [
              for (final model in HrZoneModel.values)
                DropdownMenuItem(
                  value: model,
                  child: Text(
                    l10n.get('training.hrZoneModel.${model.wire ?? 'auto'}'),
                  ),
                ),
            ],
            onChanged: (v) => setState(() => _hrModel = v ?? HrZoneModel.auto),
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: const Key('training.timezoneInput'),
            controller: _timezone,
            decoration: InputDecoration(
              labelText: l10n.get('training.timezone'),
              helperText: l10n.get('training.effectiveTimezone'),
            ),
          ),
          const SizedBox(height: 20),
          FilledButton(
            key: const Key('training.save'),
            onPressed: widget.saving ? null : _submit,
            child: Text(l10n.get('training.save')),
          ),
        ],
      ),
    );
  }
}

class _SettingsValues {
  /// A null field means "unchanged", so the repository omits it entirely.
  final double? ftpW;
  final FtpSource? ftpSource;
  final double? maxHrBpm;
  final double? restingHrBpm;
  final HrZoneModel? hrZoneModel;
  final String? timezone;

  const _SettingsValues({
    required this.ftpW,
    required this.ftpSource,
    required this.maxHrBpm,
    required this.restingHrBpm,
    required this.hrZoneModel,
    required this.timezone,
  });
}

/// The published calculation registry, so a number can be traced to its
/// formula rather than trusted blindly (§31).
class CalculationVersionsPage extends ConsumerWidget {
  const CalculationVersionsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = AppLocalizations.of(context);
    final versions = ref.watch(calculationVersionsProvider);
    return Scaffold(
      appBar: AppBar(title: Text(l10n.get('training.calculationVersions'))),
      body: versions.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(l10n.get('training.error.load'))),
        data: (items) => ListView.builder(
          itemCount: items.length,
          itemBuilder: (context, i) {
            final v = items[i];
            return ListTile(
              key: Key('training.version.${v.version}'),
              title: Text(v.title),
              subtitle: Text('${v.version} — ${v.summary}'),
              trailing: Chip(
                visualDensity: VisualDensity.compact,
                label: Text(
                  l10n.get(v.isActive ? 'training.active' : 'training.retired'),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}
