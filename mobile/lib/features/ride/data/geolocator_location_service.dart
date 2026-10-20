import 'dart:async';

import 'package:geolocator/geolocator.dart';

import '../domain/gps_processor.dart';
import '../domain/location_source.dart';

class GeolocatorLocationService implements LocationSource {
  @override
  Future<LocationPermissionState> ensurePrecisePermission() async {
    bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      return LocationPermissionState.serviceDisabled;
    }

    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied) {
      return LocationPermissionState.denied;
    }
    if (permission == LocationPermission.deniedForever) {
      return LocationPermissionState.deniedForever;
    }
    if (permission == LocationPermission.whileInUse ||
        permission == LocationPermission.always) {
      if (permission == LocationPermission.whileInUse) {
        // still ok for foreground, but background may be limited
        final background = await Geolocator.requestPermission();
        if (background == LocationPermission.always) {
          // background enabled
        }
      }
      return LocationPermissionState.granted;
    }
    return LocationPermissionState.denied;
  }

  @override
  Future<bool> hasBackgroundPermission() async {
    final p = await Geolocator.checkPermission();
    return p == LocationPermission.always;
  }

  @override
  Stream<GpsObservation> positions({
    required TrackingPolicy policy,
    required int Function() nextSeq,
    String? foregroundTitle,
    String? foregroundText,
  }) {
    final settings = AndroidSettings(
      accuracy: policy.accuracy,
      distanceFilter: policy.distanceFilterM,
      forceLocationManager: false,
      intervalDuration: const Duration(seconds: 1),
      foregroundNotificationConfig: ForegroundNotificationConfig(
        notificationTitle: foregroundTitle ?? 'CycleCoach',
        notificationText: foregroundText ?? 'Recording your ride — GPS active',
      ),
    );
    return Geolocator.getPositionStream(locationSettings: settings).map((p) =>
        GpsObservation(
          seq: nextSeq(),
          lat: p.latitude,
          lon: p.longitude,
          time: p.timestamp,
          alt: p.altitude,
          accuracy: p.accuracy,
          speed: p.speed < 0 ? null : p.speed,
          heading: p.heading,
        ));
  }

  @override
  Future<void> openSettings() => Geolocator.openAppSettings();
}
