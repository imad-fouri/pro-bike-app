import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../../../core/routing/app_router.dart';
import '../data/notification_repository.dart';
import '../data/push_registrar.dart';
import '../domain/notification.dart';
import '../domain/notification_links.dart';

final notificationRepositoryProvider = Provider<NotificationRepository>(
  (ref) => NotificationRepository(ref.watch(apiClientProvider)),
);

/// Holds a notification tap that arrived before the app could act on it.
///
/// A cold start from a push lands while `AuthStatus` is `unknown`/`loading`, and
/// the router redirects to `/splash`, so navigating immediately would drop the
/// destination. The tap is parked here and replayed once auth settles — which
/// is the whole reason a notification survives being tapped from a terminated
/// app.
final pendingLinkProvider =
    NotifierProvider<PendingNotificationLinkNotifier, PendingNotificationLink>(
      PendingNotificationLinkNotifier.new,
    );

class PendingNotificationLinkNotifier
    extends Notifier<PendingNotificationLink> {
  @override
  PendingNotificationLink build() => PendingNotificationLink();

  /// Record a validated tap.
  ///
  /// A malformed link is ignored rather than stored: there is nothing to
  /// navigate to, and holding it would replay a dead intent later.
  void open(String? deepLink) {
    final link = parseNotificationLink(deepLink);
    if (link == null) return;
    state.set(link);
  }

  /// Take the pending tap, if any. Returns it at most once.
  NotificationLink? take() => state.consume();

  void clear() => state.clear();
}

// ---------------------------------------------------------------------------
// Notification center
// ---------------------------------------------------------------------------

/// The unread count polled by the app bar.
///
/// Separate from the list so the bell can refresh without refetching a page.
class UnreadCountNotifier extends AsyncNotifier<int> {
  @override
  Future<int> build() =>
      ref.watch(notificationRepositoryProvider).unreadCount();

  Future<void> refresh() async {
    // `AsyncValue.guard` rather than letting a failure escape: the badge is
    // decoration, and a network blip must not turn the bell into an error
    // surface. A failed refresh keeps the last known count.
    final refreshed = await AsyncValue.guard(
      () => ref.read(notificationRepositoryProvider).unreadCount(),
    );
    state = refreshed.hasError ? state : AsyncData(refreshed.value ?? 0);
  }
}

/// `retry:` is disabled deliberately. The count is decoration, and Riverpod's
/// default would re-issue the request with a backoff whenever it fails — turning
/// a flaky network into a retry loop behind a bell the rider is not even looking
/// at. A failed refresh keeps the last known value, and the next foreground
/// refreshes it.
final unreadCountProvider = AsyncNotifierProvider<UnreadCountNotifier, int>(
  UnreadCountNotifier.new,
  retry: (retryCount, error) => null,
);

/// One page of the notification center plus its paging state.
class NotificationListState {
  final List<AppNotification> items;
  final int total;
  final int page;
  final int pageSize;
  final bool loadingMore;

  const NotificationListState({
    this.items = const [],
    this.total = 0,
    this.page = 1,
    this.pageSize = 20,
    this.loadingMore = false,
  });

  bool get hasMore => page * pageSize < total;
  bool get isEmpty => items.isEmpty;

  NotificationListState copyWith({
    List<AppNotification>? items,
    int? total,
    int? page,
    bool? loadingMore,
  }) => NotificationListState(
    items: items ?? this.items,
    total: total ?? this.total,
    page: page ?? this.page,
    pageSize: pageSize,
    loadingMore: loadingMore ?? this.loadingMore,
  );
}

/// The notification center, newest first.
///
/// Nothing is applied optimistically. Marking read awaits the server and then
/// updates from its answer, because the server is what decides whether a
/// notification is still unread — and a locally-optimistic dot that the server
/// then contradicts is worse than a brief delay.
class NotificationListNotifier extends AsyncNotifier<NotificationListState> {
  static const pageSize = 20;

  @override
  Future<NotificationListState> build() async {
    final page = await ref
        .watch(notificationRepositoryProvider)
        .list(pageSize: pageSize);
    return NotificationListState(
      items: page.items,
      total: page.total,
      page: page.page,
      pageSize: page.pageSize,
    );
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final page = await ref
          .read(notificationRepositoryProvider)
          .list(pageSize: pageSize);
      return NotificationListState(
        items: page.items,
        total: page.total,
        page: page.page,
        pageSize: page.pageSize,
      );
    });
  }

  /// Append the next page. No-op while one is in flight or at the end, so a
  /// scroll notification cannot fire a burst of duplicate requests.
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || current.loadingMore || !current.hasMore) return;
    state = AsyncData(current.copyWith(loadingMore: true));
    try {
      final next = await ref
          .read(notificationRepositoryProvider)
          .list(page: current.page + 1, pageSize: current.pageSize);
      state = AsyncData(
        current.copyWith(
          items: [...current.items, ...next.items],
          total: next.total,
          page: next.page,
          loadingMore: false,
        ),
      );
    } on Object catch (e, st) {
      state = AsyncError(e, st);
    }
  }

  /// Mark one read and adopt the server's answer.
  Future<AppNotification?> markRead(String notificationId) async {
    final current = state.value;
    if (current == null) return null;
    final index = current.items.indexWhere((n) => n.id == notificationId);
    if (index < 0) return null;
    final previous = current.items[index];
    try {
      final updated = await ref
          .read(notificationRepositoryProvider)
          .markRead(notificationId);
      final latest = state.value ?? current;
      // Re-locate the row: an intervening refresh may have changed the index.
      final at = latest.items.indexWhere((n) => n.id == notificationId);
      final items = [...latest.items];
      if (at >= 0) items[at] = updated;
      state = AsyncData(latest.copyWith(items: items));
      await ref.read(unreadCountProvider.notifier).refresh();
      return updated;
    } on Object {
      // Restore the row so a failed tap does not leave a false "read" dot.
      if (previous.isUnread) {
        final latest = state.value;
        if (latest != null) {
          final at = latest.items.indexWhere((n) => n.id == notificationId);
          if (at >= 0) {
            final items = [...latest.items];
            items[at] = previous;
            state = AsyncData(latest.copyWith(items: items));
          }
        }
      }
      rethrow;
    }
  }

  /// Mark every unread read, then re-read the page from the server.
  Future<int> markAllRead() async {
    final marked = await ref.read(notificationRepositoryProvider).markAllRead();
    await refresh();
    await ref.read(unreadCountProvider.notifier).refresh();
    return marked;
  }
}

final notificationListProvider =
    AsyncNotifierProvider<NotificationListNotifier, NotificationListState>(
      NotificationListNotifier.new,
    );

/// Replays a parked tap once authentication resolves.
///
/// Held as a provider rather than inside the router because the router is
/// REBUILT on every auth-state change with `initialLocation: '/splash'`: a tap
/// that cold-starts the app would otherwise be discarded the moment auth
/// settles. Listening to [authProvider] here is what makes a tap work from a
/// terminated app, without changing how routing behaves for anything else.
final pendingLinkReplayProvider = Provider<void>((ref) {
  ref.listen<AuthState>(authProvider, (previous, next) {
    // Only act on a settled state: `unknown`/`loading` cannot be navigated from.
    final settled =
        next.status == AuthStatus.authenticated ||
        next.status == AuthStatus.unauthenticated;
    if (!settled) return;
    if (next.status != AuthStatus.authenticated) {
      // Signed out: a tap cannot be honoured, and holding it would replay into
      // the wrong account later.
      ref.read(pendingLinkProvider.notifier).clear();
      return;
    }
    final link = ref.read(pendingLinkProvider.notifier).take();
    if (link == null) return;
    final router = ref.read(routerProvider);
    if (link.id == null) {
      router.go(link.route);
    } else {
      router.go('${link.route}/${link.id}');
    }
  });
});

/// The rider's own registered devices. Never holds a token.
final pushDeviceListProvider = FutureProvider.autoDispose<List<PushDevice>>((
  ref,
) {
  return ref.watch(notificationRepositoryProvider).devices();
});

/// Server-confirmed device mutations with duplicate-submission protection.
///
/// The same two rules as the social and team action notifiers: an in-flight key
/// short-circuits, and nothing is applied before the server has answered.
class PushDeviceActions extends Notifier<Set<String>> {
  @override
  Set<String> build() => const <String>{};

  NotificationRepository get repo => ref.read(notificationRepositoryProvider);

  bool isBusy(String key) => state.contains(key);

  static String deviceKey(String deviceId) => 'device:$deviceId';

  /// Register or refresh this device.
  ///
  /// Returns null when no provider is available — the Phase 8.4 default — which
  /// is an ordinary outcome, not a failure. The UI reports "not configured yet"
  /// rather than an error.
  Future<PushDevice?> register({
    required String deviceId,
    required PushRegistrar registrar,
  }) async {
    final registration = await registrar.register();
    if (registration == null) return null;
    return _run(deviceKey(deviceId), () async {
      final device = await repo.registerDevice(
        deviceId: registration.deviceId,
        token: registration.token,
        platform: registration.platform,
        provider: registration.provider,
      );
      ref.read(deviceRegistrationProvider.notifier).state = ref
          .read(deviceRegistrationProvider)
          .having(registration.deviceId);
      ref.invalidate(pushDeviceListProvider);
      return device;
    });
  }

  Future<void> setEnabled(PushDevice device, bool enabled) =>
      _run(deviceKey(device.id), () async {
        await repo.setDeviceEnabled(device.id, enabled);
        ref.invalidate(pushDeviceListProvider);
      });

  Future<void> revoke(PushDevice device) =>
      _run(deviceKey(device.id), () async {
        await repo.revokeDevice(device.id);
        ref.read(deviceRegistrationProvider.notifier).state = ref
            .read(deviceRegistrationProvider)
            .without(device.deviceId);
        ref.invalidate(pushDeviceListProvider);
      });

  /// Best-effort unregistration on logout.
  ///
  /// Swallows failures deliberately: a rider logging out must not be blocked by
  /// a network error, and the server-side device row is revoked by the rider (or
  /// swept as stale) regardless.
  Future<void> bestEffortUnregister(PushRegistrar registrar) async {
    try {
      await registrar.unregister();
    } on Object {
      // Swallowed on purpose — see above.
    }
  }

  Future<T?> _run<T>(String key, Future<T> Function() body) async {
    if (state.contains(key)) return null;
    state = {...state, key};
    try {
      return await body();
    } finally {
      final next = {...state};
      next.remove(key);
      state = next;
    }
  }
}

final pushDeviceActionsProvider =
    NotifierProvider<PushDeviceActions, Set<String>>(PushDeviceActions.new);
