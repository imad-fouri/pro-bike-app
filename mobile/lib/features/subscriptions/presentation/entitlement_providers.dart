import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/entitlement_repository.dart';
import '../domain/entitlement.dart';

/// How long a fetched state may be reused for display before the next screen
/// asks the server again.
///
/// Fifteen minutes is an operational default, not product authority: premium
/// endpoints enforce access on every request, so a stale cache can only show a
/// briefly wrong label, never open a locked feature.
final entitlementCacheTtlProvider = Provider<Duration>(
  (_) => const Duration(minutes: 15),
);

final entitlementRepositoryProvider = Provider<EntitlementRepository>(
  (ref) => EntitlementRepository(ref.watch(apiClientProvider)),
);

/// In-memory entitlement cache tied to the session.
///
/// Watching [authProvider] is what makes logout and account switching safe:
/// the moment authentication ends, this provider rebuilds to `null`, so the
/// previous rider's plan cannot survive into the next session. Nothing here
/// is written to disk; a restart starts with no cached authority at all.
class EntitlementNotifier extends AsyncNotifier<EntitlementState?> {
  @override
  Future<EntitlementState?> build() async {
    final auth = ref.watch(authProvider);
    if (!auth.isAuthenticated) return null;
    return ref.watch(entitlementRepositoryProvider).fetch();
  }

  bool isFreshAt(DateTime now) {
    final cached = switch (state) {
      AsyncData(:final value) => value,
      _ => null,
    };
    if (cached == null) return false;
    final ttl = ref.read(entitlementCacheTtlProvider);
    return now.toUtc().difference(cached.fetchedAt) <= ttl;
  }

  Future<void> refresh() async {
    if (!ref.read(authProvider).isAuthenticated) {
      state = const AsyncData(null);
      return;
    }
    state = const AsyncLoading();
    state = await AsyncValue.guard(
      () => ref.read(entitlementRepositoryProvider).fetch(),
    );
  }

  Future<void> refreshIfStale() async {
    if (isFreshAt(DateTime.now().toUtc())) return;
    await refresh();
  }
}

final entitlementProvider =
    AsyncNotifierProvider<EntitlementNotifier, EntitlementState?>(
      EntitlementNotifier.new,
    );
