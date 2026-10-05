import '../../../core/network/api_client.dart';
import '../domain/group_ride.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// Contract source of truth is `backend/app/api/v1/group_rides.py`. Every call
/// is `auth: true`; the viewer comes from the access token, never from a body.
/// A rider id in a path or body names only a *target* (somebody to invite, a
/// participant to remove) and the server re-resolves it against the caller's own
/// roster row.
///
/// There is no `is_organizer` argument anywhere in this class. Authority is a
/// property of the caller's roster row, checked server-side; sending a flag the
/// server ignores would invite the client to treat it as permission and then get
/// a 403 it did not expect (ADR-16 §3).
class GroupRideRepository {
  final ApiClient api;
  static const prefix = '/api/v1/group-rides';

  const GroupRideRepository(this.api);

  // --- rides ----------------------------------------------------------------

  Future<GroupRide> create({
    required String title,
    String? description,
    DateTime? startsAt,
    String? meetingPoint,
    String? routeId,
    int? routeVersion,
  }) async {
    final body = await api.post(prefix, {
      'title': title,
      'description': ?description,
      // Sent as an explicit UTC ISO string: an offset-less local time would be
      // re-read in the server's timezone, which is not the rider's.
      'starts_at': ?startsAt?.toUtc().toIso8601String(),
      'meeting_point': ?meetingPoint,
      // route_id/route_version are BOTH-OR-NEITHER server-side, and pinned as a
      // pair. Sending one without the other is a 422, so they travel together.
      'route_id': ?routeId,
      'route_version': ?routeVersion,
    }, auth: true);
    return GroupRide.fromJson(body);
  }

  /// Every ride the viewer holds any roster row on — joined, invited, declined,
  /// left, or removed — newest first.
  ///
  /// A removed or withdrawn rider keeps their row on purpose: their own history
  /// should still show the ride, while visibility of the live ride is gone.
  Future<GroupRideList> myRides() async {
    final body = await api.get(prefix, auth: true);
    return GroupRideList.fromJson(body);
  }

  Future<GroupRide> ride(String rideId) async {
    final body = await api.get('$prefix/$rideId', auth: true);
    return GroupRide.fromJson(body);
  }

  /// Pending invitations addressed to the viewer.
  Future<GroupRideInvitationList> invitations() async {
    final body = await api.get('$prefix/invitations', auth: true);
    return GroupRideInvitationList.fromJson(body);
  }

  // --- roster ---------------------------------------------------------------

  Future<GroupRide> invite(
    String rideId, {
    required String userId,
    String? message,
  }) async {
    final body = await api.post('$prefix/$rideId/invitations', {
      'user_id': userId,
      'message': ?message,
    }, auth: true);
    return GroupRide.fromJson(body);
  }

  /// Invite several riders, reporting per-target outcomes.
  Future<InviteBatchResult> inviteBatch(
    String rideId, {
    required List<String> userIds,
    String? message,
  }) async {
    final body = await api.post('$prefix/$rideId/invitations:batch', {
      'user_ids': userIds,
      if (message != null && message.isNotEmpty) 'message': message,
    }, auth: true);
    return InviteBatchResult.fromJson(body);
  }

  /// Answer your own invitation. `accept: false` declines.
  ///
  /// Idempotent on the server for a rider who already joined, so a double tap is
  /// not an error.
  Future<GroupRide> respond(String rideId, {required bool accept}) async {
    final body = await api.post('$prefix/$rideId/respond', {
      'accept': accept,
    }, auth: true);
    return GroupRide.fromJson(body);
  }

  /// Withdraw from a ride.
  ///
  /// Takes no permission and is never presented as something the organizer
  /// grants: withdrawal is a consent right, available at any point the ride is
  /// not already finished.
  Future<GroupRide> leave(String rideId) async {
    final body = await api.post('$prefix/$rideId/leave', {}, auth: true);
    return GroupRide.fromJson(body);
  }

  Future<GroupRide> removeParticipant(String rideId, String userId) async {
    final body = await api.delete(
      '$prefix/$rideId/participants/$userId',
      auth: true,
    );
    return GroupRide.fromJson(body);
  }

  // --- lifecycle ------------------------------------------------------------

  Future<GroupRide> start(String rideId) async {
    final body = await api.post('$prefix/$rideId/start', {}, auth: true);
    return GroupRide.fromJson(body);
  }

  Future<GroupRide> complete(String rideId) async {
    final body = await api.post('$prefix/$rideId/complete', {}, auth: true);
    return GroupRide.fromJson(body);
  }

  /// Allowed from `open` or `started` — cancelling a ride that has already
  /// rolled is normal, and forcing the organizer to mark it completed first
  /// would make the one outcome everybody wants to record the hardest.
  Future<GroupRide> cancel(String rideId) async {
    final body = await api.post('$prefix/$rideId/cancel', {}, auth: true);
    return GroupRide.fromJson(body);
  }

  // --- chat -----------------------------------------------------------------

  /// The ride's single channel, created on first open.
  ///
  /// Fetching it through the ride means the client never has to learn a channel
  /// id before it can display a ride's messages. Authorization is re-derived
  /// server-side from the live roster, so opening a ride after being removed
  /// fails there and not here.
  Future<Map<String, dynamic>> conversation(String rideId) =>
      api.get('$prefix/$rideId/conversation', auth: true);

  // --- live location --------------------------------------------------------

  /// Publish THIS rider's position.
  ///
  /// Explicit opt-in only. Being on a ride is not consent, and nothing here ever
  /// publishes a position without the rider having asked for it.
  Future<Map<String, dynamic>> publishLocation(
    String rideId, {
    required double latitude,
    required double longitude,
    double? accuracyM,
  }) => api.post('$prefix/$rideId/location', {
    'latitude': latitude,
    'longitude': longitude,
    'accuracy_m': ?accuracyM,
  }, auth: true);

  /// Stop sharing. Explicit and idempotent — a rider who wants to stop being
  /// visible must never be told they were not.
  Future<Map<String, dynamic>> stopSharing(String rideId) =>
      api.delete('$prefix/$rideId/location', auth: true);

  /// The riders currently sharing, as visible to this viewer.
  ///
  /// Throws on 503: an unreachable Redis is an error state, never an empty list,
  /// so "nobody is sharing" and "we could not ask" stay distinguishable.
  Future<RideLocationSnapshot> locations(String rideId) async {
    final body = await api.get('$prefix/$rideId/location', auth: true);
    return RideLocationSnapshot.fromJson(body);
  }
}
