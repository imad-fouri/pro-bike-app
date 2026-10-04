import '../../../core/network/api_client.dart';
import '../domain/bike.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
class BikesRepository {
  final ApiClient api;
  static const prefix = '/api/v1/bikes';

  const BikesRepository(this.api);

  Future<({List<Bike> items, int total})> list({
    int page = 1,
    int pageSize = 20,
    String? category,
    bool archived = false,
  }) async {
    final q = {
      'page': '$page',
      'page_size': '$pageSize',
      if (category case final c) 'category': c,
      if (archived) 'status_all': 'true',
    }.entries.map((e) => '${e.key}=${e.value}').join('&');
    final body = await api.get('$prefix?$q', auth: true);
    final items = (body['items'] as List)
        .map((e) => Bike.fromJson(e as Map<String, dynamic>))
        .toList();
    return (items: items, total: (body['total'] as num).toInt());
  }

  Future<Bike> detail(String id) async {
    final body = await api.get('$prefix/$id', auth: true);
    return Bike.fromJson(body);
  }

  Future<Bike> create(Bike bike) async {
    final body = await api.post(prefix, bike.toCreateJson(), auth: true);
    return Bike.fromJson(body);
  }

  Future<Bike> update(Bike bike, Map<String, dynamic> changes) async {
    final body = await api.patch('$prefix/${bike.id}', {
      ...changes,
      'expected_version': bike.version,
    }, auth: true);
    return Bike.fromJson(body);
  }

  Future<Bike> archive(String id) async {
    final body = await api.post('$prefix/$id/archive', {}, auth: true);
    return Bike.fromJson(body);
  }

  Future<Bike> restore(String id) async {
    final body = await api.post('$prefix/$id/restore', {}, auth: true);
    return Bike.fromJson(body);
  }

  Future<void> remove(String id) async {
    await api.delete('$prefix/$id', auth: true);
  }
}
