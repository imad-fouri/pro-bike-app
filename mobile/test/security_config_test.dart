import 'package:cyclecoach/core/config/app_config.dart';
import 'package:flutter_test/flutter_test.dart';

/// Phase 10 WS-M regression tests for mobile production configuration.
///
/// Proven against the pre-fix code: `AppConfig` had no validation and `env` was
/// read but referenced nowhere in the app, so a release build missing
/// `--dart-define=API_BASE=...` started up silently pointed at
/// `http://localhost:8000` — cleartext HTTP carrying the access token, the
/// refresh token and every recorded GPS coordinate, to a host that is not the
/// API. The app looked and behaved like a working build.
AppConfig build({
  String env = 'production',
  String apiBase = 'https://api.cyclecoach.app',
  String wsBase = 'wss://api.cyclecoach.app',
}) => AppConfig(
  env: env,
  apiBase: apiBase,
  wsBase: wsBase,
  mapTileUrl: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  mapAttribution: '© OpenStreetMap contributors',
);

void main() {
  group('a correctly configured release build', () {
    test('passes with no problems', () {
      expect(build().productionConfigProblems(), isEmpty);
    });

    test('validate does not throw', () {
      expect(build().validate, returnsNormally);
    });

    test('recognises both production and prod', () {
      expect(build(env: 'production').isProduction, isTrue);
      // A release pipeline that passes APP_ENV=prod must not skip the checks;
      // that is the same fail-open mistake the backend gate fixed.
      expect(build(env: 'prod').isProduction, isTrue);
      expect(build(env: 'PRODUCTION').isProduction, isTrue);
    });
  });

  group('cleartext transport is refused', () {
    test('an http API base is rejected in production', () {
      final problems = build(
        apiBase: 'http://api.cyclecoach.app',
      ).productionConfigProblems();
      expect(problems, isNotEmpty);
      expect(problems.join(), contains('API_BASE'));
      expect(problems.join(), contains('cleartext'));
    });

    test('a ws socket base is rejected in production', () {
      final problems = build(
        wsBase: 'ws://api.cyclecoach.app',
      ).productionConfigProblems();
      expect(problems, isNotEmpty);
      expect(problems.join(), contains('WS_BASE'));
    });

    test('validate throws rather than degrading', () {
      // Degrading is the failure: a release build that quietly downgraded to
      // cleartext would be indistinguishable from a working one.
      expect(
        () => build(apiBase: 'http://api.cyclecoach.app').validate(),
        throwsA(isA<AppConfigError>()),
      );
    });

    test('the error names every problem at once', () {
      // An operator should see all faults in one build, not one crash per fault.
      final config = build(
        apiBase: 'http://localhost:8000',
        wsBase: 'ws://localhost:8000',
      );
      expect(config.productionConfigProblems().length, greaterThanOrEqualTo(2));
      expect(
        () => config.validate(),
        throwsA(
          isA<AppConfigError>().having(
            (e) => e.problems.length,
            'problem count',
            greaterThanOrEqualTo(2),
          ),
        ),
      );
    });
  });

  group('a development host is refused in production', () {
    test('localhost over https is still refused', () {
      // Encrypted and still wrong: the traffic goes nowhere useful.
      final problems = build(
        apiBase: 'https://localhost:8000',
      ).productionConfigProblems();
      expect(problems, isNotEmpty);
      expect(problems.join(), contains('development host'));
    });

    test('the shipped default is refused', () {
      // The exact configuration the app had before this change.
      final problems = build(
        apiBase: 'http://localhost:8000',
        wsBase: 'ws://localhost:8000',
      ).productionConfigProblems();
      expect(problems.join(), contains('localhost'));
    });

    test('127.0.0.1 is refused', () {
      expect(
        build(apiBase: 'https://127.0.0.1:8000').productionConfigProblems(),
        isNotEmpty,
      );
    });
  });

  group('an unparseable URL is refused', () {
    test('a bare host with no scheme is rejected', () {
      final problems = build(
        apiBase: 'api.cyclecoach.app',
      ).productionConfigProblems();
      expect(problems, isNotEmpty);
    });

    test('an empty base is rejected', () {
      expect(build(apiBase: '').productionConfigProblems(), isNotEmpty);
    });
  });

  group('the guard itself cannot fail open', () {
    test('an unrecognised environment is rejected in every build', () {
      // Checked in ALL environments, not only production: a typo like
      // APP_ENV=produciton would otherwise skip the production checks entirely
      // while still being treated as a release build by the store.
      for (final env in ['produciton', 'stagingg', '', 'PRODUCTION ']) {
        expect(
          build(env: env).productionConfigProblems(),
          isNotEmpty,
          reason: '"$env" must be rejected',
        );
      }
    });

    test('the environment problem is reported even in development', () {
      final problems = build(
        env: 'nonsense',
        apiBase: 'http://localhost:8000',
      ).productionConfigProblems();
      expect(problems.join(), contains('APP_ENV'));
    });
  });

  group('development and test are never gated', () {
    test('a dev build on localhost passes', () {
      // Local work and CI must not have to configure a production host. If this
      // ever fails, the gate has started blocking people who are not shipping.
      expect(
        build(
          env: 'dev',
          apiBase: 'http://localhost:8000',
          wsBase: 'ws://localhost:8000',
        ).productionConfigProblems(),
        isEmpty,
      );
    });

    test('a test build on 10.0.2.2 passes', () {
      // The Android emulator's host alias.
      expect(
        build(
          env: 'test',
          apiBase: 'http://10.0.2.2:8000',
          wsBase: 'ws://10.0.2.2:8000',
        ).productionConfigProblems(),
        isEmpty,
      );
    });

    test('a staging build on localhost passes', () {
      expect(
        build(
          env: 'staging',
          apiBase: 'http://localhost:8000',
        ).productionConfigProblems(),
        isEmpty,
      );
    });

    test('validate never throws outside production', () {
      for (final env in ['dev', 'development', 'test', 'staging']) {
        expect(
          () => build(env: env, apiBase: 'http://localhost:8000').validate(),
          returnsNormally,
          reason: env,
        );
      }
    });
  });

  group('the error message', () {
    test('carries the count and every problem', () {
      try {
        build(
          apiBase: 'http://localhost:8000',
          wsBase: 'ws://localhost:8000',
        ).validate();
        fail('expected AppConfigError');
      } on AppConfigError catch (e) {
        expect(e.toString(), contains('AppConfigError'));
        expect(e.problems, isNotEmpty);
      }
    });

    test('contains no secret material', () {
      // There are no secrets in this class today; the rule is kept for the
      // settings that arrive, and asserted so a future addition cannot skip it.
      try {
        build(apiBase: 'https://user:hunter2@api.cyclecoach.app').validate();
      } on AppConfigError catch (e) {
        expect(e.toString(), isNot(contains('hunter2')));
      }
    });
  });
}
