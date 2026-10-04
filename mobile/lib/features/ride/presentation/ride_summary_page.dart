import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../domain/units.dart';
import 'ride_providers.dart';

/// Post-finish summary (server summary when synced, local metrics fallback).
/// Advanced metrics (FTP/VO₂max/load) explicitly deferred — never faked.
class RideSummaryPage extends ConsumerStatefulWidget {
  const RideSummaryPage({super.key});
  @override
  ConsumerState<RideSummaryPage> createState() => _RideSummaryPageState();
}

class _RideSummaryPageState extends ConsumerState<RideSummaryPage> {
  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final live = ref.watch(rideSessionProvider);
    const imperial = false;
    final m = live?.metrics;
    if (m == null) {
      return Scaffold(
        appBar: AppBar(title: Text(t.get('ride.summary'))),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    return Scaffold(
      appBar: AppBar(title: Text(t.get('ride.summary'))),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              formatDistance(m.distanceM, imperial: imperial),
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.displaySmall,
            ),
            const SizedBox(height: 16),
            Text(
              '${t.get('ride.moving')}: ${formatDuration(m.movingS)} • '
              '${t.get('ride.climb')}: ${formatElevation(m.gainM, imperial: imperial)}',
              textAlign: TextAlign.center,
            ),
            if (live?.syncState != 'idle')
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  t.get('ride.offlineSummary'),
                  textAlign: TextAlign.center,
                ),
              ),
            const Spacer(),
            FilledButton(
              onPressed: () => context.go('/home'),
              child: Text(t.get('ride.done')),
            ),
          ],
        ),
      ),
    );
  }
}

/// Restart recovery: unfinished local ride found — resume, or close out.
class RideRecoveryPage extends ConsumerWidget {
  const RideRecoveryPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final unfinished = ref.watch(unfinishedRideProvider);
    return Scaffold(
      appBar: AppBar(title: Text(t.get('ride.recovery'))),
      body: unfinished.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => Center(child: Text(t.get('ride.genericError'))),
        data: (ride) {
          if (ride == null) {
            WidgetsBinding.instance.addPostFrameCallback(
              (_) => context.go('/ride/start'),
            );
            return const Center(child: CircularProgressIndicator());
          }
          return Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(t.get('ride.unfinishedBody'), textAlign: TextAlign.center),
                const SizedBox(height: 24),
                FilledButton(
                  onPressed: () async {
                    final session = ref.read(rideSessionProvider.notifier);
                    // Bike name unknown offline — resolved on next sync; use id.
                    await session.startRide(
                      bikeId: ride.bikeId,
                      bikeName: ride.bikeId,
                      resumeLocalId: ride.id,
                    );
                    if (context.mounted) context.go('/ride/recording');
                  },
                  child: Text(t.get('ride.resumeRide')),
                ),
                const SizedBox(height: 12),
                OutlinedButton(
                  onPressed: () async {
                    final session = ref.read(rideSessionProvider.notifier);
                    await session.startRide(
                      bikeId: ride.bikeId,
                      bikeName: ride.bikeId,
                      resumeLocalId: ride.id,
                    );
                    await session.finish();
                    if (context.mounted) context.go('/ride/summary');
                  },
                  child: Text(t.get('ride.finishRide')),
                ),
                const SizedBox(height: 12),
                TextButton(
                  onPressed: () async {
                    final session = ref.read(rideSessionProvider.notifier);
                    await session.startRide(
                      bikeId: ride.bikeId,
                      bikeName: ride.bikeId,
                      resumeLocalId: ride.id,
                    );
                    await session.discardRide();
                    if (context.mounted) context.go('/home');
                  },
                  child: Text(t.get('ride.discardRide')),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
