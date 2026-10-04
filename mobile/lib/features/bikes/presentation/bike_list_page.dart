import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import 'bikes_providers.dart';

/// Professional bike list: loading / empty / error / data states.
class BikeListPage extends ConsumerWidget {
  const BikeListPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final list = ref.watch(bikeListProvider);
    return Scaffold(
      appBar: AppBar(title: Text(t.get('bikes.title'))),
      floatingActionButton: FloatingActionButton(
        onPressed: () => context.go('/bikes/new'),
        child: const Icon(Icons.add),
      ),
      body: list.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _ErrorState(
          message: _friendly(t, e),
          onRetry: () {
            ref.read(bikeListProvider.notifier).refresh();
          },
        ),
        data: (bikes) {
          if (bikes.isEmpty) {
            return _EmptyState(
              message: t.get('bikes.empty'),
              actionLabel: t.get('bikes.add'),
              onAction: () => context.go('/bikes/new'),
            );
          }
          return RefreshIndicator(
            onRefresh: () => ref.read(bikeListProvider.notifier).refresh(),
            child: ListView.builder(
              itemCount: bikes.length,
              itemBuilder: (context, i) {
                final bike = bikes[i];
                return Card(
                  child: ListTile(
                    leading: const Icon(Icons.directions_bike),
                    title: Text(bike.name),
                    subtitle: Text(
                      '${t.get('bikes.category.${bike.category}')} • ${bike.subtitle()}',
                    ),
                    trailing: bike.isArchived
                        ? Text(t.get('bikes.archived'))
                        : (bike.weightKg != null
                              ? Text(bike.displayWeight(imperial: false))
                              : null),
                    onTap: () => context.go('/bikes/${bike.id}'),
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }

  static String _friendly(AppLocalizations t, Object e) {
    if (e is ApiException) {
      switch (e.status) {
        case 401:
          return t.get('auth.login');
        case 0:
          return t.get('bikes.networkError');
        default:
          return '${t.get('bikes.genericError')} (${e.code})';
      }
    }
    return t.get('bikes.genericError');
  }
}

class _EmptyState extends StatelessWidget {
  final String message;
  final String actionLabel;
  final VoidCallback onAction;
  const _EmptyState({
    required this.message,
    required this.actionLabel,
    required this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.directions_bike, size: 64),
          const SizedBox(height: 16),
          Text(message),
          const SizedBox(height: 16),
          FilledButton(onPressed: onAction, child: Text(actionLabel)),
        ],
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorState({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(message),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: onRetry,
            child: Text(context.l10n.get('bikes.retry')),
          ),
        ],
      ),
    );
  }
}
