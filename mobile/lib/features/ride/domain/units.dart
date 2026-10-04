/// Unit formatting. Backend stores canonical metric; UI converts per the
/// user's measurement system. Never persisted in presentation units.
String formatDistance(double meters, {required bool imperial}) {
  if (imperial) {
    return '${(meters / 1609.344).toStringAsFixed(2)} mi';
  }
  if (meters >= 10000) return '${(meters / 1000).toStringAsFixed(1)} km';
  if (meters >= 1000) return '${(meters / 1000).toStringAsFixed(2)} km';
  return '${meters.toStringAsFixed(0)} m';
}

String formatSpeed(double ms, {required bool imperial}) {
  if (imperial) {
    return '${(ms * 2.23694).toStringAsFixed(1)} mph';
  }
  return '${(ms * 3.6).toStringAsFixed(1)} km/h';
}

String formatElevation(double meters, {required bool imperial}) {
  if (imperial) {
    return '${(meters * 3.28084).toStringAsFixed(0)} ft';
  }
  return '${meters.toStringAsFixed(0)} m';
}

String formatDuration(int totalSeconds) {
  final h = totalSeconds ~/ 3600;
  final m = (totalSeconds % 3600) ~/ 60;
  final s = totalSeconds % 60;
  String two(int n) => n.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '${two(m)}:${two(s)}';
}
