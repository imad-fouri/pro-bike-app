import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../routes/domain/route.dart';
import '../../routes/presentation/routes_providers.dart';
import '../domain/ride_validators.dart';
import 'group_ride_providers.dart';
import 'group_ride_widgets.dart';

/// Create a group ride.
///
/// Deliberately a CREATE-ONLY screen: there is no edit. A ride's route pin is
/// immutable and its roster is only ever changed through its lifecycle, so an
/// edit form would have to refuse most of its own fields — and a form that mostly
/// refuses is worse than no form (ADR-16 §3, §4).
///
/// The route pin takes an EXACT version, chosen from the rider's own routes.
/// There is no "latest" default: the whole point of pinning is that the geometry
/// cannot move under them later.
class GroupRideFormPage extends ConsumerStatefulWidget {
  const GroupRideFormPage({super.key});

  @override
  ConsumerState<GroupRideFormPage> createState() => _GroupRideFormPageState();
}

class _GroupRideFormPageState extends ConsumerState<GroupRideFormPage> {
  final _title = TextEditingController();
  final _description = TextEditingController();
  final _meetingPoint = TextEditingController();

  /// The pinned version, as the picker holds it.
  ///
  /// Null means "no route", which is a legitimate choice — a ride can be a
  /// meeting time and nothing else.
  String? _routeId;
  int? _routeVersion;
  DateTime? _when;
  String? _errorKey;
  bool _saving = false;

  @override
  void dispose() {
    _title.dispose();
    _description.dispose();
    _meetingPoint.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Scaffold(
      appBar: AppBar(title: Text(t.get('groupRide.createRide'))),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          TextField(
            key: const Key('groupRide.form.title'),
            controller: _title,
            maxLength: RideValidators.maxTitle,
            decoration: InputDecoration(
              labelText: t.get('groupRide.title'),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          TextField(
            key: const Key('groupRide.form.description'),
            controller: _description,
            maxLines: 3,
            maxLength: RideValidators.maxDescription,
            decoration: InputDecoration(
              labelText: t.get('groupRide.description'),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          TextField(
            key: const Key('groupRide.form.meetingPoint'),
            controller: _meetingPoint,
            maxLength: RideValidators.maxMeetingPoint,
            decoration: InputDecoration(
              labelText: t.get('groupRide.meetingPoint'),
              helperText: t.get('groupRide.meetingPointHint'),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Text(
            t.get('groupRide.when'),
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          Text(
            t.get('groupRide.startsAtHint'),
            style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
          ),
          const SizedBox(height: AppSpacing.sm),
          _WhenPicker(
            onPicked: (when) => setState(() => _when = when),
            value: _when,
          ),
          const SizedBox(height: AppSpacing.md),
          _RoutePinPicker(
            routes: ref.watch(routeListProvider),
            routeId: _routeId,
            routeVersion: _routeVersion,
            onPicked: (id, version) => setState(() {
              _routeId = id;
              _routeVersion = version;
            }),
            onClear: () => setState(() {
              _routeId = null;
              _routeVersion = null;
            }),
          ),
          if (_errorKey != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              t.get(_errorKey!),
              key: const Key('groupRide.form.error'),
              style: const TextStyle(color: AppColors.zone5),
            ),
          ],
          const SizedBox(height: AppSpacing.lg),
          FilledButton(
            key: const Key('groupRide.form.submit'),
            onPressed: _saving ? null : _save,
            child: Text(t.get('groupRide.create')),
          ),
          TextButton(
            onPressed: _saving ? null : () => context.pop(),
            child: Text(t.get('routes.cancel')),
          ),
        ],
      ),
    );
  }

  Future<void> _save() async {
    final t = context.l10n;
    final error = RideValidators.form(
      title: _title.text,
      description: _description.text,
      meetingPoint: _meetingPoint.text,
      routeId: _routeId,
      routeVersion: _routeVersion,
    );
    if (error != null) {
      setState(() => _errorKey = error);
      return;
    }
    setState(() {
      _saving = true;
      _errorKey = null;
    });
    try {
      final ride = await ref
          .read(groupRideActionsProvider.notifier)
          .create(
            title: _title.text.trim(),
            description: _emptyToNull(_description.text),
            meetingPoint: _emptyToNull(_meetingPoint.text),
            startsAt: _when,
            routeId: _routeId,
            routeVersion: _routeVersion,
          );
      if (!mounted) return;
      // Straight to the ride, not back to the list: the organizer has just made
      // the thing they came here to make, and the next action is inviting people.
      context.go('/group-rides/${ride.id}');
    } on Object catch (e) {
      if (!mounted) return;
      if (e is ApiException) {
        _snack(friendlyRideError(t, e));
      } else {
        setState(() => _errorKey = 'social.error.generic');
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  static String? _emptyToNull(String v) {
    final trimmed = v.trim();
    return trimmed.isEmpty ? null : trimmed;
  }

  void _snack(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }
}

/// When the ride is scheduled. Optional.
///
/// `starts_at` is SCHEDULED INFORMATION, not a gate: it never blocks joining or
/// starting, and the create form says so rather than implying the app will hold
/// the ride for you (ADR-16 §3).
class _WhenPicker extends StatelessWidget {
  final DateTime? value;
  final ValueChanged<DateTime?> onPicked;

  const _WhenPicker({required this.value, required this.onPicked});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            key: const Key('groupRide.form.pickWhen'),
            icon: const Icon(Icons.event_rounded),
            label: Text(
              value == null
                  ? t.get('groupRide.noDate')
                  : '${value!.year}-${_two(value!.month)}-${_two(value!.day)} '
                        '${_two(value!.hour)}:${_two(value!.minute)}',
            ),
            onPressed: () async {
              final now = DateTime.now();
              final picked = await showDatePicker(
                context: context,
                initialDate: value ?? now,
                firstDate: DateTime(now.year - 1),
                lastDate: DateTime(now.year + 2),
              );
              if (picked == null || !context.mounted) return;
              final time = await showTimePicker(
                context: context,
                initialTime: TimeOfDay.fromDateTime(value ?? now),
              );
              onPicked(
                DateTime(
                  picked.year,
                  picked.month,
                  picked.day,
                  time?.hour ?? 9,
                  time?.minute ?? 0,
                ).toUtc(),
              );
            },
          ),
        ),
        if (value != null)
          IconButton(
            key: const Key('groupRide.form.clearWhen'),
            tooltip: t.get('routes.cancel'),
            icon: const Icon(Icons.clear_rounded),
            onPressed: () => onPicked(null),
          ),
      ],
    );
  }

  /// Zero-padded so the string sorts and reads the same in every locale.
  ///
  /// Hand-built on purpose: `MaterialLocalizations.of(context).formatMediumDate`
  /// would be locale-correct, but this label is also the payload's source of
  /// truth for a scheduled ride and a stable ISO-like shape is easier to verify.
  static String _two(int n) => n.toString().padLeft(2, '0');
}

/// Pick the IMMUTABLE route version this ride will be pinned to.
class _RoutePinPicker extends StatelessWidget {
  final AsyncValue<List<AppRoute>> routes;
  final String? routeId;
  final int? routeVersion;
  final void Function(String id, int version) onPicked;
  final VoidCallback onClear;

  const _RoutePinPicker({
    required this.routes,
    required this.routeId,
    required this.routeVersion,
    required this.onPicked,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          t.get('groupRide.route'),
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        Text(
          t.get('groupRide.routePinHint'),
          style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
        ),
        const SizedBox(height: AppSpacing.sm),
        routes.when(
          loading: () => const SizedBox.shrink(),
          error: (_, _) => Text(
            t.get('groupRide.routesUnavailable'),
            style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
          ),
          data: (items) {
            if (items.isEmpty) {
              return Text(
                t.get('groupRide.noRoutesToPin'),
                key: const Key('groupRide.form.noRoutes'),
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textMuted,
                ),
              );
            }
            // RadioGroup owns the selection so all options render as one control
            // set. The value is a (route, version) PAIR, not a bare version: two
            // different routes can both be at version 3, so matching on the number
            // alone would light up — and then pin — the wrong route.
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                RadioGroup<_PinChoice>(
                  groupValue: routeId == null || routeVersion == null
                      ? null
                      : _PinChoice(routeId!, routeVersion!),
                  onChanged: (v) {
                    if (v != null) {
                      onPicked(v.routeId, v.version);
                    }
                  },
                  child: Column(
                    children: [
                      for (final r in items)
                        RadioListTile<_PinChoice>(
                          key: Key(
                            'groupRide.form.pin.${r.id}.${r.currentVersion}',
                          ),
                          value: _PinChoice(r.id, r.currentVersion),
                          dense: true,
                          title: Text(r.name),
                          subtitle: Text(
                            t.getWith('groupRide.versionLabel', {
                              'n': '${r.currentVersion}',
                            }),
                            style: const TextStyle(
                              fontSize: 11,
                              color: AppColors.textMuted,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
                if (routeId != null)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton(
                      key: const Key('groupRide.form.clearRoute'),
                      onPressed: onClear,
                      child: Text(t.get('groupRide.noRoute')),
                    ),
                  ),
              ],
            );
          },
        ),
      ],
    );
  }
}

/// One "pin this exact version" option.
///
/// A value object rather than a bare int because the selected value must identify
/// the route as well as the version: two routes can both be at version 3, and
/// `RadioGroup` compares values with `==` to decide which tile is lit.
@immutable
class _PinChoice {
  final String routeId;
  final int version;

  const _PinChoice(this.routeId, this.version);

  @override
  bool operator ==(Object other) =>
      other is _PinChoice &&
      other.routeId == routeId &&
      other.version == version;

  @override
  int get hashCode => Object.hash(routeId, version);
}
