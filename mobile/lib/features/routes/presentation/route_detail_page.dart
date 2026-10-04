import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../ride/presentation/ride_providers.dart';
import '../data/gpx_export.dart';
import '../domain/route.dart';
import 'route_list_page.dart';
import 'route_map.dart';
import 'routes_providers.dart';

/// Route detail: map, derived metrics, version history, export, offline copy.
class RouteDetailPage extends ConsumerStatefulWidget {
  final String routeId;
  const RouteDetailPage({super.key, required this.routeId});
  @override
  ConsumerState<RouteDetailPage> createState() => _RouteDetailPageState();
}

class _RouteDetailPageState extends ConsumerState<RouteDetailPage> {
  int? _viewVersion; // null = current version

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final detail = ref.watch(routeDetailProvider(widget.routeId));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('routes.detail'))),
      body: detail.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _OfflineOrError(
          routeId: widget.routeId,
          error: e,
          onRetry: () => ref.invalidate(routeDetailProvider(widget.routeId)),
        ),
        data: (record) {
          final route = record.route;
          final geometry = _shownGeometry(record.geometry);
          return _RouteBody(
            route: route,
            geometry: geometry,
            viewVersion: _viewVersion,
            onViewVersion: (v) => setState(() => _viewVersion = v),
            onShowCurrent: () => setState(() => _viewVersion = null),
          );
        },
      ),
    );
  }

  RouteGeometry? _shownGeometry(RouteGeometry? current) {
    if (_viewVersion == null) return current;
    final key = '${widget.routeId}#$_viewVersion';
    return ref
        .watch(routeGeometryProvider(key))
        .maybeWhen(data: (g) => g, orElse: () => null);
  }
}

/// Falls back to the explicit offline download when the network is gone.
class _OfflineOrError extends ConsumerWidget {
  final String routeId;
  final Object error;
  final VoidCallback onRetry;
  const _OfflineOrError({
    required this.routeId,
    required this.error,
    required this.onRetry,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final offline = ref.watch(offlineRoutesProvider);
    return offline.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, _) => _errorView(t, error, onRetry),
      data: (routes) {
        final match = routes.where((r) => r.id == routeId).firstOrNull;
        if (match == null) return _errorView(t, error, onRetry);
        return _RouteBody(route: match, geometry: null, onViewVersion: (_) {});
      },
    );
  }

  static Widget _errorView(
    AppLocalizations t,
    Object error,
    VoidCallback onRetry,
  ) => Center(
    child: Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Text(friendlyRouteError(t, error)),
        const SizedBox(height: 16),
        OutlinedButton(onPressed: onRetry, child: Text(t.get('routes.retry'))),
      ],
    ),
  );
}

class _RouteBody extends ConsumerStatefulWidget {
  final AppRoute route;
  final RouteGeometry? geometry;
  final int? viewVersion;
  final void Function(int) onViewVersion;
  final VoidCallback? onShowCurrent;
  const _RouteBody({
    required this.route,
    required this.geometry,
    this.viewVersion,
    required this.onViewVersion,
    this.onShowCurrent,
  });

  @override
  ConsumerState<_RouteBody> createState() => _RouteBodyState();
}

class _RouteBodyState extends ConsumerState<_RouteBody> {
  bool _busy = false;

  AppRoute get _route => widget.route;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final imperial = false; // unit preference arrives with settings (§43)
    final geometry = widget.geometry;
    final cached = ref
        .watch(offlineRoutesProvider)
        .maybeWhen(
          data: (l) => l.any((r) => r.id == _route.id),
          orElse: () => false,
        );

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(_route.name, style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 4),
        Wrap(
          spacing: 8,
          children: [
            Chip(label: Text(t.get(_route.activityKey))),
            Chip(label: Text(t.get(_route.privacyKey))),
            if (widget.viewVersion != null)
              Chip(
                avatar: const Icon(Icons.history, size: 16),
                label: Text('${t.get('routes.version')} ${widget.viewVersion}'),
              )
            else
              Chip(label: Text('v${_route.currentVersion}')),
          ],
        ),
        const SizedBox(height: 12),
        RouteMapView(
          points: geometry?.points ?? const <RoutePointInput>[],
          height: 280,
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            _Metric(
              label: t.get('routes.distance'),
              value: _route.distanceLabel(imperial: imperial),
            ),
            _Metric(
              label: t.get('routes.climb'),
              value: _route.gainLabel(imperial: imperial),
            ),
            _Metric(
              label: t.get('routes.duration'),
              value: _route.durationLabel(),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            _Metric(
              label: t.get('routes.difficulty'),
              value: _route.difficulty == null
                  ? '—'
                  : t.get(_route.difficultyKey),
            ),
            _Metric(
              label: t.get('routes.points'),
              value: '${_route.pointCount}',
            ),
            _Metric(
              label: t.get('routes.version'),
              value: '${_route.currentVersion}',
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          t.get('routes.provenance'),
          style: Theme.of(context).textTheme.bodySmall,
        ),
        if (_route.description != null &&
            _route.description!.trim().isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(_route.description!),
        ],
        const SizedBox(height: 16),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: () => context.go('/routes/${_route.id}/edit'),
              icon: const Icon(Icons.edit_outlined),
              label: Text(t.get('routes.edit')),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _export(),
              icon: const Icon(Icons.ios_share_rounded),
              label: Text(t.get('routes.export')),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _toggleArchive(),
              icon: Icon(
                _route.isArchived
                    ? Icons.unarchive_outlined
                    : Icons.archive_outlined,
              ),
              label: Text(
                t.get(_route.isArchived ? 'routes.restore' : 'routes.archive'),
              ),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _download(cached),
              icon: Icon(
                cached ? Icons.check_circle_outline : Icons.download_rounded,
              ),
              label: Text(
                t.get(
                  cached ? 'routes.offlineSaved' : 'routes.offlineDownload',
                ),
              ),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _delete(),
              icon: const Icon(Icons.delete_outline),
              label: Text(t.get('routes.delete')),
            ),
          ],
        ),
        const SizedBox(height: 24),
        _VersionsSection(
          routeId: _route.id,
          currentVersion: _route.currentVersion,
          viewVersion: widget.viewVersion,
          onView: widget.onViewVersion,
          onCurrent: widget.onShowCurrent ?? () {},
        ),
        const SizedBox(height: 32),
      ],
    );
  }

  Future<void> _export() async {
    final t = context.l10n;
    setState(() => _busy = true);
    try {
      final res = await ref.read(routesRepositoryProvider).exportGpx(_route.id);
      final location = await saveGpx(res.filename, res.bytes);
      if (!mounted) return;
      _snack('${t.get('routes.export')}: $location');
    } on ApiException catch (e) {
      if (mounted) _snack(friendlyRouteError(t, e));
    } catch (_) {
      if (mounted) _snack(t.get('routes.genericError'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggleArchive() async {
    final t = context.l10n;
    setState(() => _busy = true);
    try {
      final notifier = ref.read(routeListProvider.notifier);
      if (_route.isArchived) {
        await notifier.restore(_route.id);
      } else {
        await notifier.archive(_route.id);
      }
      ref.invalidate(routeDetailProvider(_route.id));
    } on ApiException catch (e) {
      if (mounted) _snack(friendlyRouteError(t, e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _download(bool already) async {
    final t = context.l10n;
    setState(() => _busy = true);
    try {
      final db = await ref.read(rideDatabaseProvider.future);
      if (already) {
        await db.uncacheRoute(_route.id);
        _snack(t.get('routes.offlineRemove'));
      } else {
        final geometry = widget.geometry;
        await db.cacheRoute(
          id: _route.id,
          routeJson: jsonEncode(_route.toJson()),
          versionNo: _route.currentVersion,
          geometryJson: geometry == null ? null : jsonEncode(geometry.toJson()),
        );
        _snack(t.get('routes.savedOffline'));
      }
      ref.invalidate(offlineRoutesProvider);
    } catch (_) {
      if (mounted) _snack(t.get('routes.genericError'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    final t = context.l10n;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(t.get('routes.deleteTitle')),
        content: Text(t.get('routes.deleteBody')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(t.get('routes.cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(t.get('routes.delete')),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await ref.read(routeListProvider.notifier).remove(_route.id);
      if (mounted) context.go('/routes');
    } on ApiException catch (e) {
      if (mounted) _snack(friendlyRouteError(t, e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }
}

class _VersionsSection extends ConsumerWidget {
  final String routeId;
  final int currentVersion;
  final int? viewVersion;
  final void Function(int) onView;
  final VoidCallback onCurrent;
  const _VersionsSection({
    required this.routeId,
    required this.currentVersion,
    required this.viewVersion,
    required this.onView,
    required this.onCurrent,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final versions = ref.watch(routeVersionsProvider(routeId));
    return Card(
      child: ExpansionTile(
        title: Text(t.get('routes.versions')),
        subtitle: Text(t.get('routes.versionNote')),
        children: [
          if (viewVersion != null)
            ListTile(
              leading: const Icon(Icons.flag_rounded),
              title: Text(t.get('routes.showCurrent')),
              onTap: onCurrent,
            ),
          ...versions.when(
            loading: () => [const Center(child: CircularProgressIndicator())],
            error: (_, _) => [
              ListTile(
                title: Text(t.get('routes.genericError')),
                trailing: IconButton(
                  icon: const Icon(Icons.refresh),
                  onPressed: () =>
                      ref.invalidate(routeVersionsProvider(routeId)),
                ),
              ),
            ],
            data: (items) => [
              for (final v in items)
                ListTile(
                  leading: Text('v${v.versionNo}'),
                  title: Text(
                    [
                      '${v.pointCount} ${t.get('routes.points')}',
                      if (v.changelog != null && v.changelog!.isNotEmpty)
                        v.changelog!,
                    ].join(' • '),
                  ),
                  subtitle: Text(
                    [
                      v.estimatedDurationS == null
                          ? '—'
                          : Duration(
                              seconds: v.estimatedDurationS!,
                            ).inMinutes.toString(),
                      v.createdAt.toLocal().toString().split('.').first,
                    ].join(' • '),
                  ),
                  trailing: v.versionNo == currentVersion && viewVersion == null
                      ? const Icon(Icons.check_circle_outline)
                      : null,
                  onTap: () => onView(v.versionNo),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  final String label;
  final String value;
  const _Metric({required this.label, required this.value});

  @override
  Widget build(BuildContext context) => Expanded(
    child: Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 4),
            Text(value, style: Theme.of(context).textTheme.titleMedium),
          ],
        ),
      ),
    ),
  );
}
