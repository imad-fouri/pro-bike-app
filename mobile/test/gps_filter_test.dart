import 'package:cyclecoach/features/ride/domain/gps_processor.dart';
import 'package:flutter_test/flutter_test.dart';

/// Deterministic evidence for the confidence-anchor metering rules.
///
/// Stationary jitter must never move the meters; genuine slow movement must
/// be preserved (quantized, not lost); jumps/accuracy/order rules from the
/// original engine stay intact. No live GPS here — fixed coordinates only,
/// and no claim about real-world accuracy follows from these tests.
const _lat = 33.0;
const _lon = -6.0;
final _t0 = DateTime.utc(2026, 5, 1, 7);

GpsObservation fix(
  int seq, {
  double dlat = 0,
  double dlon = 0,
  int sec = -1,
  double? accuracy,
  double? alt,
  double? speed,
}) {
  return GpsObservation(
    seq: seq,
    lat: _lat + dlat,
    lon: _lon + dlon,
    time: _t0.add(Duration(seconds: sec < 0 ? seq * 5 : sec)),
    accuracy: accuracy,
    alt: alt,
    speed: speed,
  );
}

void main() {
  group('confidence gate', () {
    test('required displacement scales with accuracy over a floor', () {
      const policy = GpsPolicy();
      expect(policy.requiredDisplacementM(null), 8.0);
      expect(policy.requiredDisplacementM(4.0), 4.0);
      expect(policy.requiredDisplacementM(2.0), 3.0);
      expect(policy.requiredDisplacementM(20.0), 20.0);
    });

    test('stationary jitter accrues nothing', () {
      final e = GpsEngine();
      e.process(fix(0, accuracy: 10));
      // ±6 m oscillation around the anchor, 5 s apart, 20 fixes.
      for (var i = 1; i <= 20; i++) {
        final v = e.process(
          fix(i, dlat: (i.isEven ? 0.000054 : -0.000054), accuracy: 10),
        );
        expect(v.accepted, isFalse, reason: 'fix $i held');
        expect(v.reason, 'stationary');
      }
      expect(e.distanceM, 0.0);
      expect(e.movingS, 0.0);
      expect(e.acceptedCount, 1);
    });

    test('genuine slow movement is preserved, not hidden', () {
      final e = GpsEngine();
      e.process(fix(0, accuracy: 5));
      // 0.5 m/s walk: 2.5 m per 5 s fix, well under the moving threshold.
      for (var i = 1; i <= 10; i++) {
        e.process(fix(i, dlat: i * 0.0000225, accuracy: 5));
      }
      // Accrued in 5 m gate-sized chunks: the full 25 m, only later.
      expect(e.distanceM, closeTo(25.0, 0.5));
      // Below the 1 m/s moving threshold: distance without moving time,
      // exactly like the pre-anchor engine.
      expect(e.movingS, 0.0);
    });

    test('noisy fixes inside the confidence radius never meter', () {
      final e = GpsEngine();
      e.process(fix(0, accuracy: 20));
      for (var i = 1; i <= 5; i++) {
        final v = e.process(fix(i, dlat: i * 0.00002, accuracy: 20));
        expect(v.reason, 'stationary');
      }
      expect(e.distanceM, 0.0);
      // A real 30 m move clears the 20 m radius and accrues in full.
      final v = e.process(fix(6, dlat: 0.00027, accuracy: 20));
      expect(v.accepted, isTrue);
      expect(e.distanceM, closeTo(30.0, 0.5));
    });
  });

  group('preserved rejection rules', () {
    test('impossible jump rejected and poisons nothing', () {
      final e = GpsEngine();
      e.process(fix(0));
      final v = e.process(fix(1, dlon: 0.01, sec: 1));
      expect(v.reason, 'jump');
      expect(e.distanceM, 0.0);
      // Next good fix is judged against the unpoisoned anchor.
      final w = e.process(fix(2, dlat: 0.00009));
      expect(w.accepted, isTrue);
      expect(e.distanceM, closeTo(10.0, 0.5));
    });

    test('poor accuracy rejected without touching state', () {
      final e = GpsEngine();
      e.process(fix(0));
      expect(
        e.process(fix(1, dlat: 0.00009, accuracy: 99)).reason,
        'bad_accuracy',
      );
      expect(e.distanceM, 0.0);
      final v = e.process(fix(2, dlat: 0.00009));
      expect(v.accepted, isTrue);
      expect(e.distanceM, closeTo(10.0, 0.5));
    });

    test('regressed timestamps and out-of-order points rejected', () {
      final e = GpsEngine();
      e.process(fix(0));
      e.process(fix(1, dlat: 0.00009));
      // Same timestamp as the previous fix, new sequence: time went nowhere.
      expect(
        e.process(fix(2, dlat: 0.00009, sec: 5)).reason,
        'time_regression',
      );
      expect(e.process(fix(0, dlat: 0.00018, sec: 20)).reason, 'out_of_order');
      expect(e.distanceM, closeTo(10.0, 0.5));
    });

    test('invalid speed rejected', () {
      final e = GpsEngine();
      e.process(fix(0));
      expect(e.process(fix(1, speed: -3)).reason, 'bad_speed');
      expect(e.process(fix(1, speed: 500)).reason, 'bad_speed');
      expect(e.distanceM, 0.0);
    });
  });

  group('segments and continuity', () {
    test('pause/resume re-anchors with zero segment distance', () {
      final e = GpsEngine();
      e.process(fix(0));
      e.process(fix(1, dlat: 0.00009));
      e.resetSegment();
      final v = e.process(fix(2, dlat: 0.0009, sec: 3605));
      expect(v.accepted, isTrue);
      expect(v.distanceM, 0.0);
      expect(e.distanceM, closeTo(10.0, 0.5));
    });

    test('distance continuity across holds and accrues', () {
      final e = GpsEngine();
      e.process(fix(0, accuracy: 8));
      e.process(fix(1, dlat: 0.000054, accuracy: 8)); // held
      e.process(fix(2, dlat: 0.00009, accuracy: 8)); // accrues 10 m
      e.process(fix(3, dlat: 0.0001, accuracy: 8)); // held
      e.process(fix(4, dlat: 0.00018, accuracy: 8)); // accrues 10 m
      expect(e.distanceM, closeTo(20.0, 0.5));
    });

    test('elapsed spans holds while moving counts confident spans', () {
      final e = GpsEngine();
      e.process(fix(0));
      e.process(fix(1, dlat: 0.00009));
      final m = e.metrics();
      expect(m.elapsedS, 5);
      expect(m.movingS, 5);
      expect(m.avgSpeedMS, closeTo(2.0, 0.1));
    });
  });

  group('elevation gating', () {
    test('altitude drift while stationary gains nothing', () {
      final e = GpsEngine();
      e.process(fix(0, alt: 100, accuracy: 10));
      // Altitude wanders ±2 m; position never clears the gate.
      e.process(fix(1, alt: 102, accuracy: 10));
      e.process(fix(2, alt: 99, accuracy: 10));
      e.process(fix(3, alt: 101, accuracy: 10));
      expect(e.gainM, 0.0);
      expect(e.lossM, 0.0);
    });

    test('confident climbs still accrue past the threshold', () {
      final e = GpsEngine();
      e.process(fix(0, alt: 100));
      e.process(fix(1, dlat: 0.00009, alt: 102));
      expect(e.gainM, 0.0); // +2 m noise ignored
      e.process(fix(2, dlat: 0.00018, alt: 105));
      expect(e.gainM, closeTo(5.0, 0.01));
    });
  });
}
