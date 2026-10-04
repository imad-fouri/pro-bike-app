/// Bike entity. Canonical units from the API (kg); presentation converts
/// to lb when the user's measurement system is imperial.
class Bike {
  final String id;
  final String name;
  final String category;
  final String? brand;
  final String? model;
  final int? modelYear;
  final String? frameSize;
  final double? weightKg;
  final String? notes;
  final String? imageRef;
  final String status;
  final double initialDistanceKm;
  final int version;

  const Bike({
    required this.id,
    required this.name,
    required this.category,
    this.brand,
    this.model,
    this.modelYear,
    this.frameSize,
    this.weightKg,
    this.notes,
    this.imageRef,
    required this.status,
    required this.initialDistanceKm,
    required this.version,
  });

  bool get isArchived => status == 'archived';

  /// Display weight in the user's unit system. Space reserved for future
  /// ride-derived mileage, sensors, components — never faked.
  String displayWeight({required bool imperial}) {
    if (weightKg == null) return '';
    if (imperial) {
      return '${(weightKg! * 2.20462).toStringAsFixed(1)} lb';
    }
    return '${weightKg!.toStringAsFixed(1)} kg';
  }

  String subtitle() {
    final parts = <String>[category];
    if (brand != null) {
      parts.add(model != null ? '$brand $model' : brand!);
    } else if (model != null) {
      parts.add(model!);
    }
    return parts.join(' • ');
  }

  factory Bike.fromJson(Map<String, dynamic> json) {
    return Bike(
      id: '${json['id']}',
      name: '${json['name']}',
      category: '${json['category']}',
      brand: json['brand'] as String?,
      model: json['model'] as String?,
      modelYear: (json['model_year'] as num?)?.toInt(),
      frameSize: json['frame_size'] as String?,
      weightKg: (json['weight_kg'] as num?)?.toDouble(),
      notes: json['notes'] as String?,
      imageRef: json['image_ref'] as String?,
      status: '${json['status']}',
      initialDistanceKm: ((json['initial_distance_km'] as num?) ?? 0)
          .toDouble(),
      version: (json['version'] as num? ?? 1).toInt(),
    );
  }

  Map<String, dynamic> toCreateJson() {
    return {
      'name': name,
      'category': category,
      if (brand != null) 'brand': brand,
      if (model != null) 'model': model,
      if (modelYear != null) 'model_year': modelYear,
      if (frameSize != null) 'frame_size': frameSize,
      if (weightKg != null) 'weight_kg': weightKg,
      if (notes != null) 'notes': notes,
      'initial_distance_km': initialDistanceKm,
    };
  }
}
