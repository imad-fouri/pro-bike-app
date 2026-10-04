import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../domain/route.dart';
import 'routes_providers.dart';

/// Route list: loading / empty / error / data + GPX import + filter.
class RouteListPage extends ConsumerStatefulWidget {
  const RouteListPage({super.key});
  @override
  ConsumerState<RouteListPage> createState() => _RouteListPageState();
}

class _RouteListPageState extends ConsumerState<RouteListPage> {
  String? _activity;
  bool _importing = false;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final list = ref.watch(routeListProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('routes.title')),
        actions: [
          IconButton(
            tooltip: t.get('routes.import'),
            onPressed: _importing ? null : () => _importGpx(context),
            icon: const Icon(Icons.upload_file_rounded),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => context.go('/routes/new'),
        child: const Icon(Icons.add),
      ),
      body: list.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _ErrorState(
          message: _friendly(t, e),
          onRetry: () => ref.read(routeListProvider.notifier).refresh(),
        ),
        data: (routes) {
          final filtered = _activity == null
              ? routes
              : routes.where((r) => r.activityType == _activity).toList();
          if (filtered.isEmpty) {
            return _EmptyState(
              message: t.get('routes.empty'),
              actionLabel: t.get('routes.create'),
              onAction: () => context.go('/routes/new'),
              secondaryLabel: t.get('routes.import'),
              onSecondary: () => _importGpx(context),
            );
          }
          return RefreshIndicator(
            onRefresh: () => ref.read(routeListProvider.notifier).refresh(),
            child: ListView(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  child: DropdownButtonFormField<String?>(
                    initialValue: _activity,
                    decoration: InputDecoration(
                      labelText: t.get('routes.activity'),
                    ),
                    items: [
                      DropdownMenuItem<String?>(
                        value: null,
                        child: Text(t.get('routes.title')),
                      ),
                      for (final a in _activities)
                        DropdownMenuItem<String?>(
                          value: a,
                          child: Text(t.get('routes.activity.$a')),
                        ),
                    ],
                    onChanged: (v) => setState(() => _activity = v),
                  ),
                ),
                for (final route in filtered)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                    child: _RouteCard(route: route),
                  ),
                const SizedBox(height: 96),
              ],
            ),
          );
        },
      ),
    );
  }

  Future<void> _importGpx(BuildContext context) async {
    final t = context.l10n;
    final picked = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['gpx'],
    );
    if (picked.isEmpty) return;
    final file = picked.first;
    if (!context.mounted) return;
    setState(() => _importing = true);
    final Uint8List bytes;
    try {
      bytes = await file.readAsBytes();
    } on Exception {
      if (context.mounted) _snack(context, t.get('routes.genericError'));
      if (mounted) setState(() => _importing = false);
      return;
    }
    try {
      final result = await ref
          .read(routeListProvider.notifier)
          .importGpx(bytes: bytes, filename: file.name);
      if (!context.mounted) return;
      _snack(context, '${t.get('routes.imported')}: ${result.route.name}');
      context.go('/routes/${result.route.id}');
    } on ApiException catch (e) {
      if (context.mounted) _snack(context, friendlyRouteError(t, e));
    } catch (_) {
      if (context.mounted) _snack(context, t.get('routes.genericError'));
    } finally {
      if (mounted) setState(() => _importing = false);
    }
  }

  void _snack(BuildContext context, String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  static String _friendly(AppLocalizations t, Object e) =>
      friendlyRouteError(t, e);
}

/// Shared API error → message mapping for every route screen.
String friendlyRouteError(AppLocalizations t, Object e) {
  if (e is ApiException) {
    switch (e.code) {
      case 'ROUTE_NOT_FOUND':
      case 'ROUTE_VERSION_NOT_FOUND':
        return t.get('routes.notFound');
      case 'VERSION_CONFLICT':
        return t.get('routes.conflict');
      case 'PRIVACY_PUBLIC_DISABLED':
        return t.get('routes.privacyDisabled');
      case 'NETWORK_TIMEOUT':
      case 'NETWORK_ERROR':
        return t.get('routes.networkError');
      default:
        return '${t.get('routes.genericError')} (${e.code})';
    }
  }
  return t.get('routes.genericError');
}

const _activities = [
  'road',
  'gravel',
  'mountain_bike',
  'touring',
  'bikepacking',
  'commuting',
  'e_bike',
  'other',
];

class _RouteCard extends StatelessWidget {
  final AppRoute route;
  const _RouteCard({required this.route});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final imperial = false; // unit preference arrives with settings (§43)
    return Card(
      child: ListTile(
        leading: const Icon(Icons.route_rounded),
        title: Text(route.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          [
            t.get(route.activityKey),
            route.distanceLabel(imperial: imperial),
            route.gainLabel(imperial: imperial),
            if (route.estimatedDurationS != null) route.durationLabel(),
          ].join(' • '),
        ),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text('v${route.currentVersion}'),
            if (route.isArchived)
              Text(
                t.get('routes.archive'),
                style: const TextStyle(fontSize: 11),
              ),
          ],
        ),
        onTap: () => context.go('/routes/${route.id}'),
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  final String message;
  final String actionLabel;
  final VoidCallback onAction;
  final String secondaryLabel;
  final VoidCallback onSecondary;
  const _EmptyState({
    required this.message,
    required this.actionLabel,
    required this.onAction,
    required this.secondaryLabel,
    required this.onSecondary,
  });

  @override
  Widget build(BuildContext context) => Center(
    child: Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        const Icon(Icons.route_rounded, size: 64),
        const SizedBox(height: 16),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(message, textAlign: TextAlign.center),
        ),
        const SizedBox(height: 16),
        FilledButton(onPressed: onAction, child: Text(actionLabel)),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: onSecondary,
          icon: const Icon(Icons.upload_file_rounded),
          label: Text(secondaryLabel),
        ),
      ],
    ),
  );
}

class _ErrorState extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorState({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) => Center(
    child: Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Text(message),
        const SizedBox(height: 16),
        OutlinedButton(
          onPressed: onRetry,
          child: Text(context.l10n.get('routes.retry')),
        ),
      ],
    ),
  );
}
