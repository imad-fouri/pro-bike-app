import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/ride_history_repository.dart';
import '../domain/ride_history.dart';

final rideHistoryRepositoryProvider = Provider<RideHistoryRepository>((ref) {
  return RideHistoryRepository(ref.watch(apiClientProvider));
});

/// Paginated completed-ride feed, newest first.
///
/// Server-confirmed paging like the notification center: pages append only
/// after the server answers, refresh re-reads page one, and a failed
/// load-more keeps the rows already shown.
class RideHistoryListState {
  final List<RideHistoryItem> items;
  final int total;
  final int page;
  final int pageSize;
  final bool loadingMore;

  const RideHistoryListState({
    this.items = const [],
    this.total = 0,
    this.page = 1,
    this.pageSize = 20,
    this.loadingMore = false,
  });

  bool get hasMore => items.length < total;
  bool get isEmpty => items.isEmpty;

  RideHistoryListState copyWith({
    List<RideHistoryItem>? items,
    int? total,
    int? page,
    bool? loadingMore,
  }) => RideHistoryListState(
    items: items ?? this.items,
    total: total ?? this.total,
    page: page ?? this.page,
    pageSize: pageSize,
    loadingMore: loadingMore ?? this.loadingMore,
  );
}

class RideHistoryList extends AsyncNotifier<RideHistoryListState> {
  static const pageSize = 20;

  @override
  Future<RideHistoryListState> build() async {
    final page = await ref
        .watch(rideHistoryRepositoryProvider)
        .historyPage(pageSize: pageSize);
    return RideHistoryListState(
      items: page.items,
      total: page.total,
      pageSize: pageSize,
    );
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final page = await ref
          .read(rideHistoryRepositoryProvider)
          .historyPage(pageSize: pageSize);
      return RideHistoryListState(
        items: page.items,
        total: page.total,
        pageSize: pageSize,
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
          .read(rideHistoryRepositoryProvider)
          .historyPage(page: current.page + 1, pageSize: current.pageSize);
      state = AsyncData(
        current.copyWith(
          items: [...current.items, ...next.items],
          total: next.total,
          page: current.page + 1,
          loadingMore: false,
        ),
      );
    } on Object catch (e, st) {
      state = AsyncError(e, st);
    }
  }
}

final rideHistoryProvider =
    AsyncNotifierProvider<RideHistoryList, RideHistoryListState>(
      RideHistoryList.new,
      // A failed page must surface immediately (the feed owns an explicit
      // retry action). The framework default would re-issue the request
      // with a backoff behind a screen the rider may have left.
      retry: (retryCount, error) => null,
    );

/// One ride + its map trail. A 404 surfaces as an error the detail screen
/// renders as not-found; anything else renders as a retryable error.
class RideDetailState {
  final RideHistoryItem ride;
  final List<RideTrackPoint> points;
  final bool truncated;

  const RideDetailState({
    required this.ride,
    required this.points,
    required this.truncated,
  });
}

final rideDetailProvider = FutureProvider.family<RideDetailState, String>(
  (ref, rideId) async {
    final repo = ref.watch(rideHistoryRepositoryProvider);
    final ride = await repo.detail(rideId);
    final track = await repo.trackPoints(rideId);
    return RideDetailState(
      ride: ride,
      points: track.points,
      truncated: track.truncated,
    );
  },
  // Same rule as the feed: surface immediately, retry explicitly.
  retry: (retryCount, error) => null,
);

/// Dashboard aggregates with explicit refresh for the retry path.
class RidesOverviewState extends AsyncNotifier<RidesOverview> {
  @override
  Future<RidesOverview> build() =>
      ref.watch(rideHistoryRepositoryProvider).overview();

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(
      () => ref.read(rideHistoryRepositoryProvider).overview(),
    );
  }
}

final ridesOverviewProvider =
    AsyncNotifierProvider<RidesOverviewState, RidesOverview>(
      RidesOverviewState.new,
      // Same rule as the feed: surface immediately, retry explicitly.
      retry: (retryCount, error) => null,
    );
