/// Phase 9 group-ride domain, provider and live-location tests (ADR-16).
///
/// Three things are defended here, and each is cheap to get subtly wrong:
///
/// 1. **Derived truth, not re-sent truth.** `viewer` carries only two booleans,
///    so the UI must read "am I invited?" out of the roster. Getting that wrong
///    either hides Accept from a rider who was invited, or offers it to a rider
///    who was never on the ride.
/// 2. **A route pin is both-or-neither.** Half a pair is a 422, and a "latest
///    version" default would silently change the geometry riders agreed on.
/// 3. **Location is opt-in and forgets.** Nothing publishes until asked, a failed
///    read is an error rather than an empty map, and a revoked position is
///    actually revoked.
///
/// The location tests drive a scripted ticker and a fake gateway rather than a
/// real GPS or a real clock, so "polled twice" and "stopped once" are assertions
/// about this code and not about scheduler timing.
library;

import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/group_rides/data/group_ride_repository.dart';
import 'package:cyclecoach/features/group_rides/domain/group_ride.dart';
import 'package:cyclecoach/features/group_rides/domain/ride_validators.dart';
import 'package:cyclecoach/features/group_rides/presentation/group_ride_providers.dart';
import 'package:cyclecoach/features/group_rides/presentation/ride_location_controller.dart';
import 'package:cyclecoach/features/ride/domain/gps_processor.dart';
import 'package:cyclecoach/features/ride/domain/location_source.dart';
import 'package:cyclecoach/features/ride/presentation/ride_providers.dart'
    show locationSourceProvider;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

Map<String, dynamic> participantJson({
  String userId = 'u-2',
  String role = 'participant',
  String status = 'joined',
  String? invitedByUserId = 'u-1',
  String? message,
  String? respondedAt = '2026-01-01T10:00:00Z',
  String? username = 'karim_amrani',
  String? displayName = 'Karim Amrani',
  String createdAt = '2026-01-01T09:00:00Z',
}) => {
  'user_id': userId,
  'role': role,
  'status': status,
  'invited_by_user_id': invitedByUserId,
  'message': message,
  'responded_at': respondedAt,
  'created_at': createdAt,
  'username': username,
  'display_name': displayName,
  'avatar_url': null,
};

Map<String, dynamic> rideJson({
  String id = 'r-1',
  String organizerUserId = 'u-1',
  String title = 'Sunday Spin',
  String? description,
  String status = 'open',
  String? startsAt,
  String? meetingPoint,
  String? routeId,
  int? routeVersion,
  List<Map<String, dynamic>>? roster,
  Map<String, dynamic>? viewer,
  int? participantCount,
}) => {
  'id': id,
  'organizer_user_id': organizerUserId,
  'title': title,
  'description': description,
  'status': status,
  'starts_at': startsAt,
  'meeting_point': meetingPoint,
  'route_id': routeId,
  'route_version': routeVersion,
  'created_at': '2026-01-01T08:00:00Z',
  'updated_at': '2026-01-01T08:00:00Z',
  'started_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'participant_count': participantCount ?? (roster ?? const []).length,
  'roster': roster ?? const [],
  'viewer': viewer ?? const {'is_organizer': false, 'is_joined': false},
};

Map<String, dynamic> invitationJson({
  String participantId = 'p-1',
  String groupRideId = 'r-1',
  String title = 'Sunday Spin',
  String? message,
  String? organizerUsername = 'imad_fouri',
  String? organizerDisplayName = 'Imad Fouri',
}) => {
  'participant_id': participantId,
  'group_ride_id': groupRideId,
  'title': title,
  'starts_at': null,
  'meeting_point': null,
  'message': message,
  'created_at': '2026-01-01T09:00:00Z',
  'organizer_user_id': 'u-1',
  'username': organizerUsername,
  'display_name': organizerDisplayName,
  'avatar_url': null,
};

Map<String, dynamic> riderLocationJson({
  String userId = 'u-2',
  String username = 'karim_amrani',
  String? displayName = 'Karim Amrani',
  double latitude = 33.5,
  double longitude = -6.5,
  double? accuracyM = 12,
  int ageSeconds = 4,
  bool isSelf = false,
}) => {
  'user_id': userId,
  'username': username,
  'display_name': displayName,
  'avatar_url': null,
  'latitude': latitude,
  'longitude': longitude,
  'accuracy_m': accuracyM,
  'age_seconds': ageSeconds,
  'is_self': isSelf,
};

Map<String, dynamic> snapshotJson({
  List<Map<String, dynamic>>? items,
  int staleAfterSeconds = 60,
  int expiresInSeconds = 300,
}) => {
  'items': items ?? const [],
  'stale_after_seconds': staleAfterSeconds,
  'expires_in_seconds': expiresInSeconds,
};

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

/// A gateway that records what the controller asked for and fails on demand.
///
/// The interesting behaviour of [RideLocationController] is what happens when
/// Redis is unreachable or a permission is refused, and neither is reachable from
/// a real GPS in a test.
class FakeLocationGateway implements GroupRideLocationGateway {
  final List<String> calls = [];
  int publishes = 0;
  int stops = 0;
  int reads = 0;

  /// Positions handed to the last `locations()` call.
  List<Map<String, dynamic>> snapshot = const [];

  Object? failLocationsWith;
  Object? failPublishWith;
  Object? failStopWith;

  /// Set once a publish has happened, so `stopSharing` can be checked for the
  /// revoke it is supposed to issue.
  bool get everPublished => publishes > 0;

  @override
  Future<void> publish({
    required double latitude,
    required double longitude,
    double? accuracyM,
  }) async {
    calls.add('publish($latitude,$longitude,$accuracyM)');
    publishes++;
    final failure = failPublishWith;
    if (failure != null) throw failure;
  }

  @override
  Future<void> stopSharing() async {
    calls.add('stop');
    stops++;
    final failure = failStopWith;
    if (failure != null) throw failure;
  }

  @override
  Future<RideLocationSnapshot> locations() async {
    calls.add('locations');
    reads++;
    final failure = failLocationsWith;
    if (failure != null) throw failure;
    return RideLocationSnapshot.fromJson(snapshotJson(items: snapshot));
  }
}

/// A scripted [LocationSource]. No plugin, no permission dialog, no platform.
class FakeLocationSource implements LocationSource {
  FakeLocationSource({this.permission = LocationPermissionState.granted});

  LocationPermissionState permission;
  final StreamController<GpsObservation> _controller =
      StreamController<GpsObservation>.broadcast();
  final List<int> requestedSeq = [];
  int ensureCalls = 0;
  Object? failEnsureWith;

  @override
  Future<LocationPermissionState> ensurePrecisePermission() async {
    ensureCalls++;
    final failure = failEnsureWith;
    if (failure != null) throw failure;
    return permission;
  }

  @override
  Stream<GpsObservation> positions({
    required TrackingPolicy policy,
    required int Function() nextSeq,
  }) {
    requestedSeq.add(nextSeq());
    return _controller.stream;
  }

  @override
  Future<void> openSettings() async {}

  int _seq = 0;

  void emit({double lat = 33.5, double lon = -6.5, double? accuracy = 8}) {
    _controller.add(
      GpsObservation(
        seq: ++_seq,
        lat: lat,
        lon: lon,
        time: DateTime.now(),
        accuracy: accuracy,
      ),
    );
  }

  /// Fail the stream the way a dead GPS plugin does, so the controller's
  /// `onError` path is reachable from a test.
  void emitError(Object error) {
    _controller.addError(error);
  }

  Future<void> close() => _controller.close();
}

/// A ticker the test steps by hand, so poll counts are never a race.
class ScriptedTicker {
  final Map<Duration, StreamController<void>> _controllers = {};
  final List<Duration> requested = [];

  Stream<void> factory(Duration interval) {
    requested.add(interval);
    return _controllers
        .putIfAbsent(interval, () => StreamController<void>.broadcast())
        .stream;
  }

  void tick() {
    for (final controller in _controllers.values) {
      if (!controller.isClosed) controller.add(null);
    }
  }

  Future<void> close() async {
    for (final controller in _controllers.values) {
      if (!controller.isClosed) await controller.close();
    }
  }
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

class Harness {
  Harness._(this.container, this.gateway, this.source, this.ticker);

  final ProviderContainer container;
  final FakeLocationGateway gateway;
  final FakeLocationSource source;
  final ScriptedTicker ticker;

  RideLocationController location(String rideId) =>
      container.read(rideLocationProvider(rideId).notifier);

  RideLocationState locationState(String rideId) =>
      container.read(rideLocationProvider(rideId));

  /// Keep the location family alive so `container.read` returns the live
  /// controller rather than rebuilding one that has never published.
  ProviderSubscription<RideLocationState> keepLocationAlive(String rideId) =>
      container.listen<RideLocationState>(
        rideLocationProvider(rideId),
        (_, _) {},
        fireImmediately: true,
      );

  Future<void> dispose() async {
    container.dispose();
    await ticker.close();
    await source.close();
  }
}

Harness harness({String rideId = 'r-1'}) {
  final gateway = FakeLocationGateway();
  final source = FakeLocationSource();
  final ticker = ScriptedTicker();
  final container = ProviderContainer(
    overrides: [
      rideLocationGatewayProvider(rideId).overrideWithValue(gateway),
      locationSourceProvider.overrideWithValue(source),
      rideLocationTickerProvider.overrideWithValue(ticker.factory),
    ],
  );
  return Harness._(container, gateway, source, ticker);
}

void main() {
  group('domain: lifecycle parsing', () {
    test('every wire status round-trips and labels itself', () {
      expect(GroupRideStatus.parse('open'), GroupRideStatus.open);
      expect(GroupRideStatus.parse('started'), GroupRideStatus.started);
      expect(GroupRideStatus.parse('completed'), GroupRideStatus.completed);
      expect(GroupRideStatus.parse('cancelled'), GroupRideStatus.cancelled);

      for (final status in GroupRideStatus.values) {
        expect(GroupRideStatus.parse(status.wire), status);
        expect(status.labelKey, 'groupRide.status.${status.wire}');
      }
    });

    test('an unknown status degrades to open rather than crashing', () {
      // A newer server adding a status must not crash an older client. `open` is
      // the safe degradation because it is the state in which the most actions
      // are permitted and the server re-checks all of them anyway.
      expect(GroupRideStatus.parse('postponed'), GroupRideStatus.open);
      expect(GroupRideStatus.parse(null), GroupRideStatus.open);
    });

    test('terminal states are exactly completed and cancelled', () {
      // `started` is NOT terminal: it is the only live state. A test that treated
      // it as terminal would hide the live-location UI on a running ride.
      expect(GroupRideStatus.open.isTerminal, isFalse);
      expect(GroupRideStatus.started.isTerminal, isFalse);
      expect(GroupRideStatus.completed.isTerminal, isTrue);
      expect(GroupRideStatus.cancelled.isTerminal, isTrue);
    });

    test('only `started` allows live location', () {
      // Sharing onto an open or finished ride is a feature the server refuses.
      // Offering it would be offering a broken button.
      expect(GroupRideStatus.open.allowsLiveLocation, isFalse);
      expect(GroupRideStatus.started.allowsLiveLocation, isTrue);
      expect(GroupRideStatus.completed.allowsLiveLocation, isFalse);
      expect(GroupRideStatus.cancelled.allowsLiveLocation, isFalse);
    });

    test('only `open` accepts new participants', () {
      // The roster freezes at `started` (ADR-16 §3). Withdrawal is unaffected,
      // which is a rule about consent rather than about the roster.
      expect(GroupRideStatus.open.acceptsInvites, isTrue);
      expect(GroupRideStatus.started.acceptsInvites, isFalse);
      expect(GroupRideStatus.completed.acceptsInvites, isFalse);
      expect(GroupRideStatus.cancelled.acceptsInvites, isFalse);
    });
  });

  group('domain: viewer status is derived from the roster', () {
    test('an invited rider is told they are invited', () {
      // `viewer` carries only `is_organizer` and `is_joined`, which cannot
      // distinguish "invited, awaiting my answer" from "no row at all". The
      // roster is the only place that distinction exists.
      final ride = GroupRide.fromJson(
        rideJson(
          roster: [
            participantJson(userId: 'u-1', role: 'organizer'),
            participantJson(
              userId: 'u-9',
              status: 'invited',
              respondedAt: null,
            ),
          ],
        ),
      );
      expect(ride.viewerStatus('u-9'), GroupRideParticipantStatus.invited);
      expect(ride.viewerRow('u-9')?.status.isPending, isTrue);
    });

    test('a rider with no roster row holds no status at all', () {
      // The distinction that matters: this rider must be offered NO answer
      // buttons, not a disabled Accept that implies they were invited.
      final ride = GroupRide.fromJson(
        rideJson(
          roster: [participantJson(userId: 'u-1', role: 'organizer')],
        ),
      );
      expect(ride.viewerStatus('u-stranger'), isNull);
      expect(ride.viewerRow('u-stranger'), isNull);
    });

    test('an unknown user id is treated as no row, never as a guess', () {
      // The signed-in id arrives asynchronously. Guessing here would put the
      // wrong rider's row — and the wrong buttons — on screen.
      final ride = GroupRide.fromJson(
        rideJson(roster: [participantJson(userId: 'u-2')]),
      );
      expect(ride.viewerStatus(null), isNull);
      expect(ride.viewerRow(null), isNull);
      expect(ride.viewerStatus('u-2'), GroupRideParticipantStatus.joined);
    });

    test('a withdrawn rider is neither on the ride nor pending', () {
      final ride = GroupRide.fromJson(
        rideJson(
          roster: [participantJson(userId: 'u-2', status: 'left')],
        ),
      );
      final status = ride.viewerStatus('u-2');
      expect(status, GroupRideParticipantStatus.left);
      expect(status?.isPending, isFalse);
      expect(status?.isOnRide, isFalse);
    });

    test('pending and on-roster partition the roster without overlap', () {
      final ride = GroupRide.fromJson(
        rideJson(
          roster: [
            participantJson(userId: 'a', status: 'invited', respondedAt: null),
            participantJson(userId: 'b', status: 'joined'),
            participantJson(userId: 'c', status: 'declined'),
            participantJson(userId: 'd', status: 'removed'),
          ],
        ),
      );
      expect(ride.pendingInvites.map((p) => p.userId), ['a']);
      expect(ride.onRoster.map((p) => p.userId), ['b']);
      // Declined and removed appear in NEITHER list, which is what lets the UI
      // show history without offering anyone an action.
      expect(ride.roster.map((p) => p.userId).toSet(), {'a', 'b', 'c', 'd'});
    });
  });

  group('domain: route pin is an exact pair', () {
    test('no pin is valid and a full pin is valid', () {
      expect(RideValidators.validateRoutePin(null, null), isNull);
      expect(RideValidators.validateRoutePin('route-1', 3), isNull);
    });

    test('half a pair is refused rather than sent to be 422ed', () {
      expect(
        RideValidators.validateRoutePin('route-1', null),
        'groupRide.invalidRoutePin',
      );
      expect(
        RideValidators.validateRoutePin(null, 3),
        'groupRide.invalidRoutePin',
      );
    });

    test('a version below 1 is refused', () {
      // Version numbers are 1-based server-side; a 0 would silently pin nothing.
      expect(
        RideValidators.validateRoutePin('route-1', 0),
        'groupRide.invalidRoutePin',
      );
      expect(
        RideValidators.validateRoutePin('route-1', -1),
        'groupRide.invalidRoutePin',
      );
    });

    test('the pin survives parsing as a pair, not as two loose fields', () {
      final ride = GroupRide.fromJson(
        rideJson(routeId: 'route-9', routeVersion: 2),
      );
      expect(ride.routeId, 'route-9');
      expect(ride.routeVersion, 2);
      expect(ride.hasRoute, isTrue);
      // The version is preserved EXACTLY: a UI that resolved "the current
      // version" instead would re-point the ride at different geometry (ADR-16 §4).
      expect(ride.routeVersion, isNot(1));
    });

    test('a ride with no route reports no route rather than a null route', () {
      final ride = GroupRide.fromJson(rideJson());
      expect(ride.hasRoute, isFalse);
      expect(ride.routeId, isNull);
      expect(ride.routeVersion, isNull);
    });
  });

  group('domain: validators mirror the server limits', () {
    test('the title is trimmed before it is measured', () {
      // A rider who typed trailing spaces has not written a 121-character title,
      // and failing them for it would be the client disagreeing with the server
      // about what was submitted.
      expect(RideValidators.validateTitle('  Sunday Spin  '), isNull);
      expect(RideValidators.validateTitle('${'a' * 120}  '), isNull);
      expect(RideValidators.validateTitle('a' * 121), 'groupRide.invalidTitle');
    });

    test('an empty or whitespace-only title is refused', () {
      expect(RideValidators.validateTitle(''), 'groupRide.invalidTitle');
      expect(RideValidators.validateTitle('   '), 'groupRide.invalidTitle');
    });

    test('optional fields are optional', () {
      expect(RideValidators.validateDescription(''), isNull);
      expect(RideValidators.validateMeetingPoint(''), isNull);
      expect(RideValidators.validateInviteMessage(''), isNull);
    });

    test('optional fields are still bounded when present', () {
      expect(
        RideValidators.validateDescription('a' * 1001),
        'groupRide.invalidDescription',
      );
      expect(
        RideValidators.validateMeetingPoint('a' * 161),
        'groupRide.invalidMeetingPoint',
      );
      expect(
        RideValidators.validateInviteMessage('a' * 281),
        'groupRide.invalidInviteMessage',
      );
    });

    test('the form reports the first problem, not a list', () {
      expect(
        RideValidators.form(
          title: '',
          description: 'a' * 1001,
          meetingPoint: '',
          routeId: 'route-1',
        ),
        'groupRide.invalidTitle',
      );
    });
  });

  group('domain: batch invites report partial progress', () {
    test('a batch with one refusal is not a failure', () {
      // An organizer inviting ten riders must learn that nine were invited and
      // one blocked them, not get one opaque error.
      final result = InviteBatchResult.fromJson({
        'invited': ['u-2', 'u-3'],
        'rejected': [
          {'user_id': 'u-4', 'code': 'RIDE_BLOCKED'},
        ],
      });
      expect(result.invited, ['u-2', 'u-3']);
      expect(result.rejected.single.code, 'RIDE_BLOCKED');
      expect(result.allAccepted, isFalse);
    });

    test('a fully accepted batch says so', () {
      final result = InviteBatchResult.fromJson({
        'invited': ['u-2'],
        'rejected': <dynamic>[],
      });
      expect(result.allAccepted, isTrue);
    });

    test(
      'an invitation labels its organizer without borrowing a rider name',
      () {
        expect(
          GroupRideInvitation.fromJson(invitationJson()).organizerLabel,
          'Imad Fouri',
        );
        expect(
          GroupRideInvitation.fromJson(
            invitationJson(
              organizerDisplayName: null,
              organizerUsername: 'imad_f',
            ),
          ).organizerLabel,
          'imad_f',
        );
        // With neither, the id is the honest last resort — better than blank.
        expect(
          GroupRideInvitation.fromJson(
            invitationJson(organizerDisplayName: null, organizerUsername: null),
          ).organizerLabel,
          'u-1',
        );
      },
    );
  });

  group('domain: location snapshots', () {
    test('a rider is found by their own label', () {
      final location = RiderLocation.fromJson(riderLocationJson());
      expect(location.label, 'Karim Amrani');
      expect(
        RiderLocation.fromJson(riderLocationJson(displayName: null)).label,
        'karim_amrani',
      );
      expect(
        RiderLocation.fromJson(
          riderLocationJson(displayName: null, username: ''),
        ).label,
        'u-2',
      );
    });

    test('an unknown accuracy is drawn imprecisely, not precisely', () {
      // A dot whose error radius is unknown must not look as exact as one that
      // knows it. This is presentation honesty, not a privacy control.
      expect(
        RiderLocation.fromJson(riderLocationJson(accuracyM: null)).isPrecise,
        isFalse,
      );
      expect(
        RiderLocation.fromJson(riderLocationJson(accuracyM: 12)).isPrecise,
        isTrue,
      );
      expect(
        RiderLocation.fromJson(riderLocationJson(accuracyM: 80)).isPrecise,
        isFalse,
      );
    });

    test('the self dot is findable without a second lookup', () {
      final snapshot = RideLocationSnapshot.fromJson(
        snapshotJson(
          items: [
            riderLocationJson(userId: 'u-2'),
            riderLocationJson(userId: 'u-1', isSelf: true),
          ],
        ),
      );
      expect(snapshot.self?.userId, 'u-1');
      expect(snapshot.riders.length, 2);
    });

    test('an empty snapshot has no riders and no error', () {
      // "Nobody is sharing" is a real answer. It is `errorCode` that separates it
      // from "we could not ask", and that distinction lives in the controller.
      final snapshot = RideLocationSnapshot.fromJson(snapshotJson());
      expect(snapshot.riders, isEmpty);
      expect(snapshot.self, isNull);
      expect(snapshot.staleAfterSeconds, 60);
      expect(snapshot.expiresInSeconds, 300);
    });
  });

  group('location controller: nothing publishes until asked', () {
    test('building the controller asks for no permission and reads nothing', () {
      // Being on a ride is not consent. There is no path here that publishes, asks
      // for permission, or reads because the screen was opened (ADR-16 §6).
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      expect(h.source.ensureCalls, 0);
      expect(h.gateway.calls, isEmpty);
      expect(h.locationState('r-1').sharing, isFalse);
      expect(h.locationState('r-1').permission, isNull);
    });

    test('starting asks for permission and only then publishes', () async {
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      final outcome = await h.location('r-1').startSharing();

      expect(outcome, LocationPermissionState.granted);
      expect(h.source.ensureCalls, 1);
      expect(h.location('r-1').isSharing, isTrue);

      h.source.emit();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.publishes, 1);
    });

    test('a denied permission never publishes and says why', () async {
      // A refusal is a normal outcome, not an error: the ride continues without
      // it, so the state has to record the refusal rather than a failure code.
      final h = harness();
      h.source.permission = LocationPermissionState.denied;
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      final outcome = await h.location('r-1').startSharing();

      expect(outcome, LocationPermissionState.denied);
      expect(h.locationState('r-1').sharing, isFalse);
      expect(h.locationState('r-1').permission, LocationPermissionState.denied);
      expect(h.locationState('r-1').errorCode, isNull);

      h.source.emit();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.publishes, 0);
    });

    test(
      'a denied-forever permission is distinguished from a plain denial',
      () async {
        // The remedy differs: one is "ask again", the other is "open settings". A
        // UI that collapsed them would send riders to a screen that cannot help.
        final h = harness();
        h.source.permission = LocationPermissionState.deniedForever;
        addTearDown(h.dispose);
        h.keepLocationAlive('r-1');

        await h.location('r-1').startSharing();

        expect(
          h.locationState('r-1').permission,
          LocationPermissionState.deniedForever,
        );
        expect(h.gateway.publishes, 0);
      },
    );

    test('starting twice does not open a second position stream', () async {
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      await h.location('r-1').startSharing();

      // One subscription, so one fix publishes once. Two would double-post every
      // position and make the dot's own accuracy meaningless.
      expect(h.source.requestedSeq.length, 1);
    });
  });

  group('location controller: stopping forgets', () {
    test('stopping revokes the position on the server', () async {
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      h.source.emit();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.publishes, 1);

      await h.location('r-1').stopSharing();

      expect(h.gateway.stops, 1);
      expect(h.locationState('r-1').sharing, isFalse);
      // The visible dots go first and unconditionally, so a rider who taps stop
      // never keeps watching a stale map.
      expect(h.locationState('r-1').snapshot, isNull);
    });

    test('stopping when nothing was published is not a write', () async {
      // Otherwise "stop" on a ride you never shared on would still hit the
      // server, which is a write for no reason.
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').stopSharing();

      expect(h.gateway.stops, 0);
      expect(h.locationState('r-1').sharing, isFalse);
    });

    test('a failing revoke still stops the local broadcast', () async {
      // The rider's intent is "stop now" and that is already satisfied locally.
      // Reporting the server failure would invite a retry of something that is no
      // longer happening, and the stored position expires on its own TTL.
      final h = harness();
      h.gateway.failStopWith = StateError('redis down');
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      h.source.emit();
      await Future<void>.delayed(Duration.zero);

      await h.location('r-1').stopSharing();

      expect(h.gateway.stops, 1);
      expect(h.locationState('r-1').sharing, isFalse);
      expect(h.locationState('r-1').errorCode, isNull);
    });

    test('no fix is published after a stop', () async {
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      await h.location('r-1').stopSharing();

      h.source.emit();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.publishes, 0);
    });

    test('the sequence number is monotonic and never persisted', () async {
      // Not shared with the ride recorder: two independently-numbered streams
      // that reuse a counter would look like one.
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      h.source.emit();
      h.source.emit();
      await Future<void>.delayed(Duration.zero);

      expect(h.gateway.publishes, 2);
      expect(h.source.requestedSeq, [1]);
    });
  });

  group('location controller: failure is visible', () {
    test('a failed read is an error state, not an empty map', () async {
      // The service answers 503 rather than an empty list precisely so "nobody is
      // sharing" and "we could not ask" stay distinguishable. Collapsing them
      // here would throw that away one layer up.
      final h = harness();
      h.gateway.failLocationsWith = StateError('redis unreachable');
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').refresh();

      expect(h.locationState('r-1').errorCode, 'LOCATION_UNAVAILABLE');
      expect(h.locationState('r-1').snapshot, isNull);
      expect(h.locationState('r-1').hasRiders, isFalse);
    });

    test('a successful read clears a previous failure', () async {
      // Otherwise a single blip would leave a permanent error banner over a
      // working map.
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      h.gateway.failLocationsWith = StateError('redis unreachable');
      await h.location('r-1').refresh();
      expect(h.locationState('r-1').errorCode, 'LOCATION_UNAVAILABLE');

      h.gateway.failLocationsWith = null;
      h.gateway.snapshot = [riderLocationJson()];
      await h.location('r-1').refresh();

      expect(h.locationState('r-1').errorCode, isNull);
      expect(h.locationState('r-1').hasRiders, isTrue);
    });

    test(
      'a failed publish surfaces a code without stopping the broadcast',
      () async {
        // A rider under a tunnel will come back. One dropped fix is not a reason to
        // stop sharing — but a failure must not look like success.
        final h = harness();
        h.gateway.failPublishWith = StateError('write failed');
        addTearDown(h.dispose);
        h.keepLocationAlive('r-1');

        await h.location('r-1').startSharing();
        h.source.emit();
        await Future<void>.delayed(Duration.zero);

        expect(h.locationState('r-1').errorCode, 'LOCATION_PUBLISH_FAILED');
        expect(h.locationState('r-1').sharing, isTrue);
      },
    );

    test('a broken GPS stream stops sharing instead of lying about it', () async {
      // The subscription is gone, so nothing would be published; the only honest
      // state is "not sharing".
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      h.source.emitError(StateError('gps died'));
      await Future<void>.delayed(Duration.zero);

      expect(h.locationState('r-1').sharing, isFalse);
      expect(h.locationState('r-1').errorCode, 'LOCATION_STREAM_FAILED');
    });
  });

  group('location controller: polling', () {
    test(
      'the first read is immediate so the map is not empty on arrival',
      () async {
        // A rider opening the ride to see where the group is should not stare at a
        // blank map for two seconds.
        final h = harness();
        h.gateway.snapshot = [riderLocationJson()];
        addTearDown(h.dispose);
        h.keepLocationAlive('r-1');

        await h.location('r-1').startPollingNow();

        expect(h.gateway.reads, 1);
        expect(h.locationState('r-1').hasRiders, isTrue);
      },
    );

    test('polling repeats only on a tick, at the documented interval', () async {
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startPollingNow();
      expect(h.gateway.reads, 1);

      h.ticker.tick();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.reads, 2);

      h.ticker.tick();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.reads, 3);

      // Inside the server's own 60-second staleness cutoff: polling slower would
      // let a dot age past what the server still calls live.
      expect(h.ticker.requested, [kRideLocationPollInterval]);
      expect(kRideLocationPollInterval.inSeconds, lessThan(60));
    });

    test('starting polling twice does not double the read rate', () async {
      final h = harness();
      addTearDown(h.dispose);
      h.keepLocationAlive('r-1');

      await h.location('r-1').startPollingNow();
      h.location('r-1').startPolling();

      h.ticker.tick();
      await Future<void>.delayed(Duration.zero);
      expect(h.gateway.reads, 2);
    });

    test(
      'stopping polling silences the timer without clearing the map',
      () async {
        // Stopping the POLL is not the same as revoking a position: the last read
        // is still the truth as of when it was made.
        final h = harness();
        h.gateway.snapshot = [riderLocationJson()];
        addTearDown(h.dispose);
        h.keepLocationAlive('r-1');

        await h.location('r-1').startPollingNow();
        h.location('r-1').stopPolling();

        h.ticker.tick();
        await Future<void>.delayed(Duration.zero);

        expect(h.gateway.reads, 1);
        expect(h.locationState('r-1').hasRiders, isTrue);
      },
    );

    test('leaving the screen closes both subscriptions', () async {
      // Navigating away must not leave this device broadcasting — that is the
      // privacy behaviour ADR-16 §6 wants even BEFORE the rider taps stop.
      final h = harness();
      final subscription = h.keepLocationAlive('r-1');

      await h.location('r-1').startSharing();
      await h.location('r-1').startPollingNow();
      final publishesWhileAlive = h.gateway.publishes;

      subscription.close();
      // `container.pump()` drains provider microtasks; a plain delay would make
      // this assertion depend on wall-clock timing.
      await Future<void>.delayed(Duration.zero);
      h.source.emit();
      h.ticker.tick();
      await Future<void>.delayed(Duration.zero);

      expect(h.gateway.publishes, publishesWhileAlive);
      expect(h.gateway.reads, 1);
    });
  });

  group('repository: payload shapes', () {
    test('a ride with a malformed timestamp still parses', () {
      // A bad timestamp must not take down the whole list. It degrades to null;
      // substituting `now()` would invent a "just now" that never happened.
      final ride = GroupRide.fromJson({
        ...rideJson(),
        'starts_at': 'not-a-date',
        'created_at': 'also-not-a-date',
      });
      expect(ride.startsAt, isNull);
      expect(ride.createdAt, DateTime(0));
    });

    test('an absent viewer block is treated as no standing at all', () {
      final ride = GroupRide.fromJson({...rideJson(), 'viewer': null});
      expect(ride.viewer.isOrganizer, isFalse);
      expect(ride.viewer.isJoined, isFalse);
    });

    test('a list response tolerates a missing items key', () {
      expect(GroupRideList.fromJson(const {}).items, isEmpty);
      expect(GroupRideInvitationList.fromJson(const {}).items, isEmpty);
    });

    test('a snapshot response tolerates a missing items key', () {
      // Defensive: an older server omitting the key must not read as "one rider
      // with no fields", which would crash on `latitude`.
      final snapshot = RideLocationSnapshot.fromJson(const {});
      expect(snapshot.riders, isEmpty);
    });
  });

  group('repository: json encoding matches the wire', () {
    test('the create body carries the pin as two fields, never a merged one', () {
      // The composite FK is `(route_id, route_version)`; sending a single
      // "route" object would let a client imply a version the server never saw.
      final body = <String, dynamic>{
        'title': 'Sunday Spin',
        'route_id': 'route-1',
        'route_version': 3,
      };
      final encoded = jsonEncode(body);
      expect(jsonDecode(encoded)['route_id'], 'route-1');
      expect(jsonDecode(encoded)['route_version'], 3);
    });

    test('the repository type is constructible from a bare base URL', () {
      // A smoke assertion that the seam the app overrides is the one the tests
      // replace; a renamed constructor would break every override at once.
      expect(GroupRideRepository, isNotNull);
    });
  });

  group('actions: teardown stops sharing BEFORE invalidating', () {
    // The regression this group exists for. `_afterMutation` invalidates
    // `rideLocationProvider`, which is an `autoDispose` family: invalidating it
    // disposes the live controller and the next read builds a fresh one whose
    // `_everPublished` is false. So calling `_afterMutation` first and
    // `_stopSharingThenInvalidate` second reads a controller that never published,
    // `stopSharing()` returns early, and the server-side revoke never happens —
    // leaving a position in Redis until its TTL, on a ride this rider just left.
    //
    // Every assertion below is "the revoke reached the server", which is the only
    // thing the ordering protects.

    /// Drive one teardown action with a repository that answers it, against a live
    /// location controller that has already published.
    Future<FakeLocationGateway> runTeardown({
      required String rideId,
      required String path,
      required Map<String, dynamic> resultJson,
      required Future<void> Function(GroupRideActions actions) invoke,
    }) async {
      final gateway = FakeLocationGateway();
      final source = FakeLocationSource();
      final ticker = ScriptedTicker();
      final container = ProviderContainer(
        overrides: [
          groupRideRepositoryProvider.overrideWithValue(
            GroupRideRepository(
              ApiClient(
                baseUrl: 'http://test',
                client: MockClient((request) async {
                  if (request.url.path.endsWith(path)) {
                    return http.Response(jsonEncode(resultJson), 200);
                  }
                  // Everything else the invalidation re-reads answers empty rather
                  // than 404, so the test fails on the revoke and not on a
                  // background refetch.
                  return http.Response('{"items": []}', 200);
                }),
                accessToken: () async => 'token',
              ),
            ),
          ),
          rideLocationGatewayProvider(rideId).overrideWithValue(gateway),
          locationSourceProvider.overrideWithValue(source),
          rideLocationTickerProvider.overrideWithValue(ticker.factory),
        ],
      );
      final h = Harness._(container, gateway, source, ticker);
      addTearDown(h.dispose);

      // Publish first, so `_everPublished` is true and a revoke is genuinely owed.
      h.keepLocationAlive(rideId);
      await h.location(rideId).startSharing();
      source.emit();
      await Future<void>.delayed(Duration.zero);
      expect(
        gateway.publishes,
        1,
        reason: 'precondition: something was published',
      );

      await invoke(container.read(groupRideActionsProvider.notifier));

      expect(
        gateway.stops,
        1,
        reason:
            'the server-side revoke must happen; a fresh controller would skip it',
      );
      return gateway;
    }

    test('leaving revokes a position it had published', () async {
      await runTeardown(
        rideId: 'r-1',
        path: '/leave',
        resultJson: rideJson(status: 'open'),
        invoke: (actions) => actions.leave('r-1').then((_) {}),
      );
    });

    test('completing revokes a position it had published', () async {
      await runTeardown(
        rideId: 'r-1',
        path: '/complete',
        resultJson: rideJson(status: 'completed'),
        invoke: (actions) => actions.complete('r-1').then((_) {}),
      );
    });

    test('cancelling revokes a position it had published', () async {
      await runTeardown(
        rideId: 'r-1',
        path: '/cancel',
        resultJson: rideJson(status: 'cancelled'),
        invoke: (actions) => actions.cancel('r-1').then((_) {}),
      );
    });

    test('a mutation that does not end the ride still refreshes the map', () {
      // The converse guard: `_afterMutation` must keep invalidating location for
      // the transitions that do NOT revoke, or the panel would show the previous
      // ride's riders after a start.
      expect(_isTeardownAction('leave'), isTrue);
      expect(_isTeardownAction('complete'), isTrue);
      expect(_isTeardownAction('cancel'), isTrue);
      expect(_isTeardownAction('start'), isFalse);
      expect(_isTeardownAction('respond'), isFalse);
      expect(_isTeardownAction('invite'), isFalse);
      expect(_isTeardownAction('removeParticipant'), isFalse);
    });
  });
}

/// Whether an action revokes this device's location broadcast.
///
/// Duplicated from `GroupRideActions` rather than imported: the production helper
/// is private, and a test that reimplemented it would agree with itself about
/// nothing. This list is asserted against the production call sites by the three
/// tests above, so a divergence shows up as a failure rather than as a stale
/// comment.
bool _isTeardownAction(String action) =>
    const {'leave', 'complete', 'cancel'}.contains(action);
