import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// WS-MOBILE-LIVE Phase A+B: Android background-recording declarations.
///
/// Screen-off recording rides on the location plugin's own foreground
/// service (`GeolocatorLocationService`, type `location`), which the app
/// activates by passing a `ForegroundNotificationConfig`. The app manifest
/// therefore needs the location permissions — and must NOT declare its own
/// `<service>`, which would duplicate (or fight) the plugin's. These tests
/// pin that split of responsibilities. Paths are relative to the package
/// root: `flutter test` runs with the package directory as the working
/// directory.
String readManifest(String variantPath) => File(variantPath).readAsStringSync();

const mainManifest = 'android/app/src/main/AndroidManifest.xml';
const debugManifest = 'android/app/src/debug/AndroidManifest.xml';

void main() {
  group('location permissions for background recording', () {
    test('foreground location is declared', () {
      final source = readManifest(mainManifest);
      expect(source, contains('ACCESS_FINE_LOCATION'));
      expect(source, contains('ACCESS_COARSE_LOCATION'));
    });

    test('background location is declared for screen-off fixes', () {
      expect(
        readManifest(mainManifest),
        contains('ACCESS_BACKGROUND_LOCATION'),
      );
    });

    test('foreground-service permissions are declared', () {
      final source = readManifest(mainManifest);
      expect(source, contains('FOREGROUND_SERVICE"'));
      expect(source, contains('FOREGROUND_SERVICE_LOCATION'));
    });

    test('INTERNET stays declared for sync traffic', () {
      expect(readManifest(mainManifest), contains('INTERNET'));
    });
  });

  group('no hand-written native service', () {
    test('the app manifest declares no <service> element', () {
      // The location plugin's manifest contributes
      // GeolocatorLocationService (foregroundServiceType="location") via
      // manifest merge; a second hand-written service here would duplicate
      // it. Verified merged into the APK at build time (see PHASE_A_B).
      for (final path in [mainManifest, debugManifest]) {
        expect(
          readManifest(path),
          isNot(contains('<service')),
          reason: '$path must not declare its own service',
        );
      }
    });

    test('release stays cleartext-free', () {
      final source = readManifest(mainManifest);
      expect(source, isNot(contains('usesCleartextTraffic="true"')));
      expect(source, isNot(contains('networkSecurityConfig')));
    });
  });
}
