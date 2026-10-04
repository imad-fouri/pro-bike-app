import 'package:drift/drift.dart';

/// Local-first ride tables (ADR-07). Backend remains system of record
/// after finalize; these rows are the source of truth while recording.
class LocalRides extends Table {
  TextColumn get id => text()(); // client_ride_uuid
  TextColumn get serverId => text().nullable()();
  TextColumn get bikeId => text()();
  // Optional planned route the ride follows (nullable: rides may have none).
  TextColumn get routeId => text().nullable()();
  TextColumn get status => text().withDefault(const Constant('recording'))();
  DateTimeColumn get startedAt => dateTime()();
  DateTimeColumn get endedAt => dateTime().nullable()();
  IntColumn get uploadedSeq => integer().withDefault(const Constant(-1))();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

class LocalPoints extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get rideId => text().references(LocalRides, #id)();
  TextColumn get pointUuid => text().unique()();
  IntColumn get seq => integer()();
  RealColumn get lat => real()();
  RealColumn get lon => real()();
  RealColumn get alt => real().nullable()();
  RealColumn get accuracy => real().nullable()();
  RealColumn get speed => real().nullable()();
  RealColumn get heading => real().nullable()();
  DateTimeColumn get recordedAt => dateTime()();
  BoolColumn get accepted => boolean().withDefault(const Constant(true))();
  TextColumn get reason => text().nullable()();
  BoolColumn get uploaded => boolean().withDefault(const Constant(false))();
}

/// Explicit offline route cache (Phase 5, §39): a route is stored only after
/// the user downloads it, keyed by the version it was downloaded at.
class CachedRoutes extends Table {
  TextColumn get id => text()(); // route id
  TextColumn get routeJson => text()(); // server payload (RouteOut)
  TextColumn get geometryJson =>
      text().nullable()(); // RouteGeometry, if fetched
  IntColumn get versionNo => integer().withDefault(const Constant(0))();
  DateTimeColumn get savedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}
