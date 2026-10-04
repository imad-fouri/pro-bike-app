import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/training_repository.dart';
import '../domain/training_models.dart';

final trainingRepositoryProvider = Provider<TrainingRepository>(
  (ref) => TrainingRepository(ref.watch(apiClientProvider)),
);

/// The dashboard's single aggregate read. One call, so the page cannot show
/// profile and activities from different moments.
final trainingSummaryProvider = FutureProvider<TrainingSummary>((ref) {
  return ref.watch(trainingRepositoryProvider).summary();
});

final ftpHistoryProvider = FutureProvider<FtpHistory>((ref) {
  return ref.watch(trainingRepositoryProvider).ftpRecords();
});

final trainingActivitiesProvider =
    FutureProvider<({List<TrainingActivity> items, int total})>((ref) {
      return ref.watch(trainingRepositoryProvider).activities(pageSize: 50);
    });

final trainingLoadsProvider =
    FutureProvider<({List<TrainingLoad> items, String version})>((ref) {
      return ref.watch(trainingRepositoryProvider).loads();
    });

final calculationVersionsProvider = FutureProvider<List<CalculationVersion>>((
  ref,
) {
  return ref.watch(trainingRepositoryProvider).calculationVersions();
});

/// Everything that reads training state goes through here so a profile or FTP
/// change invalidates the dashboard, the history and the loads together.
void invalidateTraining(Ref ref) {
  ref.invalidate(trainingSummaryProvider);
  ref.invalidate(ftpHistoryProvider);
  ref.invalidate(trainingActivitiesProvider);
  ref.invalidate(trainingLoadsProvider);
}

/// Saving profile inputs. FTP goes through the append-only record path.
class TrainingProfileEditor extends AsyncNotifier<void> {
  @override
  Future<void> build() async {}

  Future<void> save({
    double? ftpW,
    FtpSource? ftpSource,
    DateTime? effectiveAt,
    double? maxHrBpm,
    double? restingHrBpm,
    HrZoneModel? hrZoneModel,
    String? timezone,
  }) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final repo = ref.read(trainingRepositoryProvider);
      await repo.updateProfile(
        ftpW: ftpW,
        ftpSource: ftpSource,
        effectiveAt: effectiveAt,
        maxHrBpm: maxHrBpm,
        restingHrBpm: restingHrBpm,
        hrZoneModel: hrZoneModel,
        timezone: timezone,
      );
      invalidateTraining(ref);
    });
  }

  /// Records a test result. [rideId] is required for test protocols: the server
  /// computes the value, so a client cannot assert a number.
  Future<void> recordTest({
    required FtpSource source,
    required String rideId,
  }) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      await ref
          .read(trainingRepositoryProvider)
          .addFtpRecord(source: source, rideId: rideId);
      invalidateTraining(ref);
    });
  }
}

final trainingProfileEditorProvider =
    AsyncNotifierProvider<TrainingProfileEditor, void>(
      TrainingProfileEditor.new,
    );

/// Re-derives a ride's metrics against the FTP that is current now.
class ReanalyzeAction extends AsyncNotifier<void> {
  @override
  Future<void> build() async {}

  Future<TrainingActivity?> run(String rideId) async {
    state = const AsyncLoading();
    final result = await AsyncValue.guard(
      () => ref.read(trainingRepositoryProvider).reanalyze(rideId),
    );
    state = const AsyncData(null);
    if (result.hasValue) invalidateTraining(ref);
    return result.value;
  }
}

final reanalyzeProvider = AsyncNotifierProvider<ReanalyzeAction, void>(
  ReanalyzeAction.new,
);

/// Workout list with the same loading/data/empty/error contract as the other
/// features. A 409 on edit surfaces as an error the user must reload from.
class WorkoutList extends AsyncNotifier<List<Workout>> {
  @override
  Future<List<Workout>> build() async {
    final result = await ref.read(trainingRepositoryProvider).listWorkouts();
    return result.items;
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final result = await ref.read(trainingRepositoryProvider).listWorkouts();
      return result.items;
    });
  }

  Future<Workout> create(WorkoutInput input) async {
    final workout = await ref
        .read(trainingRepositoryProvider)
        .createWorkout(input);
    await refresh();
    return workout;
  }

  /// [expectedVersion] must be the version that was read; a stale write is
  /// rejected by the server rather than silently clobbering an edit.
  Future<Workout> saveWorkout(
    String id, {
    required int expectedVersion,
    WorkoutInput? input,
    WorkoutStatus? status,
  }) async {
    final workout = await ref
        .read(trainingRepositoryProvider)
        .updateWorkout(
          id,
          expectedVersion: expectedVersion,
          input: input,
          status: status,
        );
    ref.invalidate(workoutDetailProvider(id));
    await refresh();
    return workout;
  }

  Future<void> remove(String id) async {
    await ref.read(trainingRepositoryProvider).deleteWorkout(id);
    ref.invalidate(workoutDetailProvider(id));
    await refresh();
  }
}

final workoutListProvider = AsyncNotifierProvider<WorkoutList, List<Workout>>(
  WorkoutList.new,
);

final workoutDetailProvider = FutureProvider.family<Workout, String>(
  (ref, id) => ref.watch(trainingRepositoryProvider).workout(id),
);
