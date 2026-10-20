import 'dart:io';

import 'package:cyclecoach/core/config/app_config.dart';
import 'package:flutter_test/flutter_test.dart';

/// WS-MOBILE-LIVE Phase 1: Android network-security posture.
///
/// A physical phone reaches a development API over cleartext HTTP on the LAN,
/// but release builds must stay HTTPS-only. The rules under test:
///
/// * The release (main) manifest grants INTERNET — without it a release APK
///   cannot reach the API at all — and grants NOTHING that permits cleartext.
/// * Cleartext is permitted ONLY by a network security config that lives under
///   `src/debug`, so it merges into debug builds and never into release.
/// * The app-level production gate still rejects cleartext API URLs, so even a
///   misbuilt release fails closed at startup instead of sending tokens in
///   the clear.
///
/// Paths are relative to the package root: `flutter test` runs with the
/// package directory as the working directory.
String readManifest(String variantPath) => File(variantPath).readAsStringSync();

const mainManifest = 'android/app/src/main/AndroidManifest.xml';
const debugManifest = 'android/app/src/debug/AndroidManifest.xml';
const debugNetworkConfig =
    'android/app/src/debug/res/xml/network_security_config.xml';
const mainNetworkConfig =
    'android/app/src/main/res/xml/network_security_config.xml';

void main() {
  group('release (main) manifest networking', () {
    test('grants INTERNET so a release APK can reach the API', () {
      expect(
        readManifest(mainManifest),
        contains('android.permission.INTERNET'),
      );
    });

    test('does not permit cleartext traffic', () {
      final source = readManifest(mainManifest);
      expect(source, isNot(contains('usesCleartextTraffic="true"')));
      expect(source, isNot(contains('cleartextTrafficPermitted="true"')));
    });

    test('references no network security config', () {
      expect(
        readManifest(mainManifest),
        isNot(contains('networkSecurityConfig')),
      );
    });

    test('ships no network security config in the main source set', () {
      expect(File(mainNetworkConfig).existsSync(), isFalse);
    });
  });

  group('debug-only cleartext allowance', () {
    test('debug network security config exists', () {
      expect(File(debugNetworkConfig).existsSync(), isTrue);
    });

    test('debug config permits cleartext for LAN development', () {
      expect(
        readManifest(debugNetworkConfig),
        contains('cleartextTrafficPermitted="true"'),
      );
    });

    test('debug config keeps the system trust anchors', () {
      expect(
        readManifest(debugNetworkConfig),
        contains('<certificates src="system" />'),
      );
    });

    test('debug manifest points at the debug network config', () {
      expect(
        readManifest(debugManifest),
        contains('@xml/network_security_config'),
      );
    });
  });

  group('production configuration stays fail-closed', () {
    test('a production build pointed at a cleartext LAN URL is rejected', () {
      const config = AppConfig(
        env: 'production',
        apiBase: 'http://192.168.1.50:8000',
        wsBase: 'ws://192.168.1.50:8000',
        mapTileUrl: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        mapAttribution: '© OpenStreetMap contributors',
      );
      expect(config.productionConfigProblems(), isNotEmpty);
      expect(config.validate, throwsA(isA<AppConfigError>()));
    });
  });
}
