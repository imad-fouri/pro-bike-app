/// Flavor/env config via --dart-define. No secrets here.
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
}
