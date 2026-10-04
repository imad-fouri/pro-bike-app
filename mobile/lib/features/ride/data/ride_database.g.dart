// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'ride_database.dart';

// ignore_for_file: type=lint
class $LocalRidesTable extends LocalRides
    with TableInfo<$LocalRidesTable, LocalRide> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $LocalRidesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _serverIdMeta = const VerificationMeta(
    'serverId',
  );
  @override
  late final GeneratedColumn<String> serverId = GeneratedColumn<String>(
    'server_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _bikeIdMeta = const VerificationMeta('bikeId');
  @override
  late final GeneratedColumn<String> bikeId = GeneratedColumn<String>(
    'bike_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _routeIdMeta = const VerificationMeta(
    'routeId',
  );
  @override
  late final GeneratedColumn<String> routeId = GeneratedColumn<String>(
    'route_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _statusMeta = const VerificationMeta('status');
  @override
  late final GeneratedColumn<String> status = GeneratedColumn<String>(
    'status',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
    defaultValue: const Constant('recording'),
  );
  static const VerificationMeta _startedAtMeta = const VerificationMeta(
    'startedAt',
  );
  @override
  late final GeneratedColumn<DateTime> startedAt = GeneratedColumn<DateTime>(
    'started_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _endedAtMeta = const VerificationMeta(
    'endedAt',
  );
  @override
  late final GeneratedColumn<DateTime> endedAt = GeneratedColumn<DateTime>(
    'ended_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _uploadedSeqMeta = const VerificationMeta(
    'uploadedSeq',
  );
  @override
  late final GeneratedColumn<int> uploadedSeq = GeneratedColumn<int>(
    'uploaded_seq',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(-1),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    serverId,
    bikeId,
    routeId,
    status,
    startedAt,
    endedAt,
    uploadedSeq,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'local_rides';
  @override
  VerificationContext validateIntegrity(
    Insertable<LocalRide> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('server_id')) {
      context.handle(
        _serverIdMeta,
        serverId.isAcceptableOrUnknown(data['server_id']!, _serverIdMeta),
      );
    }
    if (data.containsKey('bike_id')) {
      context.handle(
        _bikeIdMeta,
        bikeId.isAcceptableOrUnknown(data['bike_id']!, _bikeIdMeta),
      );
    } else if (isInserting) {
      context.missing(_bikeIdMeta);
    }
    if (data.containsKey('route_id')) {
      context.handle(
        _routeIdMeta,
        routeId.isAcceptableOrUnknown(data['route_id']!, _routeIdMeta),
      );
    }
    if (data.containsKey('status')) {
      context.handle(
        _statusMeta,
        status.isAcceptableOrUnknown(data['status']!, _statusMeta),
      );
    }
    if (data.containsKey('started_at')) {
      context.handle(
        _startedAtMeta,
        startedAt.isAcceptableOrUnknown(data['started_at']!, _startedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_startedAtMeta);
    }
    if (data.containsKey('ended_at')) {
      context.handle(
        _endedAtMeta,
        endedAt.isAcceptableOrUnknown(data['ended_at']!, _endedAtMeta),
      );
    }
    if (data.containsKey('uploaded_seq')) {
      context.handle(
        _uploadedSeqMeta,
        uploadedSeq.isAcceptableOrUnknown(
          data['uploaded_seq']!,
          _uploadedSeqMeta,
        ),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  LocalRide map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return LocalRide(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      serverId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}server_id'],
      ),
      bikeId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}bike_id'],
      )!,
      routeId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}route_id'],
      ),
      status: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}status'],
      )!,
      startedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}started_at'],
      )!,
      endedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}ended_at'],
      ),
      uploadedSeq: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}uploaded_seq'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      )!,
    );
  }

  @override
  $LocalRidesTable createAlias(String alias) {
    return $LocalRidesTable(attachedDatabase, alias);
  }
}

class LocalRide extends DataClass implements Insertable<LocalRide> {
  final String id;
  final String? serverId;
  final String bikeId;
  final String? routeId;
  final String status;
  final DateTime startedAt;
  final DateTime? endedAt;
  final int uploadedSeq;
  final DateTime updatedAt;
  const LocalRide({
    required this.id,
    this.serverId,
    required this.bikeId,
    this.routeId,
    required this.status,
    required this.startedAt,
    this.endedAt,
    required this.uploadedSeq,
    required this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    if (!nullToAbsent || serverId != null) {
      map['server_id'] = Variable<String>(serverId);
    }
    map['bike_id'] = Variable<String>(bikeId);
    if (!nullToAbsent || routeId != null) {
      map['route_id'] = Variable<String>(routeId);
    }
    map['status'] = Variable<String>(status);
    map['started_at'] = Variable<DateTime>(startedAt);
    if (!nullToAbsent || endedAt != null) {
      map['ended_at'] = Variable<DateTime>(endedAt);
    }
    map['uploaded_seq'] = Variable<int>(uploadedSeq);
    map['updated_at'] = Variable<DateTime>(updatedAt);
    return map;
  }

  LocalRidesCompanion toCompanion(bool nullToAbsent) {
    return LocalRidesCompanion(
      id: Value(id),
      serverId: serverId == null && nullToAbsent
          ? const Value.absent()
          : Value(serverId),
      bikeId: Value(bikeId),
      routeId: routeId == null && nullToAbsent
          ? const Value.absent()
          : Value(routeId),
      status: Value(status),
      startedAt: Value(startedAt),
      endedAt: endedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(endedAt),
      uploadedSeq: Value(uploadedSeq),
      updatedAt: Value(updatedAt),
    );
  }

  factory LocalRide.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return LocalRide(
      id: serializer.fromJson<String>(json['id']),
      serverId: serializer.fromJson<String?>(json['serverId']),
      bikeId: serializer.fromJson<String>(json['bikeId']),
      routeId: serializer.fromJson<String?>(json['routeId']),
      status: serializer.fromJson<String>(json['status']),
      startedAt: serializer.fromJson<DateTime>(json['startedAt']),
      endedAt: serializer.fromJson<DateTime?>(json['endedAt']),
      uploadedSeq: serializer.fromJson<int>(json['uploadedSeq']),
      updatedAt: serializer.fromJson<DateTime>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'serverId': serializer.toJson<String?>(serverId),
      'bikeId': serializer.toJson<String>(bikeId),
      'routeId': serializer.toJson<String?>(routeId),
      'status': serializer.toJson<String>(status),
      'startedAt': serializer.toJson<DateTime>(startedAt),
      'endedAt': serializer.toJson<DateTime?>(endedAt),
      'uploadedSeq': serializer.toJson<int>(uploadedSeq),
      'updatedAt': serializer.toJson<DateTime>(updatedAt),
    };
  }

  LocalRide copyWith({
    String? id,
    Value<String?> serverId = const Value.absent(),
    String? bikeId,
    Value<String?> routeId = const Value.absent(),
    String? status,
    DateTime? startedAt,
    Value<DateTime?> endedAt = const Value.absent(),
    int? uploadedSeq,
    DateTime? updatedAt,
  }) => LocalRide(
    id: id ?? this.id,
    serverId: serverId.present ? serverId.value : this.serverId,
    bikeId: bikeId ?? this.bikeId,
    routeId: routeId.present ? routeId.value : this.routeId,
    status: status ?? this.status,
    startedAt: startedAt ?? this.startedAt,
    endedAt: endedAt.present ? endedAt.value : this.endedAt,
    uploadedSeq: uploadedSeq ?? this.uploadedSeq,
    updatedAt: updatedAt ?? this.updatedAt,
  );
  LocalRide copyWithCompanion(LocalRidesCompanion data) {
    return LocalRide(
      id: data.id.present ? data.id.value : this.id,
      serverId: data.serverId.present ? data.serverId.value : this.serverId,
      bikeId: data.bikeId.present ? data.bikeId.value : this.bikeId,
      routeId: data.routeId.present ? data.routeId.value : this.routeId,
      status: data.status.present ? data.status.value : this.status,
      startedAt: data.startedAt.present ? data.startedAt.value : this.startedAt,
      endedAt: data.endedAt.present ? data.endedAt.value : this.endedAt,
      uploadedSeq: data.uploadedSeq.present
          ? data.uploadedSeq.value
          : this.uploadedSeq,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('LocalRide(')
          ..write('id: $id, ')
          ..write('serverId: $serverId, ')
          ..write('bikeId: $bikeId, ')
          ..write('routeId: $routeId, ')
          ..write('status: $status, ')
          ..write('startedAt: $startedAt, ')
          ..write('endedAt: $endedAt, ')
          ..write('uploadedSeq: $uploadedSeq, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    serverId,
    bikeId,
    routeId,
    status,
    startedAt,
    endedAt,
    uploadedSeq,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is LocalRide &&
          other.id == this.id &&
          other.serverId == this.serverId &&
          other.bikeId == this.bikeId &&
          other.routeId == this.routeId &&
          other.status == this.status &&
          other.startedAt == this.startedAt &&
          other.endedAt == this.endedAt &&
          other.uploadedSeq == this.uploadedSeq &&
          other.updatedAt == this.updatedAt);
}

class LocalRidesCompanion extends UpdateCompanion<LocalRide> {
  final Value<String> id;
  final Value<String?> serverId;
  final Value<String> bikeId;
  final Value<String?> routeId;
  final Value<String> status;
  final Value<DateTime> startedAt;
  final Value<DateTime?> endedAt;
  final Value<int> uploadedSeq;
  final Value<DateTime> updatedAt;
  final Value<int> rowid;
  const LocalRidesCompanion({
    this.id = const Value.absent(),
    this.serverId = const Value.absent(),
    this.bikeId = const Value.absent(),
    this.routeId = const Value.absent(),
    this.status = const Value.absent(),
    this.startedAt = const Value.absent(),
    this.endedAt = const Value.absent(),
    this.uploadedSeq = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  LocalRidesCompanion.insert({
    required String id,
    this.serverId = const Value.absent(),
    required String bikeId,
    this.routeId = const Value.absent(),
    this.status = const Value.absent(),
    required DateTime startedAt,
    this.endedAt = const Value.absent(),
    this.uploadedSeq = const Value.absent(),
    required DateTime updatedAt,
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       bikeId = Value(bikeId),
       startedAt = Value(startedAt),
       updatedAt = Value(updatedAt);
  static Insertable<LocalRide> custom({
    Expression<String>? id,
    Expression<String>? serverId,
    Expression<String>? bikeId,
    Expression<String>? routeId,
    Expression<String>? status,
    Expression<DateTime>? startedAt,
    Expression<DateTime>? endedAt,
    Expression<int>? uploadedSeq,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (serverId != null) 'server_id': serverId,
      if (bikeId != null) 'bike_id': bikeId,
      if (routeId != null) 'route_id': routeId,
      if (status != null) 'status': status,
      if (startedAt != null) 'started_at': startedAt,
      if (endedAt != null) 'ended_at': endedAt,
      if (uploadedSeq != null) 'uploaded_seq': uploadedSeq,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  LocalRidesCompanion copyWith({
    Value<String>? id,
    Value<String?>? serverId,
    Value<String>? bikeId,
    Value<String?>? routeId,
    Value<String>? status,
    Value<DateTime>? startedAt,
    Value<DateTime?>? endedAt,
    Value<int>? uploadedSeq,
    Value<DateTime>? updatedAt,
    Value<int>? rowid,
  }) {
    return LocalRidesCompanion(
      id: id ?? this.id,
      serverId: serverId ?? this.serverId,
      bikeId: bikeId ?? this.bikeId,
      routeId: routeId ?? this.routeId,
      status: status ?? this.status,
      startedAt: startedAt ?? this.startedAt,
      endedAt: endedAt ?? this.endedAt,
      uploadedSeq: uploadedSeq ?? this.uploadedSeq,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (serverId.present) {
      map['server_id'] = Variable<String>(serverId.value);
    }
    if (bikeId.present) {
      map['bike_id'] = Variable<String>(bikeId.value);
    }
    if (routeId.present) {
      map['route_id'] = Variable<String>(routeId.value);
    }
    if (status.present) {
      map['status'] = Variable<String>(status.value);
    }
    if (startedAt.present) {
      map['started_at'] = Variable<DateTime>(startedAt.value);
    }
    if (endedAt.present) {
      map['ended_at'] = Variable<DateTime>(endedAt.value);
    }
    if (uploadedSeq.present) {
      map['uploaded_seq'] = Variable<int>(uploadedSeq.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('LocalRidesCompanion(')
          ..write('id: $id, ')
          ..write('serverId: $serverId, ')
          ..write('bikeId: $bikeId, ')
          ..write('routeId: $routeId, ')
          ..write('status: $status, ')
          ..write('startedAt: $startedAt, ')
          ..write('endedAt: $endedAt, ')
          ..write('uploadedSeq: $uploadedSeq, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $LocalPointsTable extends LocalPoints
    with TableInfo<$LocalPointsTable, LocalPoint> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $LocalPointsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
    'id',
    aliasedName,
    false,
    hasAutoIncrement: true,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'PRIMARY KEY AUTOINCREMENT',
    ),
  );
  static const VerificationMeta _rideIdMeta = const VerificationMeta('rideId');
  @override
  late final GeneratedColumn<String> rideId = GeneratedColumn<String>(
    'ride_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'REFERENCES local_rides (id)',
    ),
  );
  static const VerificationMeta _pointUuidMeta = const VerificationMeta(
    'pointUuid',
  );
  @override
  late final GeneratedColumn<String> pointUuid = GeneratedColumn<String>(
    'point_uuid',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways('UNIQUE'),
  );
  static const VerificationMeta _seqMeta = const VerificationMeta('seq');
  @override
  late final GeneratedColumn<int> seq = GeneratedColumn<int>(
    'seq',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _latMeta = const VerificationMeta('lat');
  @override
  late final GeneratedColumn<double> lat = GeneratedColumn<double>(
    'lat',
    aliasedName,
    false,
    type: DriftSqlType.double,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _lonMeta = const VerificationMeta('lon');
  @override
  late final GeneratedColumn<double> lon = GeneratedColumn<double>(
    'lon',
    aliasedName,
    false,
    type: DriftSqlType.double,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _altMeta = const VerificationMeta('alt');
  @override
  late final GeneratedColumn<double> alt = GeneratedColumn<double>(
    'alt',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _accuracyMeta = const VerificationMeta(
    'accuracy',
  );
  @override
  late final GeneratedColumn<double> accuracy = GeneratedColumn<double>(
    'accuracy',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _speedMeta = const VerificationMeta('speed');
  @override
  late final GeneratedColumn<double> speed = GeneratedColumn<double>(
    'speed',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _headingMeta = const VerificationMeta(
    'heading',
  );
  @override
  late final GeneratedColumn<double> heading = GeneratedColumn<double>(
    'heading',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _recordedAtMeta = const VerificationMeta(
    'recordedAt',
  );
  @override
  late final GeneratedColumn<DateTime> recordedAt = GeneratedColumn<DateTime>(
    'recorded_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _acceptedMeta = const VerificationMeta(
    'accepted',
  );
  @override
  late final GeneratedColumn<bool> accepted = GeneratedColumn<bool>(
    'accepted',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("accepted" IN (0, 1))',
    ),
    defaultValue: const Constant(true),
  );
  static const VerificationMeta _reasonMeta = const VerificationMeta('reason');
  @override
  late final GeneratedColumn<String> reason = GeneratedColumn<String>(
    'reason',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _uploadedMeta = const VerificationMeta(
    'uploaded',
  );
  @override
  late final GeneratedColumn<bool> uploaded = GeneratedColumn<bool>(
    'uploaded',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("uploaded" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    rideId,
    pointUuid,
    seq,
    lat,
    lon,
    alt,
    accuracy,
    speed,
    heading,
    recordedAt,
    accepted,
    reason,
    uploaded,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'local_points';
  @override
  VerificationContext validateIntegrity(
    Insertable<LocalPoint> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('ride_id')) {
      context.handle(
        _rideIdMeta,
        rideId.isAcceptableOrUnknown(data['ride_id']!, _rideIdMeta),
      );
    } else if (isInserting) {
      context.missing(_rideIdMeta);
    }
    if (data.containsKey('point_uuid')) {
      context.handle(
        _pointUuidMeta,
        pointUuid.isAcceptableOrUnknown(data['point_uuid']!, _pointUuidMeta),
      );
    } else if (isInserting) {
      context.missing(_pointUuidMeta);
    }
    if (data.containsKey('seq')) {
      context.handle(
        _seqMeta,
        seq.isAcceptableOrUnknown(data['seq']!, _seqMeta),
      );
    } else if (isInserting) {
      context.missing(_seqMeta);
    }
    if (data.containsKey('lat')) {
      context.handle(
        _latMeta,
        lat.isAcceptableOrUnknown(data['lat']!, _latMeta),
      );
    } else if (isInserting) {
      context.missing(_latMeta);
    }
    if (data.containsKey('lon')) {
      context.handle(
        _lonMeta,
        lon.isAcceptableOrUnknown(data['lon']!, _lonMeta),
      );
    } else if (isInserting) {
      context.missing(_lonMeta);
    }
    if (data.containsKey('alt')) {
      context.handle(
        _altMeta,
        alt.isAcceptableOrUnknown(data['alt']!, _altMeta),
      );
    }
    if (data.containsKey('accuracy')) {
      context.handle(
        _accuracyMeta,
        accuracy.isAcceptableOrUnknown(data['accuracy']!, _accuracyMeta),
      );
    }
    if (data.containsKey('speed')) {
      context.handle(
        _speedMeta,
        speed.isAcceptableOrUnknown(data['speed']!, _speedMeta),
      );
    }
    if (data.containsKey('heading')) {
      context.handle(
        _headingMeta,
        heading.isAcceptableOrUnknown(data['heading']!, _headingMeta),
      );
    }
    if (data.containsKey('recorded_at')) {
      context.handle(
        _recordedAtMeta,
        recordedAt.isAcceptableOrUnknown(data['recorded_at']!, _recordedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_recordedAtMeta);
    }
    if (data.containsKey('accepted')) {
      context.handle(
        _acceptedMeta,
        accepted.isAcceptableOrUnknown(data['accepted']!, _acceptedMeta),
      );
    }
    if (data.containsKey('reason')) {
      context.handle(
        _reasonMeta,
        reason.isAcceptableOrUnknown(data['reason']!, _reasonMeta),
      );
    }
    if (data.containsKey('uploaded')) {
      context.handle(
        _uploadedMeta,
        uploaded.isAcceptableOrUnknown(data['uploaded']!, _uploadedMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  LocalPoint map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return LocalPoint(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}id'],
      )!,
      rideId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}ride_id'],
      )!,
      pointUuid: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}point_uuid'],
      )!,
      seq: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}seq'],
      )!,
      lat: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}lat'],
      )!,
      lon: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}lon'],
      )!,
      alt: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}alt'],
      ),
      accuracy: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}accuracy'],
      ),
      speed: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}speed'],
      ),
      heading: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}heading'],
      ),
      recordedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}recorded_at'],
      )!,
      accepted: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}accepted'],
      )!,
      reason: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}reason'],
      ),
      uploaded: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}uploaded'],
      )!,
    );
  }

  @override
  $LocalPointsTable createAlias(String alias) {
    return $LocalPointsTable(attachedDatabase, alias);
  }
}

class LocalPoint extends DataClass implements Insertable<LocalPoint> {
  final int id;
  final String rideId;
  final String pointUuid;
  final int seq;
  final double lat;
  final double lon;
  final double? alt;
  final double? accuracy;
  final double? speed;
  final double? heading;
  final DateTime recordedAt;
  final bool accepted;
  final String? reason;
  final bool uploaded;
  const LocalPoint({
    required this.id,
    required this.rideId,
    required this.pointUuid,
    required this.seq,
    required this.lat,
    required this.lon,
    this.alt,
    this.accuracy,
    this.speed,
    this.heading,
    required this.recordedAt,
    required this.accepted,
    this.reason,
    required this.uploaded,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['ride_id'] = Variable<String>(rideId);
    map['point_uuid'] = Variable<String>(pointUuid);
    map['seq'] = Variable<int>(seq);
    map['lat'] = Variable<double>(lat);
    map['lon'] = Variable<double>(lon);
    if (!nullToAbsent || alt != null) {
      map['alt'] = Variable<double>(alt);
    }
    if (!nullToAbsent || accuracy != null) {
      map['accuracy'] = Variable<double>(accuracy);
    }
    if (!nullToAbsent || speed != null) {
      map['speed'] = Variable<double>(speed);
    }
    if (!nullToAbsent || heading != null) {
      map['heading'] = Variable<double>(heading);
    }
    map['recorded_at'] = Variable<DateTime>(recordedAt);
    map['accepted'] = Variable<bool>(accepted);
    if (!nullToAbsent || reason != null) {
      map['reason'] = Variable<String>(reason);
    }
    map['uploaded'] = Variable<bool>(uploaded);
    return map;
  }

  LocalPointsCompanion toCompanion(bool nullToAbsent) {
    return LocalPointsCompanion(
      id: Value(id),
      rideId: Value(rideId),
      pointUuid: Value(pointUuid),
      seq: Value(seq),
      lat: Value(lat),
      lon: Value(lon),
      alt: alt == null && nullToAbsent ? const Value.absent() : Value(alt),
      accuracy: accuracy == null && nullToAbsent
          ? const Value.absent()
          : Value(accuracy),
      speed: speed == null && nullToAbsent
          ? const Value.absent()
          : Value(speed),
      heading: heading == null && nullToAbsent
          ? const Value.absent()
          : Value(heading),
      recordedAt: Value(recordedAt),
      accepted: Value(accepted),
      reason: reason == null && nullToAbsent
          ? const Value.absent()
          : Value(reason),
      uploaded: Value(uploaded),
    );
  }

  factory LocalPoint.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return LocalPoint(
      id: serializer.fromJson<int>(json['id']),
      rideId: serializer.fromJson<String>(json['rideId']),
      pointUuid: serializer.fromJson<String>(json['pointUuid']),
      seq: serializer.fromJson<int>(json['seq']),
      lat: serializer.fromJson<double>(json['lat']),
      lon: serializer.fromJson<double>(json['lon']),
      alt: serializer.fromJson<double?>(json['alt']),
      accuracy: serializer.fromJson<double?>(json['accuracy']),
      speed: serializer.fromJson<double?>(json['speed']),
      heading: serializer.fromJson<double?>(json['heading']),
      recordedAt: serializer.fromJson<DateTime>(json['recordedAt']),
      accepted: serializer.fromJson<bool>(json['accepted']),
      reason: serializer.fromJson<String?>(json['reason']),
      uploaded: serializer.fromJson<bool>(json['uploaded']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'rideId': serializer.toJson<String>(rideId),
      'pointUuid': serializer.toJson<String>(pointUuid),
      'seq': serializer.toJson<int>(seq),
      'lat': serializer.toJson<double>(lat),
      'lon': serializer.toJson<double>(lon),
      'alt': serializer.toJson<double?>(alt),
      'accuracy': serializer.toJson<double?>(accuracy),
      'speed': serializer.toJson<double?>(speed),
      'heading': serializer.toJson<double?>(heading),
      'recordedAt': serializer.toJson<DateTime>(recordedAt),
      'accepted': serializer.toJson<bool>(accepted),
      'reason': serializer.toJson<String?>(reason),
      'uploaded': serializer.toJson<bool>(uploaded),
    };
  }

  LocalPoint copyWith({
    int? id,
    String? rideId,
    String? pointUuid,
    int? seq,
    double? lat,
    double? lon,
    Value<double?> alt = const Value.absent(),
    Value<double?> accuracy = const Value.absent(),
    Value<double?> speed = const Value.absent(),
    Value<double?> heading = const Value.absent(),
    DateTime? recordedAt,
    bool? accepted,
    Value<String?> reason = const Value.absent(),
    bool? uploaded,
  }) => LocalPoint(
    id: id ?? this.id,
    rideId: rideId ?? this.rideId,
    pointUuid: pointUuid ?? this.pointUuid,
    seq: seq ?? this.seq,
    lat: lat ?? this.lat,
    lon: lon ?? this.lon,
    alt: alt.present ? alt.value : this.alt,
    accuracy: accuracy.present ? accuracy.value : this.accuracy,
    speed: speed.present ? speed.value : this.speed,
    heading: heading.present ? heading.value : this.heading,
    recordedAt: recordedAt ?? this.recordedAt,
    accepted: accepted ?? this.accepted,
    reason: reason.present ? reason.value : this.reason,
    uploaded: uploaded ?? this.uploaded,
  );
  LocalPoint copyWithCompanion(LocalPointsCompanion data) {
    return LocalPoint(
      id: data.id.present ? data.id.value : this.id,
      rideId: data.rideId.present ? data.rideId.value : this.rideId,
      pointUuid: data.pointUuid.present ? data.pointUuid.value : this.pointUuid,
      seq: data.seq.present ? data.seq.value : this.seq,
      lat: data.lat.present ? data.lat.value : this.lat,
      lon: data.lon.present ? data.lon.value : this.lon,
      alt: data.alt.present ? data.alt.value : this.alt,
      accuracy: data.accuracy.present ? data.accuracy.value : this.accuracy,
      speed: data.speed.present ? data.speed.value : this.speed,
      heading: data.heading.present ? data.heading.value : this.heading,
      recordedAt: data.recordedAt.present
          ? data.recordedAt.value
          : this.recordedAt,
      accepted: data.accepted.present ? data.accepted.value : this.accepted,
      reason: data.reason.present ? data.reason.value : this.reason,
      uploaded: data.uploaded.present ? data.uploaded.value : this.uploaded,
    );
  }

  @override
  String toString() {
    return (StringBuffer('LocalPoint(')
          ..write('id: $id, ')
          ..write('rideId: $rideId, ')
          ..write('pointUuid: $pointUuid, ')
          ..write('seq: $seq, ')
          ..write('lat: $lat, ')
          ..write('lon: $lon, ')
          ..write('alt: $alt, ')
          ..write('accuracy: $accuracy, ')
          ..write('speed: $speed, ')
          ..write('heading: $heading, ')
          ..write('recordedAt: $recordedAt, ')
          ..write('accepted: $accepted, ')
          ..write('reason: $reason, ')
          ..write('uploaded: $uploaded')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    rideId,
    pointUuid,
    seq,
    lat,
    lon,
    alt,
    accuracy,
    speed,
    heading,
    recordedAt,
    accepted,
    reason,
    uploaded,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is LocalPoint &&
          other.id == this.id &&
          other.rideId == this.rideId &&
          other.pointUuid == this.pointUuid &&
          other.seq == this.seq &&
          other.lat == this.lat &&
          other.lon == this.lon &&
          other.alt == this.alt &&
          other.accuracy == this.accuracy &&
          other.speed == this.speed &&
          other.heading == this.heading &&
          other.recordedAt == this.recordedAt &&
          other.accepted == this.accepted &&
          other.reason == this.reason &&
          other.uploaded == this.uploaded);
}

class LocalPointsCompanion extends UpdateCompanion<LocalPoint> {
  final Value<int> id;
  final Value<String> rideId;
  final Value<String> pointUuid;
  final Value<int> seq;
  final Value<double> lat;
  final Value<double> lon;
  final Value<double?> alt;
  final Value<double?> accuracy;
  final Value<double?> speed;
  final Value<double?> heading;
  final Value<DateTime> recordedAt;
  final Value<bool> accepted;
  final Value<String?> reason;
  final Value<bool> uploaded;
  const LocalPointsCompanion({
    this.id = const Value.absent(),
    this.rideId = const Value.absent(),
    this.pointUuid = const Value.absent(),
    this.seq = const Value.absent(),
    this.lat = const Value.absent(),
    this.lon = const Value.absent(),
    this.alt = const Value.absent(),
    this.accuracy = const Value.absent(),
    this.speed = const Value.absent(),
    this.heading = const Value.absent(),
    this.recordedAt = const Value.absent(),
    this.accepted = const Value.absent(),
    this.reason = const Value.absent(),
    this.uploaded = const Value.absent(),
  });
  LocalPointsCompanion.insert({
    this.id = const Value.absent(),
    required String rideId,
    required String pointUuid,
    required int seq,
    required double lat,
    required double lon,
    this.alt = const Value.absent(),
    this.accuracy = const Value.absent(),
    this.speed = const Value.absent(),
    this.heading = const Value.absent(),
    required DateTime recordedAt,
    this.accepted = const Value.absent(),
    this.reason = const Value.absent(),
    this.uploaded = const Value.absent(),
  }) : rideId = Value(rideId),
       pointUuid = Value(pointUuid),
       seq = Value(seq),
       lat = Value(lat),
       lon = Value(lon),
       recordedAt = Value(recordedAt);
  static Insertable<LocalPoint> custom({
    Expression<int>? id,
    Expression<String>? rideId,
    Expression<String>? pointUuid,
    Expression<int>? seq,
    Expression<double>? lat,
    Expression<double>? lon,
    Expression<double>? alt,
    Expression<double>? accuracy,
    Expression<double>? speed,
    Expression<double>? heading,
    Expression<DateTime>? recordedAt,
    Expression<bool>? accepted,
    Expression<String>? reason,
    Expression<bool>? uploaded,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (rideId != null) 'ride_id': rideId,
      if (pointUuid != null) 'point_uuid': pointUuid,
      if (seq != null) 'seq': seq,
      if (lat != null) 'lat': lat,
      if (lon != null) 'lon': lon,
      if (alt != null) 'alt': alt,
      if (accuracy != null) 'accuracy': accuracy,
      if (speed != null) 'speed': speed,
      if (heading != null) 'heading': heading,
      if (recordedAt != null) 'recorded_at': recordedAt,
      if (accepted != null) 'accepted': accepted,
      if (reason != null) 'reason': reason,
      if (uploaded != null) 'uploaded': uploaded,
    });
  }

  LocalPointsCompanion copyWith({
    Value<int>? id,
    Value<String>? rideId,
    Value<String>? pointUuid,
    Value<int>? seq,
    Value<double>? lat,
    Value<double>? lon,
    Value<double?>? alt,
    Value<double?>? accuracy,
    Value<double?>? speed,
    Value<double?>? heading,
    Value<DateTime>? recordedAt,
    Value<bool>? accepted,
    Value<String?>? reason,
    Value<bool>? uploaded,
  }) {
    return LocalPointsCompanion(
      id: id ?? this.id,
      rideId: rideId ?? this.rideId,
      pointUuid: pointUuid ?? this.pointUuid,
      seq: seq ?? this.seq,
      lat: lat ?? this.lat,
      lon: lon ?? this.lon,
      alt: alt ?? this.alt,
      accuracy: accuracy ?? this.accuracy,
      speed: speed ?? this.speed,
      heading: heading ?? this.heading,
      recordedAt: recordedAt ?? this.recordedAt,
      accepted: accepted ?? this.accepted,
      reason: reason ?? this.reason,
      uploaded: uploaded ?? this.uploaded,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (rideId.present) {
      map['ride_id'] = Variable<String>(rideId.value);
    }
    if (pointUuid.present) {
      map['point_uuid'] = Variable<String>(pointUuid.value);
    }
    if (seq.present) {
      map['seq'] = Variable<int>(seq.value);
    }
    if (lat.present) {
      map['lat'] = Variable<double>(lat.value);
    }
    if (lon.present) {
      map['lon'] = Variable<double>(lon.value);
    }
    if (alt.present) {
      map['alt'] = Variable<double>(alt.value);
    }
    if (accuracy.present) {
      map['accuracy'] = Variable<double>(accuracy.value);
    }
    if (speed.present) {
      map['speed'] = Variable<double>(speed.value);
    }
    if (heading.present) {
      map['heading'] = Variable<double>(heading.value);
    }
    if (recordedAt.present) {
      map['recorded_at'] = Variable<DateTime>(recordedAt.value);
    }
    if (accepted.present) {
      map['accepted'] = Variable<bool>(accepted.value);
    }
    if (reason.present) {
      map['reason'] = Variable<String>(reason.value);
    }
    if (uploaded.present) {
      map['uploaded'] = Variable<bool>(uploaded.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('LocalPointsCompanion(')
          ..write('id: $id, ')
          ..write('rideId: $rideId, ')
          ..write('pointUuid: $pointUuid, ')
          ..write('seq: $seq, ')
          ..write('lat: $lat, ')
          ..write('lon: $lon, ')
          ..write('alt: $alt, ')
          ..write('accuracy: $accuracy, ')
          ..write('speed: $speed, ')
          ..write('heading: $heading, ')
          ..write('recordedAt: $recordedAt, ')
          ..write('accepted: $accepted, ')
          ..write('reason: $reason, ')
          ..write('uploaded: $uploaded')
          ..write(')'))
        .toString();
  }
}

class $CachedRoutesTable extends CachedRoutes
    with TableInfo<$CachedRoutesTable, CachedRoute> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $CachedRoutesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _routeJsonMeta = const VerificationMeta(
    'routeJson',
  );
  @override
  late final GeneratedColumn<String> routeJson = GeneratedColumn<String>(
    'route_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _geometryJsonMeta = const VerificationMeta(
    'geometryJson',
  );
  @override
  late final GeneratedColumn<String> geometryJson = GeneratedColumn<String>(
    'geometry_json',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _versionNoMeta = const VerificationMeta(
    'versionNo',
  );
  @override
  late final GeneratedColumn<int> versionNo = GeneratedColumn<int>(
    'version_no',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _savedAtMeta = const VerificationMeta(
    'savedAt',
  );
  @override
  late final GeneratedColumn<DateTime> savedAt = GeneratedColumn<DateTime>(
    'saved_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    routeJson,
    geometryJson,
    versionNo,
    savedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'cached_routes';
  @override
  VerificationContext validateIntegrity(
    Insertable<CachedRoute> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('route_json')) {
      context.handle(
        _routeJsonMeta,
        routeJson.isAcceptableOrUnknown(data['route_json']!, _routeJsonMeta),
      );
    } else if (isInserting) {
      context.missing(_routeJsonMeta);
    }
    if (data.containsKey('geometry_json')) {
      context.handle(
        _geometryJsonMeta,
        geometryJson.isAcceptableOrUnknown(
          data['geometry_json']!,
          _geometryJsonMeta,
        ),
      );
    }
    if (data.containsKey('version_no')) {
      context.handle(
        _versionNoMeta,
        versionNo.isAcceptableOrUnknown(data['version_no']!, _versionNoMeta),
      );
    }
    if (data.containsKey('saved_at')) {
      context.handle(
        _savedAtMeta,
        savedAt.isAcceptableOrUnknown(data['saved_at']!, _savedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_savedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  CachedRoute map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return CachedRoute(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      routeJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}route_json'],
      )!,
      geometryJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}geometry_json'],
      ),
      versionNo: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}version_no'],
      )!,
      savedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}saved_at'],
      )!,
    );
  }

  @override
  $CachedRoutesTable createAlias(String alias) {
    return $CachedRoutesTable(attachedDatabase, alias);
  }
}

class CachedRoute extends DataClass implements Insertable<CachedRoute> {
  final String id;
  final String routeJson;
  final String? geometryJson;
  final int versionNo;
  final DateTime savedAt;
  const CachedRoute({
    required this.id,
    required this.routeJson,
    this.geometryJson,
    required this.versionNo,
    required this.savedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['route_json'] = Variable<String>(routeJson);
    if (!nullToAbsent || geometryJson != null) {
      map['geometry_json'] = Variable<String>(geometryJson);
    }
    map['version_no'] = Variable<int>(versionNo);
    map['saved_at'] = Variable<DateTime>(savedAt);
    return map;
  }

  CachedRoutesCompanion toCompanion(bool nullToAbsent) {
    return CachedRoutesCompanion(
      id: Value(id),
      routeJson: Value(routeJson),
      geometryJson: geometryJson == null && nullToAbsent
          ? const Value.absent()
          : Value(geometryJson),
      versionNo: Value(versionNo),
      savedAt: Value(savedAt),
    );
  }

  factory CachedRoute.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return CachedRoute(
      id: serializer.fromJson<String>(json['id']),
      routeJson: serializer.fromJson<String>(json['routeJson']),
      geometryJson: serializer.fromJson<String?>(json['geometryJson']),
      versionNo: serializer.fromJson<int>(json['versionNo']),
      savedAt: serializer.fromJson<DateTime>(json['savedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'routeJson': serializer.toJson<String>(routeJson),
      'geometryJson': serializer.toJson<String?>(geometryJson),
      'versionNo': serializer.toJson<int>(versionNo),
      'savedAt': serializer.toJson<DateTime>(savedAt),
    };
  }

  CachedRoute copyWith({
    String? id,
    String? routeJson,
    Value<String?> geometryJson = const Value.absent(),
    int? versionNo,
    DateTime? savedAt,
  }) => CachedRoute(
    id: id ?? this.id,
    routeJson: routeJson ?? this.routeJson,
    geometryJson: geometryJson.present ? geometryJson.value : this.geometryJson,
    versionNo: versionNo ?? this.versionNo,
    savedAt: savedAt ?? this.savedAt,
  );
  CachedRoute copyWithCompanion(CachedRoutesCompanion data) {
    return CachedRoute(
      id: data.id.present ? data.id.value : this.id,
      routeJson: data.routeJson.present ? data.routeJson.value : this.routeJson,
      geometryJson: data.geometryJson.present
          ? data.geometryJson.value
          : this.geometryJson,
      versionNo: data.versionNo.present ? data.versionNo.value : this.versionNo,
      savedAt: data.savedAt.present ? data.savedAt.value : this.savedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('CachedRoute(')
          ..write('id: $id, ')
          ..write('routeJson: $routeJson, ')
          ..write('geometryJson: $geometryJson, ')
          ..write('versionNo: $versionNo, ')
          ..write('savedAt: $savedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode =>
      Object.hash(id, routeJson, geometryJson, versionNo, savedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is CachedRoute &&
          other.id == this.id &&
          other.routeJson == this.routeJson &&
          other.geometryJson == this.geometryJson &&
          other.versionNo == this.versionNo &&
          other.savedAt == this.savedAt);
}

class CachedRoutesCompanion extends UpdateCompanion<CachedRoute> {
  final Value<String> id;
  final Value<String> routeJson;
  final Value<String?> geometryJson;
  final Value<int> versionNo;
  final Value<DateTime> savedAt;
  final Value<int> rowid;
  const CachedRoutesCompanion({
    this.id = const Value.absent(),
    this.routeJson = const Value.absent(),
    this.geometryJson = const Value.absent(),
    this.versionNo = const Value.absent(),
    this.savedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  CachedRoutesCompanion.insert({
    required String id,
    required String routeJson,
    this.geometryJson = const Value.absent(),
    this.versionNo = const Value.absent(),
    required DateTime savedAt,
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       routeJson = Value(routeJson),
       savedAt = Value(savedAt);
  static Insertable<CachedRoute> custom({
    Expression<String>? id,
    Expression<String>? routeJson,
    Expression<String>? geometryJson,
    Expression<int>? versionNo,
    Expression<DateTime>? savedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (routeJson != null) 'route_json': routeJson,
      if (geometryJson != null) 'geometry_json': geometryJson,
      if (versionNo != null) 'version_no': versionNo,
      if (savedAt != null) 'saved_at': savedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  CachedRoutesCompanion copyWith({
    Value<String>? id,
    Value<String>? routeJson,
    Value<String?>? geometryJson,
    Value<int>? versionNo,
    Value<DateTime>? savedAt,
    Value<int>? rowid,
  }) {
    return CachedRoutesCompanion(
      id: id ?? this.id,
      routeJson: routeJson ?? this.routeJson,
      geometryJson: geometryJson ?? this.geometryJson,
      versionNo: versionNo ?? this.versionNo,
      savedAt: savedAt ?? this.savedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (routeJson.present) {
      map['route_json'] = Variable<String>(routeJson.value);
    }
    if (geometryJson.present) {
      map['geometry_json'] = Variable<String>(geometryJson.value);
    }
    if (versionNo.present) {
      map['version_no'] = Variable<int>(versionNo.value);
    }
    if (savedAt.present) {
      map['saved_at'] = Variable<DateTime>(savedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('CachedRoutesCompanion(')
          ..write('id: $id, ')
          ..write('routeJson: $routeJson, ')
          ..write('geometryJson: $geometryJson, ')
          ..write('versionNo: $versionNo, ')
          ..write('savedAt: $savedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

abstract class _$RideDatabase extends GeneratedDatabase {
  _$RideDatabase(QueryExecutor e) : super(e);
  $RideDatabaseManager get managers => $RideDatabaseManager(this);
  late final $LocalRidesTable localRides = $LocalRidesTable(this);
  late final $LocalPointsTable localPoints = $LocalPointsTable(this);
  late final $CachedRoutesTable cachedRoutes = $CachedRoutesTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities => [
    localRides,
    localPoints,
    cachedRoutes,
  ];
}

typedef $$LocalRidesTableCreateCompanionBuilder =
    LocalRidesCompanion Function({
      required String id,
      Value<String?> serverId,
      required String bikeId,
      Value<String?> routeId,
      Value<String> status,
      required DateTime startedAt,
      Value<DateTime?> endedAt,
      Value<int> uploadedSeq,
      required DateTime updatedAt,
      Value<int> rowid,
    });
typedef $$LocalRidesTableUpdateCompanionBuilder =
    LocalRidesCompanion Function({
      Value<String> id,
      Value<String?> serverId,
      Value<String> bikeId,
      Value<String?> routeId,
      Value<String> status,
      Value<DateTime> startedAt,
      Value<DateTime?> endedAt,
      Value<int> uploadedSeq,
      Value<DateTime> updatedAt,
      Value<int> rowid,
    });

final class $$LocalRidesTableReferences
    extends BaseReferences<_$RideDatabase, $LocalRidesTable, LocalRide> {
  $$LocalRidesTableReferences(super.$_db, super.$_table, super.$_typedResult);

  static MultiTypedResultKey<$LocalPointsTable, List<LocalPoint>>
  _localPointsRefsTable(_$RideDatabase db) => MultiTypedResultKey.fromTable(
    db.localPoints,
    aliasName: 'local_rides__id__local_points__ride_id',
  );

  $$LocalPointsTableProcessedTableManager get localPointsRefs {
    final manager = $$LocalPointsTableTableManager(
      $_db,
      $_db.localPoints,
    ).filter((f) => f.rideId.id.sqlEquals($_itemColumn<String>('id')!));

    final cache = $_typedResult.readTableOrNull(_localPointsRefsTable($_db));
    return ProcessedTableManager(
      manager.$state.copyWith(prefetchedData: cache),
    );
  }
}

class $$LocalRidesTableFilterComposer
    extends Composer<_$RideDatabase, $LocalRidesTable> {
  $$LocalRidesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get serverId => $composableBuilder(
    column: $table.serverId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get bikeId => $composableBuilder(
    column: $table.bikeId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get routeId => $composableBuilder(
    column: $table.routeId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get startedAt => $composableBuilder(
    column: $table.startedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get endedAt => $composableBuilder(
    column: $table.endedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get uploadedSeq => $composableBuilder(
    column: $table.uploadedSeq,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );

  Expression<bool> localPointsRefs(
    Expression<bool> Function($$LocalPointsTableFilterComposer f) f,
  ) {
    final $$LocalPointsTableFilterComposer composer = $composerBuilder(
      composer: this,
      getCurrentColumn: (t) => t.id,
      referencedTable: $db.localPoints,
      getReferencedColumn: (t) => t.rideId,
      builder:
          (
            joinBuilder, {
            $addJoinBuilderToRootComposer,
            $removeJoinBuilderFromRootComposer,
          }) => $$LocalPointsTableFilterComposer(
            $db: $db,
            $table: $db.localPoints,
            $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
            joinBuilder: joinBuilder,
            $removeJoinBuilderFromRootComposer:
                $removeJoinBuilderFromRootComposer,
          ),
    );
    return f(composer);
  }
}

class $$LocalRidesTableOrderingComposer
    extends Composer<_$RideDatabase, $LocalRidesTable> {
  $$LocalRidesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get serverId => $composableBuilder(
    column: $table.serverId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get bikeId => $composableBuilder(
    column: $table.bikeId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get routeId => $composableBuilder(
    column: $table.routeId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get startedAt => $composableBuilder(
    column: $table.startedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get endedAt => $composableBuilder(
    column: $table.endedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get uploadedSeq => $composableBuilder(
    column: $table.uploadedSeq,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$LocalRidesTableAnnotationComposer
    extends Composer<_$RideDatabase, $LocalRidesTable> {
  $$LocalRidesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get serverId =>
      $composableBuilder(column: $table.serverId, builder: (column) => column);

  GeneratedColumn<String> get bikeId =>
      $composableBuilder(column: $table.bikeId, builder: (column) => column);

  GeneratedColumn<String> get routeId =>
      $composableBuilder(column: $table.routeId, builder: (column) => column);

  GeneratedColumn<String> get status =>
      $composableBuilder(column: $table.status, builder: (column) => column);

  GeneratedColumn<DateTime> get startedAt =>
      $composableBuilder(column: $table.startedAt, builder: (column) => column);

  GeneratedColumn<DateTime> get endedAt =>
      $composableBuilder(column: $table.endedAt, builder: (column) => column);

  GeneratedColumn<int> get uploadedSeq => $composableBuilder(
    column: $table.uploadedSeq,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);

  Expression<T> localPointsRefs<T extends Object>(
    Expression<T> Function($$LocalPointsTableAnnotationComposer a) f,
  ) {
    final $$LocalPointsTableAnnotationComposer composer = $composerBuilder(
      composer: this,
      getCurrentColumn: (t) => t.id,
      referencedTable: $db.localPoints,
      getReferencedColumn: (t) => t.rideId,
      builder:
          (
            joinBuilder, {
            $addJoinBuilderToRootComposer,
            $removeJoinBuilderFromRootComposer,
          }) => $$LocalPointsTableAnnotationComposer(
            $db: $db,
            $table: $db.localPoints,
            $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
            joinBuilder: joinBuilder,
            $removeJoinBuilderFromRootComposer:
                $removeJoinBuilderFromRootComposer,
          ),
    );
    return f(composer);
  }
}

class $$LocalRidesTableTableManager
    extends
        RootTableManager<
          _$RideDatabase,
          $LocalRidesTable,
          LocalRide,
          $$LocalRidesTableFilterComposer,
          $$LocalRidesTableOrderingComposer,
          $$LocalRidesTableAnnotationComposer,
          $$LocalRidesTableCreateCompanionBuilder,
          $$LocalRidesTableUpdateCompanionBuilder,
          (LocalRide, $$LocalRidesTableReferences),
          LocalRide,
          PrefetchHooks Function({bool localPointsRefs})
        > {
  $$LocalRidesTableTableManager(_$RideDatabase db, $LocalRidesTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$LocalRidesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$LocalRidesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$LocalRidesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String?> serverId = const Value.absent(),
                Value<String> bikeId = const Value.absent(),
                Value<String?> routeId = const Value.absent(),
                Value<String> status = const Value.absent(),
                Value<DateTime> startedAt = const Value.absent(),
                Value<DateTime?> endedAt = const Value.absent(),
                Value<int> uploadedSeq = const Value.absent(),
                Value<DateTime> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => LocalRidesCompanion(
                id: id,
                serverId: serverId,
                bikeId: bikeId,
                routeId: routeId,
                status: status,
                startedAt: startedAt,
                endedAt: endedAt,
                uploadedSeq: uploadedSeq,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                Value<String?> serverId = const Value.absent(),
                required String bikeId,
                Value<String?> routeId = const Value.absent(),
                Value<String> status = const Value.absent(),
                required DateTime startedAt,
                Value<DateTime?> endedAt = const Value.absent(),
                Value<int> uploadedSeq = const Value.absent(),
                required DateTime updatedAt,
                Value<int> rowid = const Value.absent(),
              }) => LocalRidesCompanion.insert(
                id: id,
                serverId: serverId,
                bikeId: bikeId,
                routeId: routeId,
                status: status,
                startedAt: startedAt,
                endedAt: endedAt,
                uploadedSeq: uploadedSeq,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<$LocalRidesTable, LocalRide>(table),
                  $$LocalRidesTableReferences(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: ({localPointsRefs = false}) {
            return PrefetchHooks(
              db: db,
              explicitlyWatchedTables: [if (localPointsRefs) db.localPoints],
              addJoins: null,
              getPrefetchedDataCallback: (items) async {
                return [
                  if (localPointsRefs)
                    await $_getPrefetchedData<
                      LocalRide,
                      $LocalRidesTable,
                      LocalPoint
                    >(
                      currentTable: table,
                      referencedTable: $$LocalRidesTableReferences
                          ._localPointsRefsTable(db),
                      managerFromTypedResult: (p0) =>
                          $$LocalRidesTableReferences(
                            db,
                            table,
                            p0,
                          ).localPointsRefs,
                      referencedItemsForCurrentItem: (item, referencedItems) =>
                          referencedItems.where((e) => e.rideId == item.id),
                      typedResults: items,
                    ),
                ];
              },
            );
          },
        ),
      );
}

typedef $$LocalRidesTableProcessedTableManager =
    ProcessedTableManager<
      _$RideDatabase,
      $LocalRidesTable,
      LocalRide,
      $$LocalRidesTableFilterComposer,
      $$LocalRidesTableOrderingComposer,
      $$LocalRidesTableAnnotationComposer,
      $$LocalRidesTableCreateCompanionBuilder,
      $$LocalRidesTableUpdateCompanionBuilder,
      (LocalRide, $$LocalRidesTableReferences),
      LocalRide,
      PrefetchHooks Function({bool localPointsRefs})
    >;
typedef $$LocalPointsTableCreateCompanionBuilder =
    LocalPointsCompanion Function({
      Value<int> id,
      required String rideId,
      required String pointUuid,
      required int seq,
      required double lat,
      required double lon,
      Value<double?> alt,
      Value<double?> accuracy,
      Value<double?> speed,
      Value<double?> heading,
      required DateTime recordedAt,
      Value<bool> accepted,
      Value<String?> reason,
      Value<bool> uploaded,
    });
typedef $$LocalPointsTableUpdateCompanionBuilder =
    LocalPointsCompanion Function({
      Value<int> id,
      Value<String> rideId,
      Value<String> pointUuid,
      Value<int> seq,
      Value<double> lat,
      Value<double> lon,
      Value<double?> alt,
      Value<double?> accuracy,
      Value<double?> speed,
      Value<double?> heading,
      Value<DateTime> recordedAt,
      Value<bool> accepted,
      Value<String?> reason,
      Value<bool> uploaded,
    });

final class $$LocalPointsTableReferences
    extends BaseReferences<_$RideDatabase, $LocalPointsTable, LocalPoint> {
  $$LocalPointsTableReferences(super.$_db, super.$_table, super.$_typedResult);

  static $LocalRidesTable _rideIdTable(_$RideDatabase db) =>
      db.localRides.createAlias('local_points__ride_id__local_rides__id');

  $$LocalRidesTableProcessedTableManager get rideId {
    final $_column = $_itemColumn<String>('ride_id')!;

    final manager = $$LocalRidesTableTableManager(
      $_db,
      $_db.localRides,
    ).filter((f) => f.id.sqlEquals($_column));
    final item = $_typedResult.readTableOrNull(_rideIdTable($_db));
    if (item == null) return manager;
    return ProcessedTableManager(
      manager.$state.copyWith(prefetchedData: [item]),
    );
  }
}

class $$LocalPointsTableFilterComposer
    extends Composer<_$RideDatabase, $LocalPointsTable> {
  $$LocalPointsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get pointUuid => $composableBuilder(
    column: $table.pointUuid,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get seq => $composableBuilder(
    column: $table.seq,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get lat => $composableBuilder(
    column: $table.lat,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get lon => $composableBuilder(
    column: $table.lon,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get alt => $composableBuilder(
    column: $table.alt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get accuracy => $composableBuilder(
    column: $table.accuracy,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get speed => $composableBuilder(
    column: $table.speed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get heading => $composableBuilder(
    column: $table.heading,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get recordedAt => $composableBuilder(
    column: $table.recordedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get accepted => $composableBuilder(
    column: $table.accepted,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get reason => $composableBuilder(
    column: $table.reason,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get uploaded => $composableBuilder(
    column: $table.uploaded,
    builder: (column) => ColumnFilters(column),
  );

  $$LocalRidesTableFilterComposer get rideId {
    final $$LocalRidesTableFilterComposer composer = $composerBuilder(
      composer: this,
      getCurrentColumn: (t) => t.rideId,
      referencedTable: $db.localRides,
      getReferencedColumn: (t) => t.id,
      builder:
          (
            joinBuilder, {
            $addJoinBuilderToRootComposer,
            $removeJoinBuilderFromRootComposer,
          }) => $$LocalRidesTableFilterComposer(
            $db: $db,
            $table: $db.localRides,
            $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
            joinBuilder: joinBuilder,
            $removeJoinBuilderFromRootComposer:
                $removeJoinBuilderFromRootComposer,
          ),
    );
    return composer;
  }
}

class $$LocalPointsTableOrderingComposer
    extends Composer<_$RideDatabase, $LocalPointsTable> {
  $$LocalPointsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get pointUuid => $composableBuilder(
    column: $table.pointUuid,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get seq => $composableBuilder(
    column: $table.seq,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get lat => $composableBuilder(
    column: $table.lat,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get lon => $composableBuilder(
    column: $table.lon,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get alt => $composableBuilder(
    column: $table.alt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get accuracy => $composableBuilder(
    column: $table.accuracy,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get speed => $composableBuilder(
    column: $table.speed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get heading => $composableBuilder(
    column: $table.heading,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get recordedAt => $composableBuilder(
    column: $table.recordedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get accepted => $composableBuilder(
    column: $table.accepted,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get reason => $composableBuilder(
    column: $table.reason,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get uploaded => $composableBuilder(
    column: $table.uploaded,
    builder: (column) => ColumnOrderings(column),
  );

  $$LocalRidesTableOrderingComposer get rideId {
    final $$LocalRidesTableOrderingComposer composer = $composerBuilder(
      composer: this,
      getCurrentColumn: (t) => t.rideId,
      referencedTable: $db.localRides,
      getReferencedColumn: (t) => t.id,
      builder:
          (
            joinBuilder, {
            $addJoinBuilderToRootComposer,
            $removeJoinBuilderFromRootComposer,
          }) => $$LocalRidesTableOrderingComposer(
            $db: $db,
            $table: $db.localRides,
            $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
            joinBuilder: joinBuilder,
            $removeJoinBuilderFromRootComposer:
                $removeJoinBuilderFromRootComposer,
          ),
    );
    return composer;
  }
}

class $$LocalPointsTableAnnotationComposer
    extends Composer<_$RideDatabase, $LocalPointsTable> {
  $$LocalPointsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get pointUuid =>
      $composableBuilder(column: $table.pointUuid, builder: (column) => column);

  GeneratedColumn<int> get seq =>
      $composableBuilder(column: $table.seq, builder: (column) => column);

  GeneratedColumn<double> get lat =>
      $composableBuilder(column: $table.lat, builder: (column) => column);

  GeneratedColumn<double> get lon =>
      $composableBuilder(column: $table.lon, builder: (column) => column);

  GeneratedColumn<double> get alt =>
      $composableBuilder(column: $table.alt, builder: (column) => column);

  GeneratedColumn<double> get accuracy =>
      $composableBuilder(column: $table.accuracy, builder: (column) => column);

  GeneratedColumn<double> get speed =>
      $composableBuilder(column: $table.speed, builder: (column) => column);

  GeneratedColumn<double> get heading =>
      $composableBuilder(column: $table.heading, builder: (column) => column);

  GeneratedColumn<DateTime> get recordedAt => $composableBuilder(
    column: $table.recordedAt,
    builder: (column) => column,
  );

  GeneratedColumn<bool> get accepted =>
      $composableBuilder(column: $table.accepted, builder: (column) => column);

  GeneratedColumn<String> get reason =>
      $composableBuilder(column: $table.reason, builder: (column) => column);

  GeneratedColumn<bool> get uploaded =>
      $composableBuilder(column: $table.uploaded, builder: (column) => column);

  $$LocalRidesTableAnnotationComposer get rideId {
    final $$LocalRidesTableAnnotationComposer composer = $composerBuilder(
      composer: this,
      getCurrentColumn: (t) => t.rideId,
      referencedTable: $db.localRides,
      getReferencedColumn: (t) => t.id,
      builder:
          (
            joinBuilder, {
            $addJoinBuilderToRootComposer,
            $removeJoinBuilderFromRootComposer,
          }) => $$LocalRidesTableAnnotationComposer(
            $db: $db,
            $table: $db.localRides,
            $addJoinBuilderToRootComposer: $addJoinBuilderToRootComposer,
            joinBuilder: joinBuilder,
            $removeJoinBuilderFromRootComposer:
                $removeJoinBuilderFromRootComposer,
          ),
    );
    return composer;
  }
}

class $$LocalPointsTableTableManager
    extends
        RootTableManager<
          _$RideDatabase,
          $LocalPointsTable,
          LocalPoint,
          $$LocalPointsTableFilterComposer,
          $$LocalPointsTableOrderingComposer,
          $$LocalPointsTableAnnotationComposer,
          $$LocalPointsTableCreateCompanionBuilder,
          $$LocalPointsTableUpdateCompanionBuilder,
          (LocalPoint, $$LocalPointsTableReferences),
          LocalPoint,
          PrefetchHooks Function({bool rideId})
        > {
  $$LocalPointsTableTableManager(_$RideDatabase db, $LocalPointsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$LocalPointsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$LocalPointsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$LocalPointsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                Value<String> rideId = const Value.absent(),
                Value<String> pointUuid = const Value.absent(),
                Value<int> seq = const Value.absent(),
                Value<double> lat = const Value.absent(),
                Value<double> lon = const Value.absent(),
                Value<double?> alt = const Value.absent(),
                Value<double?> accuracy = const Value.absent(),
                Value<double?> speed = const Value.absent(),
                Value<double?> heading = const Value.absent(),
                Value<DateTime> recordedAt = const Value.absent(),
                Value<bool> accepted = const Value.absent(),
                Value<String?> reason = const Value.absent(),
                Value<bool> uploaded = const Value.absent(),
              }) => LocalPointsCompanion(
                id: id,
                rideId: rideId,
                pointUuid: pointUuid,
                seq: seq,
                lat: lat,
                lon: lon,
                alt: alt,
                accuracy: accuracy,
                speed: speed,
                heading: heading,
                recordedAt: recordedAt,
                accepted: accepted,
                reason: reason,
                uploaded: uploaded,
              ),
          createCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                required String rideId,
                required String pointUuid,
                required int seq,
                required double lat,
                required double lon,
                Value<double?> alt = const Value.absent(),
                Value<double?> accuracy = const Value.absent(),
                Value<double?> speed = const Value.absent(),
                Value<double?> heading = const Value.absent(),
                required DateTime recordedAt,
                Value<bool> accepted = const Value.absent(),
                Value<String?> reason = const Value.absent(),
                Value<bool> uploaded = const Value.absent(),
              }) => LocalPointsCompanion.insert(
                id: id,
                rideId: rideId,
                pointUuid: pointUuid,
                seq: seq,
                lat: lat,
                lon: lon,
                alt: alt,
                accuracy: accuracy,
                speed: speed,
                heading: heading,
                recordedAt: recordedAt,
                accepted: accepted,
                reason: reason,
                uploaded: uploaded,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<$LocalPointsTable, LocalPoint>(table),
                  $$LocalPointsTableReferences(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: ({rideId = false}) {
            return PrefetchHooks(
              db: db,
              explicitlyWatchedTables: [],
              addJoins:
                  <
                    T extends TableManagerState<
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic,
                      dynamic
                    >
                  >(state) {
                    if (rideId) {
                      state =
                          state.withJoin(
                                currentTable: table,
                                currentColumn: table.rideId,
                                referencedTable: $$LocalPointsTableReferences
                                    ._rideIdTable(db),
                                referencedColumn: $$LocalPointsTableReferences
                                    ._rideIdTable(db)
                                    .id,
                              )
                              as T;
                    }

                    return state;
                  },
              getPrefetchedDataCallback: (items) async {
                return [];
              },
            );
          },
        ),
      );
}

typedef $$LocalPointsTableProcessedTableManager =
    ProcessedTableManager<
      _$RideDatabase,
      $LocalPointsTable,
      LocalPoint,
      $$LocalPointsTableFilterComposer,
      $$LocalPointsTableOrderingComposer,
      $$LocalPointsTableAnnotationComposer,
      $$LocalPointsTableCreateCompanionBuilder,
      $$LocalPointsTableUpdateCompanionBuilder,
      (LocalPoint, $$LocalPointsTableReferences),
      LocalPoint,
      PrefetchHooks Function({bool rideId})
    >;
typedef $$CachedRoutesTableCreateCompanionBuilder =
    CachedRoutesCompanion Function({
      required String id,
      required String routeJson,
      Value<String?> geometryJson,
      Value<int> versionNo,
      required DateTime savedAt,
      Value<int> rowid,
    });
typedef $$CachedRoutesTableUpdateCompanionBuilder =
    CachedRoutesCompanion Function({
      Value<String> id,
      Value<String> routeJson,
      Value<String?> geometryJson,
      Value<int> versionNo,
      Value<DateTime> savedAt,
      Value<int> rowid,
    });

class $$CachedRoutesTableFilterComposer
    extends Composer<_$RideDatabase, $CachedRoutesTable> {
  $$CachedRoutesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get routeJson => $composableBuilder(
    column: $table.routeJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get geometryJson => $composableBuilder(
    column: $table.geometryJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get versionNo => $composableBuilder(
    column: $table.versionNo,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get savedAt => $composableBuilder(
    column: $table.savedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$CachedRoutesTableOrderingComposer
    extends Composer<_$RideDatabase, $CachedRoutesTable> {
  $$CachedRoutesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get routeJson => $composableBuilder(
    column: $table.routeJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get geometryJson => $composableBuilder(
    column: $table.geometryJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get versionNo => $composableBuilder(
    column: $table.versionNo,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get savedAt => $composableBuilder(
    column: $table.savedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$CachedRoutesTableAnnotationComposer
    extends Composer<_$RideDatabase, $CachedRoutesTable> {
  $$CachedRoutesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get routeJson =>
      $composableBuilder(column: $table.routeJson, builder: (column) => column);

  GeneratedColumn<String> get geometryJson => $composableBuilder(
    column: $table.geometryJson,
    builder: (column) => column,
  );

  GeneratedColumn<int> get versionNo =>
      $composableBuilder(column: $table.versionNo, builder: (column) => column);

  GeneratedColumn<DateTime> get savedAt =>
      $composableBuilder(column: $table.savedAt, builder: (column) => column);
}

class $$CachedRoutesTableTableManager
    extends
        RootTableManager<
          _$RideDatabase,
          $CachedRoutesTable,
          CachedRoute,
          $$CachedRoutesTableFilterComposer,
          $$CachedRoutesTableOrderingComposer,
          $$CachedRoutesTableAnnotationComposer,
          $$CachedRoutesTableCreateCompanionBuilder,
          $$CachedRoutesTableUpdateCompanionBuilder,
          (
            CachedRoute,
            BaseReferences<_$RideDatabase, $CachedRoutesTable, CachedRoute>,
          ),
          CachedRoute,
          PrefetchHooks Function()
        > {
  $$CachedRoutesTableTableManager(_$RideDatabase db, $CachedRoutesTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$CachedRoutesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$CachedRoutesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$CachedRoutesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> routeJson = const Value.absent(),
                Value<String?> geometryJson = const Value.absent(),
                Value<int> versionNo = const Value.absent(),
                Value<DateTime> savedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => CachedRoutesCompanion(
                id: id,
                routeJson: routeJson,
                geometryJson: geometryJson,
                versionNo: versionNo,
                savedAt: savedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String routeJson,
                Value<String?> geometryJson = const Value.absent(),
                Value<int> versionNo = const Value.absent(),
                required DateTime savedAt,
                Value<int> rowid = const Value.absent(),
              }) => CachedRoutesCompanion.insert(
                id: id,
                routeJson: routeJson,
                geometryJson: geometryJson,
                versionNo: versionNo,
                savedAt: savedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<$CachedRoutesTable, CachedRoute>(table),
                  BaseReferences<
                    _$RideDatabase,
                    $CachedRoutesTable,
                    CachedRoute
                  >(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$CachedRoutesTableProcessedTableManager =
    ProcessedTableManager<
      _$RideDatabase,
      $CachedRoutesTable,
      CachedRoute,
      $$CachedRoutesTableFilterComposer,
      $$CachedRoutesTableOrderingComposer,
      $$CachedRoutesTableAnnotationComposer,
      $$CachedRoutesTableCreateCompanionBuilder,
      $$CachedRoutesTableUpdateCompanionBuilder,
      (
        CachedRoute,
        BaseReferences<_$RideDatabase, $CachedRoutesTable, CachedRoute>,
      ),
      CachedRoute,
      PrefetchHooks Function()
    >;

class $RideDatabaseManager {
  final _$RideDatabase _db;
  $RideDatabaseManager(this._db);
  $$LocalRidesTableTableManager get localRides =>
      $$LocalRidesTableTableManager(_db, _db.localRides);
  $$LocalPointsTableTableManager get localPoints =>
      $$LocalPointsTableTableManager(_db, _db.localPoints);
  $$CachedRoutesTableTableManager get cachedRoutes =>
      $$CachedRoutesTableTableManager(_db, _db.cachedRoutes);
}
