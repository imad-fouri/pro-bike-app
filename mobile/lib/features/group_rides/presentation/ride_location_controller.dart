import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../ride/domain/gps_processor.dart';
import '../../ride/domain/location_source.dart';
import '../../ride/presentation/ride_providers.dart';
import '../domain/group_ride.dart';
import 'group_ride_providers.dart';

/// How often the riders currently sharing are re-read.
///
/// Two seconds is comfortably inside the server's own 60-second staleness cutoff:
/// polling slower would let a dot age past what the server still calls live, and
/// polling faster buys nothing that window cannot already show.
const kRideLocationPollInterval = Duration(seconds: 2);

/// A reader-driven clock, so tests can step the schedule instead of waiting.
///
/// Production uses [Stream.periodic]. A test that asserted "polled twice" via a
/// short `Future.delayed` would be asserting on scheduler timing, not on this
/// code, and would be flaky on a loaded machine.
typedef RideLocationTicker = Stream<void> Function(Duration interval);

/// The production ticker.
Stream<void> rideLocationPeriodicTicker(Duration interval) =>
    Stream<void>.periodic(interval);

/// Overridden in tests to drive [RideLocationController]'s poll schedule.
final rideLocationTickerProvider = Provider<RideLocationTicker>(
  (_) => rideLocationPeriodicTicker,
);

/// Live location for ONE ride, and the opt-in that starts it.
///
/// Three rules hold here, and they are the whole privacy story (ADR-16 §6):
///
/// 1. **Nothing publishes until asked.** [startSharing] is the only thing that
///    subscribes to GPS, and it is only ever called from a button a rider
///    pressed. Being on a ride is not consent — there is no path here that
///    publishes a position because a ride started, because a teammate joined, or
///    because the screen was opened.
/// 2. **Nothing is stored.** A published position is a request body and nothing
///    else. There is no list of past fixes, no local queue, no retry buffer: if a
///    ping fails it is dropped, because a durable local copy of where somebody
///    was is exactly the accident §6 refuses to have.
/// 3. **Failure is visible, not silent.** A failed publish sets [errorCode]
///    rather than being swallowed, and a failed read is an error state rather
///    than an empty map.
class RideLocationController extends Notifier<RideLocationState> {
  RideLocationController({required this.rideId});

  /// The ride this controller is scoped to.
  ///
  /// Carried in the constructor rather than read from a provider argument: a
  /// family notifier is built before its dependencies exist, so the ride id has
  /// to come in from outside.
  final String rideId;

  late final GroupRideLocationGateway _gateway;
  late final LocationSource _location;
  late final RideLocationTicker _ticker;

  StreamSubscription<GpsObservation>? _positions;
  StreamSubscription<void>? _poll;

  /// Monotonic counter for [LocationSource.positions]' `nextSeq`.
  ///
  /// Not persisted and not shared with the ride recorder: this is a different
  /// stream with a different meaning, and reusing a recorder's sequence would
  /// make two independently-numbered things look like one.
  int _seq = 0;

  /// Whether anything has actually been sent from this device on this ride.
  ///
  /// Distinct from [RideLocationState.sharing], which is a UI intent. This is
  /// what makes "stop" skip the revoke call when there was nothing to revoke —
  /// without it, stopping a ride you never shared on would still be a write.
  bool _everPublished = false;

  @override
  RideLocationState build() {
    _gateway = ref.watch(rideLocationGatewayProvider(rideId));
    // The same provider the ride recorder uses, so Phase 9 inherits the existing
    // permission handling instead of writing a second set of it.
    _location = ref.watch(locationSourceProvider);
    _ticker = ref.watch(rideLocationTickerProvider);
    // Leaving the screen closes both subscriptions. That is the privacy
    // behaviour ADR-16 §6 wants even before the rider taps "stop sharing":
    // navigating away must not leave this device broadcasting.
    ref.onDispose(() {
      _positions?.cancel();
      _poll?.cancel();
    });
    return const RideLocationState();
  }

  /// Whether this device is currently publishing.
  ///
  /// Exposed so a UI can branch on it without reading [state] directly, which is
  /// not part of the notifier's public surface.
  bool get isSharing => state.sharing;

  /// Ask for permission, then start publishing.
  ///
  /// Returns the permission outcome so the caller can explain itself rather than
  /// silently doing nothing. A denied permission is a normal result, not an
  /// error: the rider is told what is blocked and the ride continues without it.
  Future<LocationPermissionState> startSharing() async {
    final permission = await _location.ensurePrecisePermission();
    if (permission != LocationPermissionState.granted) {
      state = state.copyWith(
        sharing: false,
        permission: permission,
        clearError: true,
      );
      return permission;
    }
    if (state.sharing) return permission;

    state = state.copyWith(
      sharing: true,
      permission: permission,
      clearError: true,
    );
    _positions = _location
        .positions(policy: TrackingPolicy.balanced, nextSeq: () => ++_seq)
        .listen(
          _publish,
          // A broken GPS stream must not leave the UI claiming it is sharing. The
          // subscription is gone, so nothing would be published; saying so is the
          // only honest state.
          onError: (Object _) => _bail('LOCATION_STREAM_FAILED'),
        );
    return permission;
  }

  /// Stop publishing, tell the server, and stop polling.
  ///
  /// Server-side forgetting is best effort and its failure is deliberately not
  /// surfaced: the rider's intent is "stop now", it is already satisfied locally
  /// because [RideLocationState.sharing] is false and the position stream is
  /// unsubscribed, and reporting a failure would invite them to retry something
  /// that is no longer happening. The stored position expires on its own TTL.
  Future<void> stopSharing() async {
    // The visible dots go first and unconditionally: a rider who taps stop and
    // keeps seeing the map must not be left wondering whether the tap landed,
    // even in the case where there was never anything to revoke.
    state = state.copyWith(
      sharing: false,
      clearSnapshot: true,
      clearError: true,
    );
    await _teardownStreams();
    if (!_everPublished) return;
    try {
      await _gateway.stopSharing();
    } on Object {
      // See above: the local stop is the guarantee, the server call is cleanup.
    }
  }

  /// Re-read the riders currently sharing.
  ///
  /// A 503 is an ERROR STATE, not an empty map. The service refuses to answer
  /// with an empty list when Redis is unreachable precisely so "nobody is
  /// sharing" and "we could not ask" stay distinguishable, and collapsing them
  /// here would throw that away one layer up.
  Future<void> refresh() async {
    try {
      final snapshot = await _gateway.locations();
      state = state.copyWith(snapshot: snapshot, clearError: true);
    } on Object {
      state = state.copyWith(
        clearSnapshot: true,
        errorCode: 'LOCATION_UNAVAILABLE',
      );
    }
  }

  /// Begin polling. Separate from [refresh] so the first read is awaited by the
  /// caller and only the repeats are on a timer.
  void startPolling() {
    if (_poll != null) return;
    _poll = _ticker(kRideLocationPollInterval).listen((_) => refresh());
  }

  /// Poll once and then every interval.
  ///
  /// Called by the page's `initState` so the map is populated on arrival instead
  /// of after the first tick — a rider who opens the ride to see where the group
  /// is should not stare at an empty map for two seconds.
  Future<void> startPollingNow() async {
    await refresh();
    startPolling();
  }

  void stopPolling() {
    _poll?.cancel();
    _poll = null;
  }

  Future<void> _publish(GpsObservation fix) async {
    _everPublished = true;
    try {
      await _gateway.publish(
        latitude: fix.lat,
        longitude: fix.lon,
        accuracyM: fix.accuracy,
      );
    } on Object {
      // One dropped fix is not a reason to stop sharing — a rider riding under
      // a tunnel will come back — but a failure must not look like success, so
      // the code is surfaced while the broadcast continues.
      state = state.copyWith(errorCode: 'LOCATION_PUBLISH_FAILED');
    }
  }

  Future<void> _bail(String code) async {
    await _teardownStreams();
    state = state.copyWith(sharing: false, errorCode: code);
  }

  Future<void> _teardownStreams() async {
    await _positions?.cancel();
    _positions = null;
    await _poll?.cancel();
    _poll = null;
  }
}

/// What the location panel knows. All of it server- or permission-derived.
class RideLocationState {
  /// Whether this device is currently publishing.
  final bool sharing;

  /// The last permission outcome. Null until [RideLocationController.startSharing]
  /// has been called — the app never asks on its own.
  final LocationPermissionState? permission;

  /// The last successful read, or null when nothing has been read, when reading
  /// failed, or when sharing was just stopped.
  final RideLocationSnapshot? snapshot;

  /// A server or transport code, or null. Surfaced verbatim so the UI maps it
  /// through localizations rather than parsing an English sentence.
  final String? errorCode;

  const RideLocationState({
    this.sharing = false,
    this.permission,
    this.snapshot,
    this.errorCode,
  });

  RideLocationState copyWith({
    bool? sharing,
    LocationPermissionState? permission,
    RideLocationSnapshot? snapshot,
    String? errorCode,
    bool clearPermission = false,
    bool clearSnapshot = false,
    bool clearError = false,
  }) => RideLocationState(
    sharing: sharing ?? this.sharing,
    permission: clearPermission ? null : (permission ?? this.permission),
    snapshot: clearSnapshot ? null : (snapshot ?? this.snapshot),
    errorCode: clearError ? null : (errorCode ?? this.errorCode),
  );

  /// The riders currently sharing, or an empty list when there is nothing to
  /// show. Distinguishable from "could not read" by [errorCode].
  List<RiderLocation> get riders => snapshot?.riders ?? const [];

  bool get hasRiders => riders.isNotEmpty;
}

/// The three location calls, as an interface.
///
/// Exists so the controller can be tested against a fake that produces failures
/// on demand — the interesting behaviour here is what happens when Redis is down
/// or permission is denied, and neither is reachable from a real GPS in a test.
abstract class GroupRideLocationGateway {
  Future<void> publish({
    required double latitude,
    required double longitude,
    double? accuracyM,
  });

  Future<void> stopSharing();

  Future<RideLocationSnapshot> locations();
}
