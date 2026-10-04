/// Decoding helpers for API numeric fields.
///
/// The API serializes `NUMERIC` columns as **strings** so precision survives
/// the round trip (see the `pattern` on every Decimal field in the OpenAPI
/// schema). A client that casts straight to `num` therefore throws on real
/// data, even though a hand-written fixture with a JSON number passes.
///
/// These helpers accept both shapes so a test fixture and the live API decode
/// identically.
library;

/// A numeric field that may arrive as a number or as a decimal string.
/// Returns null for null, so "not measured" stays distinguishable from 0.
double? apiDouble(Object? value) => switch (value) {
  null => null,
  final num n => n.toDouble(),
  final String s => double.tryParse(s),
  _ => null,
};

/// The integer form, for counts and durations.
int? apiInt(Object? value) => switch (value) {
  null => null,
  final int i => i,
  final num n => n.round(),
  final String s => int.tryParse(s) ?? double.tryParse(s)?.round(),
  _ => null,
};

/// [apiDouble] with a fallback, for fields the API always sends.
double apiDoubleOr(Object? value, double fallback) =>
    apiDouble(value) ?? fallback;

/// [apiInt] with a fallback.
int apiIntOr(Object? value, int fallback) => apiInt(value) ?? fallback;
