import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../auth/presentation/login_page.dart';
import '../../bikes/presentation/bikes_providers.dart';
import '../../routes/presentation/routes_providers.dart';
import '../domain/location_source.dart';
import 'ride_providers.dart';

/// Ride start: bike selection + optional planned route + permission request
/// (never at app startup). The route reference is nullable by design (§49).
class RideStartPage extends ConsumerStatefulWidget {
  const RideStartPage({super.key});
  @override
  ConsumerState<RideStartPage> createState() => _RideStartPageState();
}

class _RideStartPageState extends ConsumerState<RideStartPage> {
  String? _bikeId;
  String? _routeId;
  String? _errorKey;
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final bikes = ref.watch(bikeListProvider);
    return AuthScaffold(
      titleKey: 'ride.start',
      children: [
        bikes.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (_, _) => Text(t.get('bikes.genericError')),
          data: (list) {
            final active = list.where((b) => !b.isArchived).toList();
            if (active.isEmpty) {
              return TextButton(
                onPressed: () => context.go('/bikes/new'),
                child: Text(t.get('ride.needBike')),
              );
            }
            _bikeId ??= active.first.id;
            return DropdownButtonFormField<String>(
              initialValue: _bikeId,
              decoration: InputDecoration(labelText: t.get('ride.bike')),
              items: [
                for (final b in active)
                  DropdownMenuItem(value: b.id, child: Text(b.name)),
              ],
              onChanged: (v) => setState(() => _bikeId = v),
            );
          },
        ),
        const SizedBox(height: 16),
        _routePicker(t),
        const SizedBox(height: 16),
        if (_errorKey != null) Text(t.get(_errorKey!)),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: _busy ? null : _start,
          child: Text(t.get('ride.startRide')),
        ),
      ],
    );
  }

  /// Optional planned route; "no route" keeps the ride reference null.
  Widget _routePicker(AppLocalizations t) {
    final routes = ref.watch(routeListProvider);
    return routes.when(
      loading: () => const LinearProgressIndicator(),
      error: (_, _) => const SizedBox.shrink(),
      data: (list) {
        final available = list.where((r) => !r.isArchived).toList();
        if (available.isEmpty) return const SizedBox.shrink();
        return DropdownButtonFormField<String?>(
          initialValue: _routeId,
          decoration: InputDecoration(labelText: t.get('routes.followRoute')),
          items: [
            DropdownMenuItem<String?>(
              value: null,
              child: Text(t.get('routes.noRoute')),
            ),
            for (final r in available)
              DropdownMenuItem<String?>(value: r.id, child: Text(r.name)),
          ],
          onChanged: (v) => setState(() => _routeId = v),
        );
      },
    );
  }

  Future<void> _start() async {
    if (_bikeId == null) return;
    setState(() {
      _busy = true;
      _errorKey = null;
    });
    final bikes = ref
        .read(bikeListProvider)
        .maybeWhen(data: (l) => l, orElse: () => []);
    final bike = bikes.firstWhere(
      (b) => b.id == _bikeId,
      orElse: () => bikes.first,
    );
    final perm = await ref
        .read(rideSessionProvider.notifier)
        .startRide(bikeId: bike.id, bikeName: bike.name, routeId: _routeId);
    if (!mounted) return;
    setState(() => _busy = false);
    switch (perm) {
      case LocationPermissionState.granted:
        context.go('/ride/recording');
      case LocationPermissionState.denied:
        setState(() => _errorKey = 'ride.permissionDenied');
      case LocationPermissionState.deniedForever:
        setState(() => _errorKey = 'ride.permissionBlocked');
      case LocationPermissionState.serviceDisabled:
        setState(() => _errorKey = 'ride.serviceDisabled');
      case LocationPermissionState.imprecise:
        setState(() => _errorKey = 'ride.imprecise');
    }
  }
}
