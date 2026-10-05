/// Flavor/env config via --dart-define. No secrets here.
///
/// Phase 10 security audit: this class had no validation at all, and `env` was
/// read but never referenced anywhere in the app. A release build that forgot
/// `--dart-define=API_BASE=...` therefore started up silently pointed at
/// `http://localhost:8000` — cleartext HTTP carrying the access token, the
/// refresh token and every GPS coordinate the rider records, sent to a host that
/// is not the API.
///
/// The failure was silent in the worst way: the app worked, logged in, and
/// recorded rides. Nothing about the running app reveals that its traffic is
/// going nowhere in the clear.
///
/// So production config fails closed here too, with the same vocabulary as the
/// backend's `Settings.production_config_problems()`: collect every problem,
/// name the setting, never the secret, and raise rather than degrade.
class AppConfig {
  final String env;
  final String apiBase;
  final String wsBase;
  final String mapTileUrl;
  final String mapAttribution;
  const AppConfig({
    required this.env,
    required this.apiBase,
    required this.wsBase,
    required this.mapTileUrl,
    required this.mapAttribution,
  });

  static const current = AppConfig(
    env: String.fromEnvironment('APP_ENV', defaultValue: 'dev'),
    apiBase: String.fromEnvironment(
      'API_BASE',
      defaultValue: 'http://localhost:8000',
    ),
    wsBase: String.fromEnvironment(
      'WS_BASE',
      defaultValue: 'ws://localhost:8000',
    ),
    // Raster tiles only; attribution is rendered on every map (§30 OSM policy).
    mapTileUrl: String.fromEnvironment(
      'MAP_TILE_URL',
      defaultValue: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    ),
    mapAttribution: String.fromEnvironment(
      'MAP_ATTRIBUTION',
      defaultValue: '© OpenStreetMap contributors',
    ),
  );

  /// The one environment that must be treated as a live deployment.
  ///
  /// `prod` is accepted as an alias because a release pipeline that passes
  /// `APP_ENV=prod` must not silently skip these checks — that is the same
  /// fail-open mistake the backend gate fixed for `ENVIRONMENT=prod`.
  bool get isProduction =>
      const {'production', 'prod'}.contains(env.toLowerCase());

  /// Hosts that mean "this is a developer's own machine".
  static const _loopbackHosts = {'localhost', '127.0.0.1', '::1', '0.0.0.0'};

  bool _isLoopback(String url) {
    final host = Uri.tryParse(url)?.host ?? '';
    return _loopbackHosts.contains(host.toLowerCase());
  }

  /// Every reason this build must not be shipped as-is.
  ///
  /// Returns a list rather than throwing so a caller can surface all of them at
  /// once, and so tests can assert on the whole set. Never includes a secret —
  /// there are none in this class, and the rule is kept for the ones that arrive.
  List<String> productionConfigProblems() {
    final problems = <String>[];

    if (isProduction) {
      // 1. Cleartext. Bearer and refresh tokens, plus every recorded coordinate,
      //    travel in this URL's requests. HTTP publishes all of it.
      for (final entry in {'API_BASE': apiBase, 'WS_BASE': wsBase}.entries) {
        final uri = Uri.tryParse(entry.value);
        if (uri == null || !uri.hasScheme) {
          problems.add('${entry.key} is not a valid absolute URL');
          continue;
        }
        if (uri.scheme != 'https' && uri.scheme != 'wss') {
          problems.add(
            '${entry.key} uses ${uri.scheme}, which sends credentials in '
            'cleartext; production requires https/wss',
          );
        }
        if (_isLoopback(entry.value)) {
          problems.add(
            '${entry.key} points at ${uri.host}, a development host; a release '
            'build would send every token to a machine that is not the API',
          );
        }
      }
    }

    // 2. An unrecognised environment would skip the checks above, so it is
    //    rejected in every build rather than only in production.
    if (!const {
      'dev',
      'development',
      'test',
      'staging',
      'prod',
      'production',
    }.contains(env.toLowerCase())) {
      problems.add(
        'APP_ENV is "$env", which is not a known environment; production checks '
        'would be silently skipped',
      );
    }

    return problems;
  }

  /// Throws [AppConfigError] when this build is unsafe to ship.
  ///
  /// Called once at startup rather than per request: a misconfigured release
  /// build should fail immediately and visibly, not degrade into sending tokens
  /// in cleartext.
  void validate() {
    final problems = productionConfigProblems();
    if (problems.isNotEmpty) {
      throw AppConfigError(problems);
    }
  }
}

/// Raised at startup when a build's configuration is unsafe.
///
/// A release build that throws here shows a failure instead of running with its
/// traffic pointed at the wrong host, which is the intended outcome. Development
/// and test configurations never throw.
class AppConfigError implements Exception {
  final List<String> problems;
  AppConfigError(this.problems);

  @override
  String toString() =>
      'AppConfigError: unsafe application configuration '
      '(${problems.length} problem(s)): ${problems.join('; ')}';
}
