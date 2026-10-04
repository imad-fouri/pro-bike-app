/// Training presentation helpers. These convert canonical API units for
/// display only — nothing here is ever sent back to the server.
///
/// The `—` placeholder means "not measured". It must never become `0`: a rider
/// without a power meter has no normalized power, not 0 W (§32, ADR-10 §2).
const unavailable = '—';

String formatPower(double? watts) =>
    watts == null ? unavailable : '${watts.round()} W';

String formatLoad(double? load) => load == null
    ? unavailable
    : load >= 100
    ? load.toStringAsFixed(0)
    : load.toStringAsFixed(1);

/// Intensity factor to two decimals; null stays unavailable.
String formatIf(double? intensityFactor) =>
    intensityFactor == null ? unavailable : intensityFactor.toStringAsFixed(2);

String formatHr(double? bpm) =>
    bpm == null ? unavailable : '${bpm.round()} bpm';

String formatCadence(double? rpm) =>
    rpm == null ? unavailable : '${rpm.round()} rpm';

/// HH:MM:SS or MM:SS for a duration in seconds.
String formatTrainingDuration(int? seconds) {
  if (seconds == null) return unavailable;
  final h = seconds ~/ 3600;
  final m = (seconds % 3600) ~/ 60;
  final s = seconds % 60;
  String two(int n) => n.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '${two(m)}:${two(s)}';
}

/// A zone as a share of total, for a distribution bar. A 0-second total yields
/// 0 rather than a division by zero, and the caller shows no bar at all.
double zoneShare(double seconds, double total) =>
    total <= 0 ? 0 : (seconds / total).clamp(0, 1).toDouble();

/// FTP as a string, or unavailable with no value to back it.
String formatFtp(double? ftpW) =>
    ftpW == null ? unavailable : '${ftpW.round()} W';

/// Distance in the user's unit system (training activities carry distance too).
String formatTrainingDistance(double? meters, {required bool imperial}) {
  if (meters == null) return unavailable;
  if (imperial) return '${(meters / 1609.344).toStringAsFixed(1)} mi';
  return '${(meters / 1000).toStringAsFixed(1)} km';
}
