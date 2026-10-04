import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/social_repository.dart';
import '../domain/social_profile.dart';

final socialRepositoryProvider = Provider<SocialRepository>(
  (ref) => SocialRepository(ref.watch(apiClientProvider)),
);

/// The viewer's own social profile. Also the only place the privacy settings
/// live, so a privacy write refreshes this and nothing else needs guessing.
final mySocialProfileProvider = FutureProvider<SocialProfile>(
  (ref) => ref.watch(socialRepositoryProvider).myProfile(),
);

/// Another rider's privacy-filtered profile, relationship state included.
/// `autoDispose` so leaving a profile screen does not pin a stale relationship
/// in memory; re-entering re-reads the server's answer.
final userProfileProvider = FutureProvider.autoDispose
    .family<PublicProfile, String>(
      (ref, userId) => ref.watch(socialRepositoryProvider).userProfile(userId),
    );

// ---------------------------------------------------------------------------
// Paginated listings
// ---------------------------------------------------------------------------

/// One page of a listing plus the cursor state the list widgets need.
class Paged<T> {
  final List<T> items;
  final int total;
  final int page;
  final int pageSize;
  final bool loadingMore;

  const Paged({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
    required this.loadingMore,
  });

  bool get hasMore => page * pageSize < total;

  Paged<T> copyWith({
    List<T>? items,
    int? total,
    int? page,
    bool? loadingMore,
  }) => Paged<T>(
    items: items ?? this.items,
    total: total ?? this.total,
    page: page ?? this.page,
    pageSize: pageSize,
    loadingMore: loadingMore ?? this.loadingMore,
  );
}

/// Shared paging machinery: refresh resets to page 1, `loadMore` appends only
/// when the server says there is more. Page size matches the server default.
abstract class _PagedNotifier<T> extends AsyncNotifier<Paged<T>> {
  static const pageSize = 20;

  SocialRepository get repo => ref.read(socialRepositoryProvider);

  Future<SocialPage<T>> fetch(int page);

  @override
  Future<Paged<T>> build() async {
    final first = await fetch(1);
    return Paged(
      items: first.items,
      total: first.total,
      page: first.page,
      pageSize: pageSize,
      loadingMore: false,
    );
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final first = await fetch(1);
      return Paged(
        items: first.items,
        total: first.total,
        page: first.page,
        pageSize: pageSize,
        loadingMore: false,
      );
    });
  }

  /// Append the next page. No-op while a page is in flight or at the end, so
  /// a scroll notification cannot fire a burst of duplicate page requests.
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || current.loadingMore || !current.hasMore) return;
    final next = current.page + 1;
    state = AsyncData(current.copyWith(loadingMore: true));
    try {
      final result = await fetch(next);
      state = AsyncData(
        current.copyWith(
          items: [...current.items, ...result.items],
          total: result.total,
          page: result.page,
          loadingMore: false,
        ),
      );
    } on Object catch (e, st) {
      // A failed append keeps the rows already on screen; the list surfaces
      // the error without discarding what the user can still read.
      state = AsyncError(e, st);
    }
  }
}

class FriendListNotifier extends _PagedNotifier<SocialFriend> {
  @override
  Future<SocialPage<SocialFriend>> fetch(int page) =>
      repo.friends(page: page, pageSize: _PagedNotifier.pageSize);
}

final friendListProvider =
    AsyncNotifierProvider<FriendListNotifier, Paged<SocialFriend>>(
      FriendListNotifier.new,
    );

class IncomingRequestsNotifier extends _PagedNotifier<FriendRequest> {
  @override
  Future<SocialPage<FriendRequest>> fetch(int page) => repo.friendRequests(
    direction: 'incoming',
    page: page,
    pageSize: _PagedNotifier.pageSize,
  );
}

class OutgoingRequestsNotifier extends _PagedNotifier<FriendRequest> {
  @override
  Future<SocialPage<FriendRequest>> fetch(int page) => repo.friendRequests(
    direction: 'outgoing',
    page: page,
    pageSize: _PagedNotifier.pageSize,
  );
}

final incomingRequestsProvider =
    AsyncNotifierProvider<IncomingRequestsNotifier, Paged<FriendRequest>>(
      IncomingRequestsNotifier.new,
    );

final outgoingRequestsProvider =
    AsyncNotifierProvider<OutgoingRequestsNotifier, Paged<FriendRequest>>(
      OutgoingRequestsNotifier.new,
    );

class BlockListNotifier extends _PagedNotifier<BlockedUser> {
  @override
  Future<SocialPage<BlockedUser>> fetch(int page) =>
      repo.blocks(page: page, pageSize: _PagedNotifier.pageSize);
}

final blockListProvider =
    AsyncNotifierProvider<BlockListNotifier, Paged<BlockedUser>>(
      BlockListNotifier.new,
    );

/// Search results for an exact query string. `autoDispose` plus a
/// query-keyed family means a new keystroke builds a new provider and the old
/// one is dropped; the page debounces before it asks for one.
final userSearchProvider = FutureProvider.autoDispose
    .family<SocialPage<PublicProfile>, String>((ref, query) {
      return ref.watch(socialRepositoryProvider).searchUsers(query);
    });

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/// Server-confirmed mutations with duplicate-submission protection.
///
/// Two rules hold for every method here:
/// 1. A key already in flight short-circuits. The UI disables its button, but
///    the guard is here too, so a double-tap or a re-entrant build cannot send
///    two requests. The backend also rejects the loser with 409; this avoids
///    spending that round trip.
/// 2. Nothing is applied optimistically. The call awaits the server, then
///    invalidates every provider whose contents the server may have changed.
///    Until the refresh lands the previous value is still on screen, so the UI
///    never claims a relationship the server has not confirmed.
class SocialActions extends Notifier<Set<String>> {
  @override
  Set<String> build() => const <String>{};

  SocialRepository get repo => ref.read(socialRepositoryProvider);

  bool isBusy(String key) => state.contains(key);

  /// Key for a per-user action, namespaced by action so a block and a friend
  /// request on the same rider never share a lock.
  static String userKey(String action, String userId) => '$action:$userId';

  static String requestKey(String action, String requestId) =>
      '$action:$requestId';

  /// Invalidate everything whose truth the server just decided. Targeted so a
  /// block does not nuke unrelated profile reads, and complete so no surface
  /// keeps a stale "FRIENDS" badge.
  void _afterMutation({String? userId}) {
    ref.invalidate(mySocialProfileProvider);
    if (userId != null) ref.invalidate(userProfileProvider(userId));
    ref.invalidate(friendListProvider);
    ref.invalidate(incomingRequestsProvider);
    ref.invalidate(outgoingRequestsProvider);
    ref.invalidate(blockListProvider);
    // Every search row embeds a relationship state, so a stale one would show
    // the wrong action buttons. Re-run the visible queries.
    ref.invalidate(userSearchProvider);
  }

  Future<void> sendFriendRequest(String userId) =>
      _run(userKey('request', userId), () async {
        await repo.sendFriendRequest(userId);
        _afterMutation(userId: userId);
      });

  Future<void> cancelFriendRequest(String requestId, String userId) =>
      _run(requestKey('cancel', requestId), () async {
        await repo.cancelFriendRequest(requestId);
        _afterMutation(userId: userId);
      });

  /// Cancel the viewer's outgoing request to [userId] from a profile screen,
  /// where only the relationship state (not the request id) is known.
  ///
  /// The id is resolved server-side by listing the outgoing queue rather than
  /// constructed locally: a guessed id would be a 404 and, worse, an id that
  /// happens to exist for a different pair. If no outgoing request is found,
  /// the providers are simply refreshed — the state was stale, and the
  /// authoritative answer is whatever the server now says.
  Future<void> cancelOutgoingRequest(String userId) => _run(
    userKey('cancel', userId),
    () async {
      final outgoing = await repo.friendRequests(
        direction: 'outgoing',
        pageSize: 100,
      );
      final match = outgoing.items.where((r) => r.userId == userId).firstOrNull;
      if (match != null) {
        await repo.cancelFriendRequest(match.id);
      }
      _afterMutation(userId: userId);
    },
  );

  Future<void> acceptFriendRequest(String requestId, String userId) =>
      _run(requestKey('accept', requestId), () async {
        await repo.acceptFriendRequest(requestId);
        _afterMutation(userId: userId);
      });

  Future<void> rejectFriendRequest(String requestId, String userId) =>
      _run(requestKey('reject', requestId), () async {
        await repo.rejectFriendRequest(requestId);
        _afterMutation(userId: userId);
      });

  Future<void> removeFriend(String userId) =>
      _run(userKey('remove', userId), () async {
        await repo.removeFriend(userId);
        _afterMutation(userId: userId);
      });

  /// A block also annihilates any friendship or pending request between the
  /// pair, server-side in the same transaction — so the relationship row is
  /// re-read rather than assumed gone.
  Future<void> blockUser(String userId) =>
      _run(userKey('block', userId), () async {
        await repo.blockUser(userId);
        _afterMutation(userId: userId);
      });

  /// Lifting the wall restores reachability, never the friendship: the server
  /// deleted the relationship row when the block went up (ADR-12 §3).
  Future<void> unblockUser(String userId) =>
      _run(userKey('unblock', userId), () async {
        await repo.unblockUser(userId);
        _afterMutation(userId: userId);
      });

  Future<void> saveProfile({
    required String username,
    required String displayName,
    required String bio,
    required String cyclingCategory,
    required String countryCode,
    required String city,
    required bool clearUsername,
  }) => _run('profile', () async {
    await repo.updateProfile(
      username: username,
      clearUsername: clearUsername,
      displayName: displayName,
      bio: bio,
      cyclingCategory: cyclingCategory,
      countryCode: countryCode,
      city: city,
    );
    ref.invalidate(mySocialProfileProvider);
  });

  Future<void> savePrivacy({
    ProfileVisibility? profileVisibility,
    FriendRequestsPolicy? allowFriendRequests,
    SearchVisibility? searchVisibility,
  }) => _run('privacy', () async {
    await repo.updatePrivacy(
      profileVisibility: profileVisibility,
      allowFriendRequests: allowFriendRequests,
      searchVisibility: searchVisibility,
    );
    ref.invalidate(mySocialProfileProvider);
  });

  Future<void> _run(String key, Future<void> Function() body) async {
    if (state.contains(key)) return;
    state = {...state, key};
    try {
      await body();
    } finally {
      final next = {...state};
      next.remove(key);
      state = next;
    }
  }
}

final socialActionsProvider = NotifierProvider<SocialActions, Set<String>>(
  SocialActions.new,
);
