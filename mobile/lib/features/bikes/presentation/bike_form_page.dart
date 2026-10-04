import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../auth/presentation/login_page.dart';
import '../domain/bike.dart';
import '../domain/bike_validators.dart';
import 'bikes_providers.dart';

/// Add / edit bike. Sends the loaded version for optimistic concurrency;
/// a 409 surfaces as "changed elsewhere" instead of silent overwrite.
class BikeFormPage extends ConsumerStatefulWidget {
  final String? bikeId;
  const BikeFormPage({super.key, this.bikeId});

  @override
  ConsumerState<BikeFormPage> createState() => _BikeFormPageState();
}

class _BikeFormPageState extends ConsumerState<BikeFormPage> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _brand = TextEditingController();
  final _model = TextEditingController();
  final _year = TextEditingController();
  final _frame = TextEditingController();
  final _weight = TextEditingController();
  final _notes = TextEditingController();
  String _category = 'road';
  Bike? _loaded;
  bool _ready = false;
  String? _errorCode;

  @override
  void initState() {
    super.initState();
    if (widget.bikeId == null) {
      _ready = true;
    } else {
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final bike = await ref
          .read(bikesRepositoryProvider)
          .detail(widget.bikeId!);
      if (!mounted) return;
      setState(() {
        _loaded = bike;
        _name.text = bike.name;
        _brand.text = bike.brand ?? '';
        _model.text = bike.model ?? '';
        _year.text = bike.modelYear?.toString() ?? '';
        _frame.text = bike.frameSize ?? '';
        _weight.text = bike.weightKg?.toString() ?? '';
        _notes.text = bike.notes ?? '';
        _category = bike.category;
        _ready = true;
      });
    } on Exception catch (e) {
      if (!mounted) return;
      setState(() {
        _errorCode = e is ApiException ? e.code : 'ERROR';
        _ready = true;
      });
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _brand.dispose();
    _model.dispose();
    _year.dispose();
    _frame.dispose();
    _weight.dispose();
    _notes.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    if (!_ready) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    String? req(String? v, String? Function(String) fn) {
      if (v == null || v.isEmpty) return t.get('auth.required');
      final key = fn(v);
      return key == null ? null : t.get(key);
    }

    return AuthScaffold(
      titleKey: widget.bikeId == null ? 'bikes.add' : 'bikes.edit',
      children: [
        if (_errorCode != null)
          Text('${t.get('bikes.genericError')} ($_errorCode)'),
        Form(
          key: _form,
          child: Column(
            children: [
              TextFormField(
                controller: _name,
                decoration: InputDecoration(labelText: t.get('bikes.name')),
                validator: (v) => req(v, BikeValidators.name),
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<String>(
                initialValue: _category,
                decoration: InputDecoration(
                  labelText: t.get('bikes.categoryLabel'),
                ),
                items: [
                  for (final c in BikeValidators.categories)
                    DropdownMenuItem(
                      value: c,
                      child: Text(t.get('bikes.category.$c')),
                    ),
                ],
                onChanged: (v) => setState(() => _category = v ?? 'road'),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _brand,
                decoration: InputDecoration(labelText: t.get('bikes.brand')),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _model,
                decoration: InputDecoration(labelText: t.get('bikes.model')),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _year,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                  labelText: t.get('bikes.modelYear'),
                ),
                validator: (v) {
                  if (v == null || v.isEmpty) return null;
                  final key = BikeValidators.modelYear(v);
                  return key == null ? null : t.get(key);
                },
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _frame,
                decoration: InputDecoration(
                  labelText: t.get('bikes.frameSize'),
                ),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _weight,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(labelText: t.get('bikes.weightKg')),
                validator: (v) {
                  if (v == null || v.isEmpty) return null;
                  final key = BikeValidators.weightKg(v);
                  return key == null ? null : t.get(key);
                },
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _notes,
                maxLines: 3,
                decoration: InputDecoration(labelText: t.get('bikes.notes')),
                validator: (v) {
                  final key = BikeValidators.notes(v ?? '');
                  return key == null ? null : t.get(key);
                },
              ),
            ],
          ),
        ),
        const SizedBox(height: 24),
        FilledButton(onPressed: _save, child: Text(t.get('bikes.save'))),
      ],
    );
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    if (!mounted) return;
    final t = context.l10n;
    try {
      final repo = ref.read(bikesRepositoryProvider);
      if (widget.bikeId == null) {
        final created = await repo.create(
          Bike(
            id: '',
            name: _name.text.trim(),
            category: _category,
            brand: _opt(_brand.text),
            model: _opt(_model.text),
            modelYear: _optInt(_year.text),
            frameSize: _opt(_frame.text),
            weightKg: _optDouble(_weight.text),
            notes: _opt(_notes.text),
            status: 'active',
            initialDistanceKm: 0,
            version: 1,
          ),
        );
        ref.read(bikeListProvider.notifier).refresh();
        if (!mounted) return;
        context.go('/bikes/${created.id}');
      } else {
        final updated = await repo.update(_loaded!, {
          'name': _name.text.trim(),
          'category': _category,
          'brand': _opt(_brand.text),
          'model': _opt(_model.text),
          'model_year': _optInt(_year.text),
          'frame_size': _opt(_frame.text),
          'weight_kg': _optDouble(_weight.text),
          'notes': _opt(_notes.text),
        });
        ref.invalidate(bikeDetailProvider(updated.id));
        ref.read(bikeListProvider.notifier).refresh();
        if (!mounted) return;
        context.go('/bikes/${updated.id}');
      }
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _errorCode = e.code == 'VERSION_CONFLICT' ? 'bikes.conflict' : e.code;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            e.code == 'VERSION_CONFLICT'
                ? t.get('bikes.conflict')
                : t.get('bikes.genericError'),
          ),
        ),
      );
    }
  }

  static String? _opt(String v) => v.trim().isEmpty ? null : v.trim();
  static int? _optInt(String v) =>
      v.trim().isEmpty ? null : int.tryParse(v.trim());
  static double? _optDouble(String v) =>
      v.trim().isEmpty ? null : double.tryParse(v.trim());
}
