import '../../../core/network/api_client.dart';
import '../domain/coach_models.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock provider, no local
/// "AI": the app calls CycleCoach and CycleCoach decides what an explanation
/// costs and whether it may be produced at all (ADR-11 §5, §8).
///
/// Nothing here sends a metric. A request carries an intent, a message, and at
/// most one resource pointer; the server resolves the pointer, enforces
/// ownership, and builds the context.
class CoachRepository {
  final ApiClient api;
  static const prefix = '/api/v1/coach';

  const CoachRepository(this.api);

  /// General entry point. [rideId] and [workoutId] are pointers the server
  /// resolves — a foreign id is a 404, never an explanation.
  Future<CoachMessage> ask({
    required CoachIntent intent,
    required String message,
    String? rideId,
    String? workoutId,
    required String locale,
  }) async {
    final body = await api.post('$prefix/message', {
      'intent': intent.wire,
      'message': message,
      'locale': locale,
      if (rideId != null || workoutId != null)
        'context_reference': <String, String>{
          'ride_id': ?rideId,
          'workout_id': ?workoutId,
        },
    }, auth: true);
    return CoachMessage.fromJson(body);
  }

  /// Ride-scoped shortcut. Used by the history list, where the intent is
  /// already fixed by the item the rider tapped.
  Future<CoachMessage> explainRide(
    String rideId, {
    required String locale,
  }) async {
    final body = await api.post('$prefix/ride/$rideId/explain', {
      'locale': locale,
    }, auth: true);
    return CoachMessage.fromJson(body);
  }

  Future<CoachMessage> explainWorkout(
    String workoutId, {
    required String locale,
  }) async {
    final body = await api.post('$prefix/workout/$workoutId/explain', {
      'locale': locale,
    }, auth: true);
    return CoachMessage.fromJson(body);
  }

  Future<CoachMessage> weeklySummary({required String locale}) async {
    final body = await api.get(
      '$prefix/weekly-summary?locale=$locale',
      auth: true,
    );
    return CoachMessage.fromJson(body);
  }

  /// Read once when the screen opens: it is metadata, not a call, and it tells
  /// the user up front whether answers will be explained or deterministic.
  Future<CoachStatus> status() async {
    final body = await api.get('$prefix/status', auth: true);
    return CoachStatus.fromJson(body);
  }
}
