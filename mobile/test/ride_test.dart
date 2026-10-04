import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/ride/data/ride_database.dart';
import 'package:cyclecoach/features/ride/data/ride_recorder.dart';
import 'package:cyclecoach/features/ride/data/rides_api.dart';
import 'package:cyclecoach/features/ride/domain/gps_processor.dart';
import 'package:cyclecoach/features/ride/domain/location_source.dart';
import 'package:cyclecoach/features/ride/domain/units.dart';
import 'package:cyclecoach/features/ride/presentation/ride_providers.dart';
import 'package:cyclecoach/features/ride/presentation/ride_recording_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:uuid/uuid.dart';

const step = 0.00009; // ~10 m latitude
final t0 = DateTime.utc(2026, 5, 1, 7);

GpsObservation obs(int seq, {double dlat = 0, int sec = -1}) {
  return GpsObservation(
    seq: seq,
    lat: 33.0 + dlat,
    lon: -6.0,
    time: t0.add(Duration(seconds: sec < 0 ? seq * 5 : sec)),
  );
}

void main() {
  _rideWidgetTests();
  _finishDrainTests();
  test('haversine + distance + moving time', () {
    expect(haversineM(0, 0, 0, 1), closeTo(111194.93, 0.5));
    final e = GpsEngine();
    e.process(obs(0));
    e.process(obs(1, dlat: step));
    e.process(obs(2, dlat: 2 * step));
    expect(e.distanceM, closeTo(20.0, 1.0));
    expect(e.movingS, closeTo(10.0, 0.1));
  });

  test('accuracy gate + duplicates + jumps + elevation', () {
    final e = GpsEngine();
    e.process(obs(0));
    expect(
      e
          .process(
            GpsObservation(
              seq: 1,
              lat: 33,
              lon: -6,
              time: t0.add(const Duration(seconds: 5)),
              accuracy: 99,
            ),
          )
          .reason,
      'bad_accuracy',
    );
    expect(e.process(obs(1, dlat: step)).accepted, isTrue);
    expect(e.process(obs(1, dlat: step)).reason, 'duplicate');
    expect(e.distanceM, closeTo(10.0, 0.5));

    final j = GpsEngine();
    j.process(obs(0));
    expect(
      j
          .process(
            GpsObservation(
              seq: 1,
              lat: 33,
              lon: -5.99,
              time: t0.add(const Duration(seconds: 1)),
            ),
          )
          .reason,
      'jump',
    );
    expect(j.distanceM, 0.0);

    final v = GpsEngine();
    v.process(GpsObservation(seq: 0, lat: 33, lon: -6, time: t0, alt: 100));
    v.process(
      GpsObservation(
        seq: 1,
        lat: 33 + step,
        lon: -6,
        time: t0.add(const Duration(seconds: 5)),
        alt: 102,
      ),
    );
    expect(v.gainM, 0.0); // +2 m noise ignored
    v.process(
      GpsObservation(
        seq: 2,
        lat: 33 + 2 * step,
        lon: -6,
        time: t0.add(const Duration(seconds: 10)),
        alt: 105,
      ),
    );
    expect(v.gainM, closeTo(5.0, 0.01));
  });

  test('pause reset kills the gap', () {
    final e = GpsEngine();
    e.process(obs(0));
    e.process(obs(1, dlat: step));
    e.resetSegment();
    final v = e.process(obs(2, dlat: step + 0.0009, sec: 3605));
    expect(v.accepted, isTrue);
    expect(v.distanceM, 0.0);
    expect(e.distanceM, closeTo(10.0, 0.5));
  });

  test('units metric/imperial + duration', () {
    expect(formatDistance(1500, imperial: false), '1.50 km');
    expect(formatDistance(1609.344, imperial: true), '1.00 mi');
    expect(formatSpeed(10, imperial: false), '36.0 km/h');
    expect(formatSpeed(10, imperial: true), contains('mph'));
    expect(formatElevation(100, imperial: true), contains('ft'));
    expect(formatDuration(3661), '1:01:01');
    expect(formatDuration(61), '01:01');
  });

  test('recorder: record → pause → resume → finish with sync', () async {
    final db = RideDatabase.forTesting(NativeDatabase.memory());
    final positions = StreamController<GpsObservation>.broadcast();
    var seq = 0;
    final fake = _FakeLocation(positions.stream);
    var uploadedPosts = 0;
    final backend = MockClient((req) async {
      final path = req.url.path;
      if (path.endsWith('/api/v1/rides')) {
        return http.Response('{"id":"srv-1","status":"recording"}', 201);
      }
      if (path.contains('/points')) {
        uploadedPosts++;
        return http.Response(
          '{"accepted":1,"rejected":[],"duplicates":0,'
          '"summary":{"distance_m":10,"elevation_gain_m":0,'
          '"elevation_loss_m":0,"moving_seconds":5,"elapsed_seconds":5,'
          '"average_speed_m_s":2,"max_speed_m_s":2,"accepted_points":1}}',
          200,
        );
      }
      if (path.endsWith('/finish')) {
        return http.Response('{"id":"srv-1","status":"completed"}', 200);
      }
      return http.Response('not found', 404);
    });
    final rec = RideRecorder(
      db: db,
      rides: RidesApi(
        ApiClient(
          baseUrl: 'http://test',
          client: backend,
          accessToken: () async => 'tok',
        ),
      ),
      location: fake,
    );
    final perm = await rec.start(bikeId: 'b1', bikeName: 'Road');
    expect(perm, LocationPermissionState.granted);
    positions.add(obs(seq++));
    positions.add(obs(seq++, dlat: step));
    await Future.delayed(const Duration(milliseconds: 100));
    await rec.pause();
    positions.add(
      obs(seq++, dlat: 2 * step, sec: 3600),
    ); // ignored while paused
    await Future.delayed(const Duration(milliseconds: 100));
    await rec.resume();
    await rec.syncNow();
    expect(uploadedPosts, greaterThanOrEqualTo(1));
    await rec.finish();
    final rows = await (db.select(db.localRides)).get();
    expect(rows.single.status, 'completed');
    final pts = await (db.select(db.localPoints)).get();
    expect(pts.where((p) => p.accepted).length, 2); // gap point never stored
    await rec.dispose();
    await db.close();
  });

  test('permission denied blocks recording', () async {
    final db = RideDatabase.forTesting(NativeDatabase.memory());
    final rec = RideRecorder(
      db: db,
      rides: RidesApi(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient((_) async {
            return http.Response('', 500);
          }),
        ),
      ),
      location: _FakeLocation(
        const Stream.empty(),
        perm: LocationPermissionState.deniedForever,
      ),
    );
    expect(
      await rec.start(bikeId: 'b', bikeName: 'b'),
      LocationPermissionState.deniedForever,
    );
    expect(await db.unfinishedRide(), isNull);
    await rec.dispose();
    await db.close();
  });

  test('unfinished ride probe finds interrupted recording', () async {
    final db = RideDatabase.forTesting(NativeDatabase.memory());
    await db
        .into(db.localRides)
        .insert(
          LocalRidesCompanion.insert(
            id: 'local-1',
            bikeId: 'b1',
            startedAt: DateTime.now(),
            updatedAt: DateTime.now(),
          ),
        );
    expect((await db.unfinishedRide())?.id, 'local-1');
    await db.close();
  });

  test('ride l10n ar', () async {
    expect(
      AppLocalizations(const Locale('ar')).get('ride.pause'),
      'إيقاف مؤقت',
    );
  });
}

/// Finish-drain regression tests: the completion state machine must never
/// strand points, mark completed before full sync, or duplicate on retry.
/// All use the real Drift schema and a fake transport — no production code.
void _finishDrainTests() {
  group('finish drain + crash recovery', () {
    test('finish drains exactly 100 points in one chunk', () async {
      final db = RideDatabase.forTesting(NativeDatabase.memory());
      final received = <String>{};
      var chunks = 0;
      final backend = _syncBackend(received: received, onChunk: () => chunks++);
      final rec = _recorder(db, backend);
      final localId = await _seedRide(db, 100);
      expect(await rec.attachForSync(localId), isTrue);

      final summary = await rec.finish();

      expect(summary, isNotNull);
      expect(received.length, 100);
      expect(chunks, 1);
      final row = await _rideRow(db, localId);
      expect(row.status, 'completed');
      expect(row.uploadedSeq, 99);
      await rec.dispose();
      await db.close();
    });

    test('finish drains 101 points across the chunk boundary', () async {
      final db = RideDatabase.forTesting(NativeDatabase.memory());
      final received = <String>{};
      var chunks = 0;
      final backend = _syncBackend(received: received, onChunk: () => chunks++);
      final rec = _recorder(db, backend);
      final localId = await _seedRide(db, 101);
      expect(await rec.attachForSync(localId), isTrue);

      expect(await rec.finish(), isNotNull);

      expect(received.length, 101);
      expect(chunks, 2);
      final row = await _rideRow(db, localId);
      expect(row.status, 'completed');
      expect(row.uploadedSeq, 100);
      await rec.dispose();
      await db.close();
    });

    test('finish drains 500 points without loss or duplicates', () async {
      final db = RideDatabase.forTesting(NativeDatabase.memory());
      final received = <String>{};
      var chunks = 0;
      final backend = _syncBackend(received: received, onChunk: () => chunks++);
      final rec = _recorder(db, backend);
      final localId = await _seedRide(db, 500);
      expect(await rec.attachForSync(localId), isTrue);

      expect(await rec.finish(), isNotNull);

      expect(received.length, 500);
      expect(chunks, 5);
      final row = await _rideRow(db, localId);
      expect(row.status, 'completed');
      expect(row.uploadedSeq, 499);
      await rec.dispose();
      await db.close();
    });

    test('finish drains 1000 points without loss or duplicates', () async {
      final db = RideDatabase.forTesting(NativeDatabase.memory());
      final received = <String>{};
      var chunks = 0;
      final backend = _syncBackend(received: received, onChunk: () => chunks++);
      final rec = _recorder(db, backend);
      final localId = await _seedRide(db, 1000);
      expect(await rec.attachForSync(localId), isTrue);

      expect(await rec.finish(), isNotNull);

      expect(received.length, 1000);
      expect(chunks, 10);
      expect((await _rideRow(db, localId)).status, 'completed');
      await rec.dispose();
      await db.close();
    });

    test('network failure leaves pending_sync, never completed', () async {
      final db = RideDatabase.forTesting(NativeDatabase.memory());
      final received = <String>{};
      final backend = _syncBackend(received: received, failPoints: true);
      final rec = _recorder(db, backend);
      final localId = await _seedRide(db, 150);
      expect(await rec.attachForSync(localId), isTrue);

      expect(await rec.finish(), isNull);

      final row = await _rideRow(db, localId);
      expect(row.status, 'pending_sync');
      expect(row.uploadedSeq, -1);
      expect(received, isEmpty);
      // The stuck ride is discoverable by both recovery probes.
      expect((await db.unfinishedRide())?.id, localId);
      expect((await db.pendingSyncRides()).map((r) => r.id), contains(localId));
      await rec.dispose();
      await db.close();
    });

    test(
      'failed chunk retried once, then recovery completes exactly once',
      () async {
        final db = RideDatabase.forTesting(NativeDatabase.memory());
        final received = <String>{};
        var failuresLeft = 1;
        final backend = _syncBackend(
          received: received,
          failPoints: false,
          failFirst: () {
            if (failuresLeft > 0) {
              failuresLeft--;
              return true;
            }
            return false;
          },
        );
        final rec = _recorder(db, backend);
        final localId = await _seedRide(db, 150);
        expect(await rec.attachForSync(localId), isTrue);

        // First chunk fails: finish must not complete.
        expect(await rec.finish(), isNull);
        expect((await _rideRow(db, localId)).status, 'pending_sync');
        await rec.dispose();

        // Simulated restart: brand-new instances against the same database,
        // network restored. The session-level recovery entry point drains it.
        final container = ProviderContainer(
          overrides: [
            rideDatabaseProvider.overrideWith((ref) async => db),
            ridesApiProvider.overrideWith(
              (ref) => RidesApi(
                ApiClient(
                  baseUrl: 'http://test',
                  client: backend,
                  accessToken: () async => 'tok',
                ),
              ),
            ),
            locationSourceProvider.overrideWithValue(
              _FakeLocation(const Stream.empty()),
            ),
          ],
        );
        final completed = await container
            .read(rideSessionProvider.notifier)
            .recoverPendingSync();

        expect(completed, 1);
        expect(received.length, 150);
        final row = await _rideRow(db, localId);
        expect(row.status, 'completed');
        expect(row.uploadedSeq, 149);
        expect(await db.pendingSyncRides(), isEmpty);
        container.dispose();
        await db.close();
      },
    );

    test(
      'kill mid-drain recovers the remaining chunks after restart',
      () async {
        final db = RideDatabase.forTesting(NativeDatabase.memory());
        final received = <String>{};
        var chunksAllowed = 1;
        final backend = _syncBackend(
          received: received,
          failPoints: false,
          failFirst: () => chunksAllowed-- <= 0,
        );
        final rec = _recorder(db, backend);
        final localId = await _seedRide(db, 250);
        expect(await rec.attachForSync(localId), isTrue);

        // One chunk lands, the rest fail: the process "dies" here.
        expect(await rec.finish(), isNull);
        expect((await _rideRow(db, localId)).uploadedSeq, 99);
        await rec.dispose();

        // Restart with a healthy network: every remaining chunk must land.
        chunksAllowed = 1000;
        final rec2 = _recorder(db, backend);
        expect(await rec2.attachForSync(localId), isTrue);
        expect(await rec2.finish(), isNotNull);

        expect(received.length, 250);
        final row = await _rideRow(db, localId);
        expect(row.status, 'completed');
        expect(row.uploadedSeq, 249);
        await rec2.dispose();
        await db.close();
      },
    );
  });
}

Future<String> _seedRide(RideDatabase db, int count) async {
  const localId = 'local-seed';
  await db
      .into(db.localRides)
      .insert(
        LocalRidesCompanion.insert(
          id: localId,
          bikeId: 'b1',
          startedAt: t0,
          updatedAt: t0,
        ),
      );
  await db.batch((b) {
    b.insertAll(db.localPoints, [
      for (var i = 0; i < count; i++)
        LocalPointsCompanion.insert(
          rideId: localId,
          pointUuid: const Uuid().v4(),
          seq: i,
          lat: 33.0 + i * step,
          lon: -6.0,
          recordedAt: t0.add(Duration(seconds: i * 5)),
        ),
    ]);
  });
  return localId;
}

Future<LocalRide> _rideRow(RideDatabase db, String localId) {
  return (db.select(
    db.localRides,
  )..where((r) => r.id.equals(localId))).getSingle();
}

RideRecorder _recorder(RideDatabase db, MockClient backend) {
  return RideRecorder(
    db: db,
    rides: RidesApi(
      ApiClient(
        baseUrl: 'http://test',
        client: backend,
        accessToken: () async => 'tok',
      ),
    ),
    location: _FakeLocation(const Stream.empty()),
  );
}

/// Fake transport speaking the real ride envelope. `failPoints` forces every
/// chunk to fail; `failFirst` fails selectively per call (for kill simulation).
MockClient _syncBackend({
  required Set<String> received,
  void Function()? onChunk,
  bool failPoints = false,
  bool Function()? failFirst,
}) {
  return MockClient((req) async {
    final path = req.url.path;
    if (path.endsWith('/api/v1/rides')) {
      return http.Response('{"id":"srv-1","status":"recording"}', 201);
    }
    if (path.contains('/points')) {
      if (failPoints || (failFirst?.call() ?? false)) {
        return http.Response(
          '{"error":{"code":"X","message":"m","details":{"code":"ERROR","message":"boom"}}}',
          500,
        );
      }
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      final points = body['points'] as List;
      for (final p in points) {
        received.add((p as Map)['client_point_uuid'] as String);
      }
      onChunk?.call();
      return http.Response(
        '{"accepted":${points.length},"rejected":[],"duplicates":0,'
        '"summary":{"distance_m":0,"elevation_gain_m":0,'
        '"elevation_loss_m":0,"moving_seconds":0,"elapsed_seconds":0,'
        '"average_speed_m_s":0,"max_speed_m_s":0,'
        '"accepted_points":${points.length}}}',
        200,
      );
    }
    if (path.endsWith('/finish')) {
      return http.Response('{"id":"srv-1","status":"completed"}', 200);
    }
    if (path.endsWith('/rides/srv-1')) {
      return http.Response('{"id":"srv-1","status":"completed"}', 200);
    }
    return http.Response('not found', 404);
  });
}

class _FixedSession extends RideSession {
  final LiveRide? fixed;
  _FixedSession(this.fixed);
  @override
  LiveRide? build() => fixed;
}

Widget _rideShell(Locale locale, LiveRide live) {
  return ProviderScope(
    overrides: [rideSessionProvider.overrideWith(() => _FixedSession(live))],
    child: Consumer(
      builder: (context, ref, _) {
        ref.watch(rideSessionProvider);
        return MaterialApp(
          locale: locale,
          supportedLocales: AppLocalizations.supported,
          localizationsDelegates: const [
            AppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const RideRecordingPage(),
        );
      },
    ),
  );
}

void _rideWidgetTests() {
  testWidgets('recording page renders RTL metrics + pause', (t) async {
    final semantics = t.ensureSemantics();
    final live = LiveRide(
      localId: 'l',
      bikeId: 'b',
      bikeName: 'Road',
      phase: RecordingPhase.recording,
      metrics: const RideMetrics(
        distanceM: 1500,
        gainM: 42,
        lossM: 0,
        movingS: 600,
        elapsedS: 700,
        avgSpeedMS: 2.5,
        maxSpeedMS: 9,
      ),
      currentSpeedMS: 3.0,
      gpsQuality: 'good',
      syncState: 'idle',
    );
    await t.pumpWidget(_rideShell(const Locale('ar'), live));
    await t.pumpAndSettle();
    final pause = find.byKey(const Key('ride-pause-button'));
    expect(pause, findsOneWidget);
    expect(find.bySemanticsLabel('إيقاف مؤقت'), findsOneWidget);
    final ctx = t.element(pause);
    expect(Directionality.of(ctx), TextDirection.rtl);
    semantics.dispose();
  });
}

class _FakeLocation implements LocationSource {
  final Stream<GpsObservation> stream;
  final LocationPermissionState perm;
  _FakeLocation(this.stream, {this.perm = LocationPermissionState.granted});

  @override
  Future<LocationPermissionState> ensurePrecisePermission() async => perm;

  @override
  Stream<GpsObservation> positions({
    required TrackingPolicy policy,
    required int Function() nextSeq,
  }) => stream;

  @override
  Future<void> openSettings() async {}
}
