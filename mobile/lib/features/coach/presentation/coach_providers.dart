import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/coach_repository.dart';
import '../domain/coach_models.dart';

final coachRepositoryProvider = Provider<CoachRepository>(
  (ref) => CoachRepository(ref.watch(apiClientProvider)),
);

/// Read once on open. A failure here is not fatal: the Coach still answers, so
/// the screen treats an unknown status as "cannot say" and keeps working.
final coachStatusProvider = FutureProvider<CoachStatus>((ref) {
  return ref.watch(coachRepositoryProvider).status();
});

/// The single answer on screen — never a transcript. Asking again replaces it,
/// and nothing is cached or persisted client-side (ADR-11 §6).
///
/// Errors are not swallowed into the answer: a transport failure or a 429 must
/// be visibly different from a coach that chose the deterministic fallback,
/// which arrives as a normal message with `fallbackUsed: true`.
class CoachAsk extends AsyncNotifier<CoachMessage?> {
  @override
  Future<CoachMessage?> build() async => null;

  Future<void> ask({
    required CoachIntent intent,
    required String message,
    required String locale,
    String? rideId,
    String? workoutId,
  }) async {
    // A second submission while the first request is still in flight must not
    // issue a second request: every ask spends rate-limit budget and, when a
    // provider is configured, real model money.
    if (state.isLoading) return;
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      return ref
          .read(coachRepositoryProvider)
          .ask(
            intent: intent,
            message: message,
            locale: locale,
            rideId: rideId,
            workoutId: workoutId,
          );
    });
  }
}

final coachAskProvider = AsyncNotifierProvider<CoachAsk, CoachMessage?>(
  CoachAsk.new,
);
