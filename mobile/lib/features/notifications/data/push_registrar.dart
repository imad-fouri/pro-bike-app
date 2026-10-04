import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/notification.dart';

/// The platform's push registration, behind an interface.
///
/// This is the ONLY file in the notification feature permitted to import a push
/// SDK. Phase 8.4 ships no SDK and contacts no provider, so this implementation
/// reports "not configured" — but the interface, the injectable provider, and
/// every screen above it exist now, which is what makes adding FCM later a
/// one-file change instead of a rewrite (ADR-15 §11).
abstract class PushRegistrar {
  /// Whether real push delivery is available in this build.
  ///
  /// False in Phase 8.4 everywhere. The app must not ask for a notification
  /// permission it cannot then honour.
  bool get isAvailable;

  /// Ask for permission and obtain a token.
  ///
  /// Returns null when unavailable or refused — a refusal is an ordinary outcome,
  /// not an error, and must never break the screen that asked.
  Future<PushRegistration?> register();

  /// Forget the device token with the platform.
  Future<void> unregister() async {}
}

/// A token obtained from the platform, plus the stable identity we pair it with.
class PushRegistration {
  /// The provider-issued token. Sent once and never stored client-side.
  final String token;
  final String deviceId;
  final PushPlatform platform;
  final PushProvider provider;

  const PushRegistration({
    required this.token,
    required this.deviceId,
    required this.platform,
    required this.provider,
  });
}

/// The Phase 8.4 registrar: no provider, therefore no token.
class UnconfiguredPushRegistrar implements PushRegistrar {
  const UnconfiguredPushRegistrar();

  @override
  bool get isAvailable => false;

  @override
  Future<PushRegistration?> register() async => null;

  @override
  Future<void> unregister() async {}
}

/// Platform detection, isolated so tests can drive it.
///
/// `Platform.isAndroid` throws off-device (web, and a test VM without a binding),
/// so the check is guarded rather than assumed.
PushPlatform currentPlatform() {
  if (kIsWeb) return PushPlatform.android;
  try {
    return Platform.isIOS ? PushPlatform.ios : PushPlatform.android;
  } on Object {
    return PushPlatform.android;
  }
}

/// FUTURE: an FCM-backed registrar. It would call `FirebaseMessaging.instance`
/// to obtain a token, listen to `onTokenRefresh`, and report `isAvailable`
/// true — with no change to anything above this file.
final pushRegistrarProvider = Provider<PushRegistrar>(
  (ref) => const UnconfiguredPushRegistrar(),
);

/// Records the device ids registered on this installation.
///
/// A durable local record, not a cache: on launch the app needs to know whether
/// this device has already been registered, so it can tell a first install from
/// a token refresh without a round trip. Kept in memory here because Phase 8.4
/// has no local database for it; a durable store is FUTURE and must live in
/// secure storage alongside the tokens it guards.
class DeviceRegistrationState {
  final Set<String> registered;
  const DeviceRegistrationState({this.registered = const {}});

  bool has(String deviceId) => registered.contains(deviceId);

  /// `having` rather than `with` — `with` is a reserved word in Dart.
  DeviceRegistrationState having(String deviceId) =>
      DeviceRegistrationState(registered: {...registered, deviceId});

  DeviceRegistrationState without(String deviceId) => DeviceRegistrationState(
    registered: registered.where((d) => d != deviceId).toSet(),
  );
}

final deviceRegistrationProvider =
    NotifierProvider<DeviceRegistrationNotifier, DeviceRegistrationState>(
      DeviceRegistrationNotifier.new,
    );

class DeviceRegistrationNotifier extends Notifier<DeviceRegistrationState> {
  @override
  DeviceRegistrationState build() => const DeviceRegistrationState();
}
