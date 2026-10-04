/// Pure bike validators mirroring backend limits (ADR-05). L10n keys only.
class BikeValidators {
  static const categories = [
    'road',
    'gravel',
    'mountain_bike',
    'cyclocross',
    'endurance',
    'time_trial',
    'track',
    'bmx',
    'touring',
    'bikepacking',
    'e_bike',
    'commuting',
    'other',
  ];

  static String? name(String value) {
    final v = value.trim();
    if (v.isEmpty || v.length > 80) return 'bikes.invalidName';
    return null;
  }

  static String? category(String value) {
    if (!categories.contains(value)) return 'bikes.invalidCategory';
    return null;
  }

  static String? modelYear(String value) {
    if (value.trim().isEmpty) return null; // optional
    final year = int.tryParse(value.trim());
    final now = DateTime.now().year;
    if (year == null || year < 1900 || year > now + 1) {
      return 'bikes.invalidYear';
    }
    return null;
  }

  static String? weightKg(String value) {
    if (value.trim().isEmpty) return null; // optional
    final w = double.tryParse(value.trim());
    if (w == null || w <= 0 || w > 200) return 'bikes.invalidWeight';
    return null;
  }

  static String? notes(String value) {
    if (value.length > 2000) return 'bikes.invalidNotes';
    return null;
  }
}
