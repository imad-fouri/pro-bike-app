import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import 'bikes_providers.dart';

/// Bike details: identity, specs, status. Space reserved for future mileage,
/// maintenance, sensors, components — values never faked.
class BikeDetailPage extends ConsumerWidget {
  final String bikeId;
  const BikeDetailPage({super.key, required this.bikeId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final detail = ref.watch(bikeDetailProvider(bikeId));
    return Scaffold(
      appBar: AppBar(
        title: Text(t.get('bikes.detail')),
        actions: [
          IconButton(
            icon: const Icon(Icons.edit),
            onPressed: () => context.go('/bikes/$bikeId/edit'),
          ),
        ],
      ),
      body: detail.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(_friendly(t, e))),
        data: (bike) {
          return ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Text(bike.name, style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 8),
              Text(
                '${t.get('bikes.category.${bike.category}')} • ${bike.subtitle()}',
              ),
              const SizedBox(height: 16),
              if (bike.brand != null)
                _Row(label: t.get('bikes.brand'), value: bike.brand!),
              if (bike.modelYear != null)
                _Row(
                  label: t.get('bikes.modelYear'),
                  value: '${bike.modelYear}',
                ),
              if (bike.frameSize != null)
                _Row(label: t.get('bikes.frameSize'), value: bike.frameSize!),
              if (bike.weightKg != null)
                _Row(
                  label: t.get('bikes.weight'),
                  value: bike.displayWeight(imperial: false),
                ),
              _Row(
                label: t.get('bikes.status'),
                value: bike.isArchived
                    ? t.get('bikes.archived')
                    : t.get('bikes.active'),
              ),
              if (bike.notes != null)
                _Row(label: t.get('bikes.notes'), value: bike.notes!),
              const SizedBox(height: 24),
              if (bike.isArchived)
                FilledButton(
                  onPressed: () async {
                    await ref.read(bikesRepositoryProvider).restore(bike.id);
                    ref.invalidate(bikeDetailProvider(bike.id));
                    ref.read(bikeListProvider.notifier).refresh();
                  },
                  child: Text(t.get('bikes.restore')),
                )
              else
                OutlinedButton(
                  onPressed: () => _confirmArchive(context, ref, bike.id),
                  child: Text(t.get('bikes.archive')),
                ),
              const SizedBox(height: 8),
              TextButton(
                onPressed: () => _confirmDelete(context, ref, bike.id),
                child: Text(t.get('bikes.delete')),
              ),
            ],
          );
        },
      ),
    );
  }

  static String _friendly(AppLocalizations t, Object e) {
    if (e is ApiException && e.status == 404) return t.get('bikes.notFound');
    if (e is ApiException && e.status == 0) return t.get('bikes.networkError');
    return t.get('bikes.genericError');
  }

  Future<void> _confirmArchive(
    BuildContext context,
    WidgetRef ref,
    String id,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(t.get('bikes.archiveTitle')),
        content: Text(t.get('bikes.archiveBody')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: Text(t.get('bikes.cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(t.get('bikes.archive')),
          ),
        ],
      ),
    );
    if (ok == true && context.mounted) {
      await ref.read(bikesRepositoryProvider).archive(id);
      ref.invalidate(bikeDetailProvider(id));
      ref.read(bikeListProvider.notifier).refresh();
      if (context.mounted) context.go('/bikes');
    }
  }

  Future<void> _confirmDelete(
    BuildContext context,
    WidgetRef ref,
    String id,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(t.get('bikes.deleteTitle')),
        content: Text(t.get('bikes.deleteBody')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: Text(t.get('bikes.cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(t.get('bikes.delete')),
          ),
        ],
      ),
    );
    if (ok == true && context.mounted) {
      await ref.read(bikesRepositoryProvider).remove(id);
      ref.read(bikeListProvider.notifier).refresh();
      if (context.mounted) context.go('/bikes');
    }
  }
}

class _Row extends StatelessWidget {
  final String label;
  final String value;
  const _Row({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(child: Text(label)),
          Expanded(child: Text(value, textAlign: TextAlign.end)),
        ],
      ),
    );
  }
}
