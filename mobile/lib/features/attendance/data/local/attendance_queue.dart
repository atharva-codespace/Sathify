import 'dart:convert';

import 'package:sqflite/sqflite.dart';

import '../models/attendance_models.dart';

/// Module 7.4 — the guard's local queue and roster cache.
///
/// -----------------------------------------------------------------------
/// WHY THIS EXISTS AT ALL
/// -----------------------------------------------------------------------
/// Gate connectivity cannot be assumed, and a worker cannot be left standing at
/// the gate while a request times out. So every decision is written here first
/// and pushed later. The device is the source of truth until the server
/// acknowledges an event — which is safe only because each event carries a UUID
/// this device generated, making a replayed push idempotent.
///
/// -----------------------------------------------------------------------
/// NOTHING IS DELETED UNTIL THE SERVER HAS ACKNOWLEDGED IT
/// -----------------------------------------------------------------------
/// [removeSettled] is called with the ids the server actually reported back —
/// created, duplicate, *and* rejected. Clearing on "the request returned" would
/// lose events whenever a response was truncated; keeping rejected ones forever
/// would mean a single malformed row blocks the queue for good. Both failure
/// modes cost someone a day's attendance, so settlement is explicit.
class AttendanceQueue {
  AttendanceQueue({Database? database}) : _injected = database;

  final Database? _injected;
  Database? _database;

  static const _databaseName = 'sathify_gate.db';
  static const _version = 1;

  static const _eventsTable = 'pending_events';
  static const _rosterTable = 'roster_cache';

  Future<Database> get _db async {
    if (_injected != null) return _injected;
    return _database ??= await openDatabase(
      _databaseName,
      version: _version,
      onCreate: (db, version) async {
        // The event's own UUID is the primary key, so re-enqueuing the same
        // decision cannot produce two rows locally either.
        await db.execute('''
          CREATE TABLE $_eventsTable (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
          )
        ''');
        await db.execute('''
          CREATE TABLE $_rosterTable (
            day TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            cached_at TEXT NOT NULL
          )
        ''');
      },
    );
  }

  // --- Pending events --------------------------------------------------------

  /// Writes a decision to the queue. Safe to call twice with the same event.
  Future<void> enqueue(AttendanceEventDraft event) async {
    final db = await _db;
    await db.insert(
      _eventsTable,
      {
        'id': event.id,
        'payload': event.copyWith(wasOffline: true).encode(),
        'queued_at': DateTime.now().toIso8601String(),
        'attempts': 0,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Everything waiting to be pushed, oldest first.
  ///
  /// Oldest first matters: a queue that drains newest-first leaves the morning's
  /// entries behind whenever the connection is briefly available, and those are
  /// the ones payroll needs.
  Future<List<AttendanceEventDraft>> pending({int limit = 500}) async {
    final db = await _db;
    final rows = await db.query(
      _eventsTable,
      orderBy: 'queued_at ASC',
      limit: limit,
    );
    return rows
        .map((row) => AttendanceEventDraft.decode(row['payload'] as String))
        .toList();
  }

  Future<int> pendingCount() async {
    final db = await _db;
    final result = await db.rawQuery('SELECT COUNT(*) AS n FROM $_eventsTable');
    return (result.first['n'] as int?) ?? 0;
  }

  /// Removes only what the server confirmed it has settled.
  Future<void> removeSettled(List<String> ids) async {
    if (ids.isEmpty) return;
    final db = await _db;
    final placeholders = List.filled(ids.length, '?').join(',');
    await db.delete(
      _eventsTable,
      where: 'id IN ($placeholders)',
      whereArgs: ids,
    );
  }

  /// Counts a failed push so a permanently stuck event can be surfaced rather
  /// than retried in silence forever.
  Future<void> recordAttempt(List<String> ids) async {
    if (ids.isEmpty) return;
    final db = await _db;
    final placeholders = List.filled(ids.length, '?').join(',');
    await db.rawUpdate(
      'UPDATE $_eventsTable SET attempts = attempts + 1 WHERE id IN ($placeholders)',
      ids,
    );
  }

  /// Events that have failed to push repeatedly — worth telling the guard about.
  Future<int> stuckCount({int threshold = 5}) async {
    final db = await _db;
    final result = await db.rawQuery(
      'SELECT COUNT(*) AS n FROM $_eventsTable WHERE attempts >= ?',
      [threshold],
    );
    return (result.first['n'] as int?) ?? 0;
  }

  // --- Roster cache ----------------------------------------------------------

  /// Stores the day's roster so scanning works with no connectivity (7.2).
  Future<void> cacheRoster(DateTime day, List<RosterEntry> roster) async {
    final db = await _db;
    await db.insert(
      _rosterTable,
      {
        'day': _dayKey(day),
        'payload': jsonEncode(roster.map((entry) => entry.toJson()).toList()),
        'cached_at': DateTime.now().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<List<RosterEntry>> cachedRoster(DateTime day) async {
    final db = await _db;
    final rows = await db.query(
      _rosterTable,
      where: 'day = ?',
      whereArgs: [_dayKey(day)],
      limit: 1,
    );
    if (rows.isEmpty) return const [];

    final decoded = jsonDecode(rows.first['payload'] as String) as List;
    return decoded
        .map((row) => RosterEntry.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<DateTime?> rosterCachedAt(DateTime day) async {
    final db = await _db;
    final rows = await db.query(
      _rosterTable,
      columns: ['cached_at'],
      where: 'day = ?',
      whereArgs: [_dayKey(day)],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return DateTime.tryParse(rows.first['cached_at'] as String);
  }

  /// Finds a scanned code in the cached roster (Module 7.2, offline path).
  Future<RosterEntry?> findByCode(DateTime day, String code) async {
    for (final entry in await cachedRoster(day)) {
      if (entry.passCode == code) return entry;
    }
    return null;
  }

  /// Drops rosters for days gone by. The queue is never pruned this way —
  /// an unsynced event from last week is still someone's pay.
  Future<void> pruneRostersBefore(DateTime day) async {
    final db = await _db;
    await db.delete(_rosterTable, where: 'day < ?', whereArgs: [_dayKey(day)]);
  }

  String _dayKey(DateTime day) => '${day.year.toString().padLeft(4, '0')}-'
      '${day.month.toString().padLeft(2, '0')}-'
      '${day.day.toString().padLeft(2, '0')}';

  Future<void> close() async {
    await _database?.close();
    _database = null;
  }
}
