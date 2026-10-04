import 'package:geolocator/geolocator.dart';

import 'gps_processor.dart';

/// Tracking policy (ADR-08). Position-driven, distance filter scales cost
/// with movement; stationary riders cost ~nothing.
class TrackingPolicy {
  final LocationAccuracy accuracy;
  final int distanceFilterM;
  final String name;

  const TrackingPolicy._(this.name, this.accuracy, this.distanceFilterM);

  static const precise = TrackingPolicy._('precise', LocationAccuracy.best, 5);
  static const balanced = TrackingPolicy._(
    'balanced',
    LocationAccuracy.medium,
    10,
  );
}

/// Location permission states surfaced to UI (never hidden).
enum LocationPermissionState {
  granted, // while-in-use or always
  denied,
  deniedForever,
  serviceDisabled,
  imprecise, // Android 12+ approximate — recording blocked with explanation
}

/// Platform location behind an interface: geolocator in prod, fake in tests.
abstract class LocationSource {
  Future<LocationPermissionState> ensurePrecisePermission();
  Stream<GpsObservation> positions({
    required TrackingPolicy policy,
    required int Function() nextSeq,
  });
  Future<void> openSettings();
}

class GeolocatorSource implements LocationSource {
  @override
  Future<LocationPermissionState> ensurePrecisePermission() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return LocationPermissionState.serviceDisabled;
    }
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
    }
    switch (perm) {
      case LocationPermission.denied:
        return LocationPermissionState.denied;
      case LocationPermission.deniedForever:
        return LocationPermissionState.deniedForever;
      case LocationPermission.unableToDetermine:
        return LocationPermissionState.denied;
      case LocationPermission.always:
      case LocationPermission.whileInUse:
        // Android 14+: precise flag is part of the grant; approximate grants
        // arrive as whileInUse without accuracy — verified at first fix via
        // observation accuracy, surfaced as imprecise if consistently coarse.
        return LocationPermissionState.granted;
    }
  }

  @override
  Stream<GpsObservation> positions({
    required TrackingPolicy policy,
    required int Function() nextSeq,
  }) {
    final settings = LocationSettings(
      accuracy: policy.accuracy,
      distanceFilter: policy.distanceFilterM,
    );
    return Geolocator.getPositionStream(locationSettings: settings).map(
      (p) => GpsObservation(
        seq: nextSeq(),
        lat: p.latitude,
        lon: p.longitude,
        time: p.timestamp,
        alt: p.altitude,
        accuracy: p.accuracy,
        speed: p.speed,
        heading: p.heading,
      ),
    );
  }

  @override
  Future<void> openSettings() => Geolocator.openAppSettings();
}
