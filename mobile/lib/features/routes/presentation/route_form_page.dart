import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:latlong2/latlong.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../auth/presentation/login_page.dart';
import '../domain/route.dart';
import '../domain/route_validators.dart';
import 'route_list_page.dart';
import 'route_map.dart';
import 'routes_providers.dart';

/// Create / edit a route. Geometry edits always produce a NEW version
/// server-side (expected_version is sent → 409 on conflict, §11).
class RouteFormPage extends ConsumerStatefulWidget {
  final String? routeId; // null = create
  const RouteFormPage({super.key, this.routeId});
  @override
  ConsumerState<RouteFormPage> createState() => _RouteFormPageState();
}

class _RouteFormPageState extends ConsumerState<RouteFormPage> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _description = TextEditingController();
  String _activity = 'road';
  String _privacy = 'private';
  final List<RoutePointInput> _points = [];
  AppRoute? _existing;
  int? _expectedVersion;
  bool _loading = false;
  bool _saving = false;
  String? _errorKey;

  bool get _isEdit => widget.routeId != null;

  @override
  void initState() {
    super.initState();
    if (_isEdit) {
      _loading = true;
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final record = await ref
          .read(routesRepositoryProvider)
          .detail(widget.routeId!, includeGeometry: true);
      if (!mounted) return;
      setState(() {
        _existing = record.route;
        _expectedVersion = record.route.currentVersion;
        _name.text = record.route.name;
        _description.text = record.route.description ?? '';
        _activity = record.route.activityType;
        _privacy = record.route.privacy;
        _points
          ..clear()
          ..addAll(record.geometry?.points ?? const <RoutePointInput>[]);
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _errorKey = 'routes.notFound';
      });
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return AuthScaffold(
      titleKey: _isEdit ? 'routes.edit' : 'routes.new',
      showBrand: false,
      children: [
        if (_loading) ...[
          const Center(child: CircularProgressIndicator()),
        ] else ...[
          Form(
            key: _form,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  controller: _name,
                  maxLength: 120,
                  decoration: InputDecoration(labelText: t.get('routes.name')),
                  validator: (v) {
                    final key = RouteValidators.validateName(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                TextFormField(
                  controller: _description,
                  maxLines: 3,
                  maxLength: 2000,
                  decoration: InputDecoration(
                    labelText: t.get('routes.description'),
                  ),
                  validator: (v) {
                    final key = RouteValidators.validateDescription(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                ),
                const SizedBox(height: 8),
                DropdownButtonFormField<String>(
                  initialValue: _activity,
                  decoration: InputDecoration(
                    labelText: t.get('routes.activity'),
                  ),
                  items: [
                    for (final a in RouteValidators.activities)
                      DropdownMenuItem(
                        value: a,
                        child: Text(t.get('routes.activity.$a')),
                      ),
                  ],
                  onChanged: (v) => setState(() => _activity = v ?? 'road'),
                ),
                const SizedBox(height: 8),
                DropdownButtonFormField<String>(
                  initialValue: _privacy,
                  decoration: InputDecoration(
                    labelText: t.get('routes.privacy'),
                  ),
                  items: [
                    for (final p in RouteValidators.privacies)
                      DropdownMenuItem(
                        value: p,
                        child: Text(t.get('routes.privacy.$p')),
                      ),
                  ],
                  onChanged: (v) => setState(() => _privacy = v ?? 'private'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: Text(
                  '${t.get('routes.points')}: ${_points.length}',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ),
              TextButton.icon(
                onPressed: _saving ? null : _importGpx,
                icon: const Icon(Icons.upload_file_rounded, size: 18),
                label: Text(t.get('routes.import')),
              ),
            ],
          ),
          Text(t.get('routes.tapToAdd')),
          const SizedBox(height: 8),
          RouteMapView(
            points: _points,
            height: 300,
            onTap: (LatLng latlng) => setState(
              () => _points.add(
                RoutePointInput(lat: latlng.latitude, lon: latlng.longitude),
              ),
            ),
            onLongPress: (_) {
              if (_points.isNotEmpty) setState(() => _points.removeLast());
            },
          ),
          if (_errorKey != null) ...[
            const SizedBox(height: 12),
            Text(t.get(_errorKey!)),
          ],
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _saving ? null : _save,
            child: Text(t.get(_isEdit ? 'routes.save' : 'routes.create')),
          ),
          const SizedBox(height: 8),
          TextButton(
            onPressed: _saving ? null : () => context.go('/routes'),
            child: Text(t.get('routes.cancel')),
          ),
        ],
      ],
    );
  }

  Future<void> _save() async {
    final t = context.l10n;
    final error = RouteValidators.form(
      name: _name.text,
      description: _description.text,
      activity: _activity,
      privacy: _privacy,
      points: _points,
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
      final notifier = ref.read(routeListProvider.notifier);
      final AppRoute route;
      if (_isEdit) {
        route = await notifier.updateRoute(
          widget.routeId!,
          expectedVersion: _expectedVersion ?? _existing!.currentVersion,
          name: _name.text.trim(),
          description: _description.text.trim(),
          activityType: _activity,
          privacy: _privacy,
          points: _points,
        );
      } else {
        route = await notifier.create(
          name: _name.text.trim(),
          description: _description.text.trim().isEmpty
              ? null
              : _description.text.trim(),
          activityType: _activity,
          privacy: _privacy,
          points: _points,
        );
      }
      if (!mounted) return;
      context.go('/routes/${route.id}');
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _errorKey = null);
      _snack(friendlyRouteError(t, e));
      if (e.code == 'VERSION_CONFLICT') _refreshExpectedVersion();
    } catch (_) {
      if (mounted) setState(() => _errorKey = 'routes.genericError');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _refreshExpectedVersion() async {
    try {
      final record = await ref
          .read(routesRepositoryProvider)
          .detail(widget.routeId!);
      if (mounted) {
        setState(() => _expectedVersion = record.route.currentVersion);
      }
    } catch (_) {
      // keep the previous value; user can reopen the screen.
    }
  }

  Future<void> _importGpx() async {
    final t = context.l10n;
    final picked = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['gpx'],
    );
    if (picked.isEmpty) return;
    final file = picked.first;
    final Uint8List bytes;
    try {
      bytes = await file.readAsBytes();
    } on Exception {
      if (mounted) _snack(t.get('routes.genericError'));
      return;
    }
    if (!mounted) return;
    setState(() => _saving = true);
    try {
      final result = await ref
          .read(routeListProvider.notifier)
          .importGpx(
            bytes: bytes,
            filename: file.name,
            name: _name.text.trim().isEmpty ? null : _name.text.trim(),
            activityType: _activity,
            privacy: _privacy,
          );
      if (!mounted) return;
      _snack(t.get('routes.imported'));
      context.go('/routes/${result.route.id}');
    } on ApiException catch (e) {
      if (mounted) _snack(friendlyRouteError(t, e));
    } catch (_) {
      if (mounted) _snack(t.get('routes.genericError'));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }
}
