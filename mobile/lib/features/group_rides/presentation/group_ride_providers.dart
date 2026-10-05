import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../../chat/presentation/chat_providers.dart';
import '../../social/presentation/social_providers.dart';
import '../data/group_ride_repository.dart';
import '../domain/group_ride.dart';
import 'ride_location_controller.dart';

final groupRideRepositoryProvider = Provider<GroupRideRepository>(
  (ref) => GroupRideRepository(ref.watch(apiClientProvider)),
);

/// The viewer's own user id, taken from their profile.
///
/// Read from the session rather than remembered by the UI: the roster does not
/// label which row is the viewer's, and a stale cached id would put somebody
/// else's row on screen.
final myUserIdProvider = FutureProvider<String>(
  (ref) async => (await ref.watch(mySocialProfileProvider.future)).userId,
);

/// Every ride the viewer holds a roster row on — the only ride list that exists.
///
/// Not filtered by status client-side: a cancelled ride is still part of the
/// viewer's history, and hiding it would make the record disagree with the
/// server's.
final myRidesProvider = FutureProvider.autoDispose<GroupRideList>((ref) {
  return ref.watch(groupRideRepositoryProvider).myRides();
});

/// Pending invitations. Kept separate from [myRidesProvider] because this is the
/// ONE read a rider with no roster row on the ride is allowed to make.
final rideInvitationsProvider =
    FutureProvider.autoDispose<GroupRideInvitationList>((ref) {
      return ref.watch(groupRideRepositoryProvider).invitations();
    });

/// One ride, re-read on demand. `autoDispose` so leaving the screen drops a
/// roster and a status that may have changed while the rider was elsewhere.
final rideDetailProvider = FutureProvider.autoDispose.family<GroupRide, String>(
  (ref, rideId) {
    return ref.watch(groupRideRepositoryProvider).ride(rideId);
  },
);

/// The ride's single channel, created on first open.
///
/// Fetched through the ride rather than through `/chat`, so the client never has
/// to learn a channel id before it can show a ride's messages. The id is still
/// the server's: this only saves a round trip, it does not derive anything.
///
/// This is the one place in the phase where a rider with no `joined` row gets a
/// 404 — which is the point. A rider who was removed must not be able to open the
/// channel they used to have, and must not learn that they used to have it.
final rideConversationProvider = FutureProvider.autoDispose
    .family<RideConversation, String>((ref, rideId) async {
      final body = await ref
          .watch(groupRideRepositoryProvider)
          .conversation(rideId);
      return RideConversation(
        id: '${body['id'] ?? ''}',
        groupRideId: rideId,
        title: body['group_ride_title'] as String? ?? '',
        unreadCount: (body['unread_count'] as num?)?.toInt() ?? 0,
      );
    });

/// The minimum a ride's chat entry point needs: an id to open and a title to put
/// in an app bar.
class RideConversation {
  final String id;
  final String groupRideId;
  final String title;
  final int unreadCount;

  const RideConversation({
    required this.id,
    required this.groupRideId,
    required this.title,
    required this.unreadCount,
  });
}

/// Live location for one ride, and the opt-in that starts it.
///
/// `autoDispose`: a controller holds an open position stream and a poll timer, so
/// one must not outlive the screen that asked for it. Leaving the ride page
/// therefore stops this device's broadcast as a side effect of leaving, which is
/// the behaviour ADR-16 §6 wants even before the rider taps "stop sharing".
///
/// The family argument is captured here rather than read inside the notifier: a
/// family notifier is instantiated before its dependencies are resolvable, so the
/// ride id has to arrive in its constructor. Everything else — the gateway, the
/// GPS source, the poll ticker — is resolved in the notifier's `build()`.
final rideLocationProvider = NotifierProvider.autoDispose
    .family<RideLocationController, RideLocationState, String>(
      (rideId) => RideLocationController(rideId: rideId),
    );

final rideLocationGatewayProvider =
    Provider.family<GroupRideLocationGateway, String>((ref, rideId) {
      return _RepositoryLocationGateway(
        ref.watch(groupRideRepositoryProvider),
        rideId,
      );
    });

class _RepositoryLocationGateway implements GroupRideLocationGateway {
  final GroupRideRepository _repo;
  final String _rideId;

  _RepositoryLocationGateway(this._repo, this._rideId);

  @override
  Future<void> publish({
    required double latitude,
    required double longitude,
    double? accuracyM,
  }) => _repo.publishLocation(
    _rideId,
    latitude: latitude,
    longitude: longitude,
    accuracyM: accuracyM,
  );

  @override
  Future<void> stopSharing() => _repo.stopSharing(_rideId);

  @override
  Future<RideLocationSnapshot> locations() => _repo.locations(_rideId);
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------
/// Server-confirmed group-ride mutations with duplicate-submission protection.
///
/// Mirrors the Phase 8.1 rules exactly:
/// 1. A key already in flight short-circuits, and each button reads its busy
///    flag from this same set, so the guard and the disabled button cannot
///    disagree.
/// 2. Nothing is applied optimistically. The call awaits the server, then
///    invalidates every provider whose contents the server may have changed.
///
/// Ride state is never computed locally. After a mutation the detail, the list,
/// and the invitation inbox are re-read, so a stale "Open" badge cannot outlive
/// the start that removed it.
class GroupRideActions extends Notifier<Set<String>> {
  @override
  Set<String> build() => const <String>{};

  GroupRideRepository get repo => ref.read(groupRideRepositoryProvider);

  bool isBusy(String key) => state.contains(key);

  static String rideKey(String action, String rideId) => '$action:$rideId';

  static String targetKey(String action, String rideId, String userId) =>
      '$action:$rideId:$userId';

  /// Invalidate everything whose truth the server just decided.
  ///
  /// The chat inbox is invalidated too: joining or leaving a ride changes which
  /// conversations this rider may open, and the inbox does not otherwise know a
  /// ride changed.
  void _afterMutation({String? rideId}) {
    ref.invalidate(myRidesProvider);
    ref.invalidate(rideInvitationsProvider);
    if (rideId != null) {
      ref.invalidate(rideDetailProvider(rideId));
      ref.invalidate(rideConversationProvider(rideId));
      ref.invalidate(rideLocationProvider(rideId));
    }
    ref.invalidate(conversationListProvider);
  }

  /// Stop this device's broadcast BEFORE invalidating the location controller.
  ///
  /// Order matters and is easy to get backwards. `rideLocationProvider` is an
  /// `autoDispose` family, so invalidating it disposes the live controller and the
  /// next read builds a fresh one whose `_everPublished` is false. Reading the
  /// notifier after invalidating would therefore call a controller that never
  /// published and skip the server-side revoke — leaving a position in Redis until
  /// its TTL, on a ride this rider has just left.
  Future<void> _stopSharingThenInvalidate(String rideId) async {
    await ref.read(rideLocationProvider(rideId).notifier).stopSharing();
    ref.invalidate(rideLocationProvider(rideId));
  }

  Future<GroupRide> create({
    required String title,
    String? description,
    DateTime? startsAt,
    String? meetingPoint,
    String? routeId,
    int? routeVersion,
  }) async {
    var created = const GroupRideList(<GroupRide>[]);
    await _run(rideKey('create', title), () async {
      created = GroupRideList([
        await repo.create(
          title: title,
          description: description,
          startsAt: startsAt,
          meetingPoint: meetingPoint,
          routeId: routeId,
          routeVersion: routeVersion,
        ),
      ]);
      _afterMutation();
    });
    return created.items.first;
  }

  /// Returns the server's updated ride so the caller can report the real outcome
  /// (e.g. "already joined" vs a fresh join) instead of guessing from the
  /// request it sent.
  Future<GroupRide?> invite(
    String rideId,
    String userId, {
    String? message,
  }) async {
    GroupRide? updated;
    await _run(targetKey('invite', rideId, userId), () async {
      updated = await repo.invite(rideId, userId: userId, message: message);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

  Future<InviteBatchResult?> inviteBatch(
    String rideId, {
    required List<String> userIds,
    String? message,
  }) async {
    InviteBatchResult? result;
    await _run(rideKey('invite-batch', rideId), () async {
      result = await repo.inviteBatch(
        rideId,
        userIds: userIds,
        message: message,
      );
      _afterMutation(rideId: rideId);
    });
    return result;
  }

  Future<GroupRide?> respond(String rideId, {required bool accept}) async {
    GroupRide? updated;
    await _run(rideKey(accept ? 'accept' : 'decline', rideId), () async {
      updated = await repo.respond(rideId, accept: accept);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

  Future<GroupRide?> leave(String rideId) async {
    GroupRide? updated;
    await _run(rideKey('leave', rideId), () async {
      updated = await repo.leave(rideId);
      // BEFORE `_afterMutation`, which invalidates `rideLocationProvider` and would
      // replace the live controller with a fresh one that never published. See
      // `_stopSharingThenInvalidate`.
      //
      // Withdrawing also stops this rider's own location broadcast: continuing to
      // publish a position to a ride you have left would be sharing with a roster
      // that no longer includes you.
      await _stopSharingThenInvalidate(rideId);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

  Future<GroupRide?> removeParticipant(String rideId, String userId) async {
    GroupRide? updated;
    await _run(targetKey('remove', rideId, userId), () async {
      updated = await repo.removeParticipant(rideId, userId);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

  Future<GroupRide?> start(String rideId) async {
    GroupRide? updated;
    await _run(rideKey('start', rideId), () async {
      updated = await repo.start(rideId);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

  Future<GroupRide?> complete(String rideId) async {
    GroupRide? updated;
    await _run(rideKey('complete', rideId), () async {
      updated = await repo.complete(rideId);
      // A finished ride has no live map left to publish onto. Ordered before
      // `_afterMutation` for the reason given in `leave`.
      await _stopSharingThenInvalidate(rideId);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

  Future<GroupRide?> cancel(String rideId) async {
    GroupRide? updated;
    await _run(rideKey('cancel', rideId), () async {
      updated = await repo.cancel(rideId);
      // Ordered before `_afterMutation` for the reason given in `leave`.
      await _stopSharingThenInvalidate(rideId);
      _afterMutation(rideId: rideId);
    });
    return updated;
  }

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

final groupRideActionsProvider =
    NotifierProvider<GroupRideActions, Set<String>>(GroupRideActions.new);
