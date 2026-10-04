import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../data/bikes_repository.dart';
import '../domain/bike.dart';

final bikesRepositoryProvider = Provider<BikesRepository>((ref) {
  return BikesRepository(ref.watch(apiClientProvider));
});

/// Bike list state: loading / data / empty / error. Refresh after mutations.
class BikeList extends AsyncNotifier<List<Bike>> {
  late BikesRepository _repo;

  @override
  Future<List<Bike>> build() async {
    _repo = ref.watch(bikesRepositoryProvider);
    final result = await _repo.list(pageSize: 100);
    return result.items;
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final result = await _repo.list(pageSize: 100);
      return result.items;
    });
  }
}

final bikeListProvider = AsyncNotifierProvider<BikeList, List<Bike>>(
  BikeList.new,
);

final bikeDetailProvider = FutureProvider.family<Bike, String>((ref, id) async {
  final repo = ref.watch(bikesRepositoryProvider);
  return repo.detail(id);
});
