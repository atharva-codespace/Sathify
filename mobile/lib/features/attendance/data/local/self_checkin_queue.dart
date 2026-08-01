import 'dart:convert';

import 'package:sqflite/sqflite.dart';

import '../models/self_checkin_models.dart';

/// Module 13.1 — the worker's local queue.
///
/// -----------------------------------------------------------------------
/// WHY THE WORKER NEEDS ONE TOO
/// -----------------------------------------------------------------------
/// 13.1 specifies local storage on "the guard and worker apps". The guard's
/// queue exists because gate connectivity cannot be assumed; the worker's
/// exists for the same reason from the other side of the same gate. A worker
/// standing in a stairwell with no signal taps "I have arrived", and that tap
/// has to survive — Module 8 bills from the record it produces, so losing it
/// costs them money.
///
/// -----------------------------------------------------------------------
/// A SEPARATE DATABASE FROM THE GUARD'S, ON PURPOSE
/// -----------------------------------------------------------------------
/// The two never run on the same account, and the guard's queue carries a
/// roster cache with every expected visit in the society — data a worker has no
/// business holding. Sharing a file would put it one query away.
///
/// The design is otherwise identical, because the property that makes it safe
/// is the same: every row is keyed on a UUID this device generated, so a
/// replayed push is idempotent and re-enqueuing the same tap cannot produce two
/// rows locally either.
class SelfCheckInQueue {
  SelfCheckInQueue({Database? database}) : _injected = database;

  final Database? _injected;
  Database? _database;

  static const _databaseName = 'sathify_worker.db';
  static const _version = 1;
  static const _table = 'pending_checkins';

  Future<Database> get _db async {
    if (_injected != null) return _injected;
    return _database ??= await openDatabase(
      _databaseName,
      version: _version,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE $_table (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
          )
        ''');
      },
    );
  }

  /// Stores a check-in before any network attempt.
  ///
  /// `ConflictAlgorithm.replace` on a UUID primary key: re-enqueuing the same
  /// tap updates the row rather than adding a second one.
  Future<void> enqueue(SelfCheckInDraft draft) async {
    final db = await _db;
    await db.insert(
      _table,
      {
        'id': draft.id,
        'payload': jsonEncode(draft.toJson()),
        'queued_at': DateTime.now().toIso8601String(),
        'attempts': 0,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Oldest first, so a queue that built up over a shift replays in the order
  /// the visits actually happened.
  Future<List<SelfCheckInDraft>> pending({int limit = 100}) async {
    final db = await _db;
    final rows = await db.query(_table, orderBy: 'queued_at ASC', limit: limit);

    return rows
        .map(
          (row) => SelfCheckInDraft.fromJson(
            jsonDecode(row['payload'] as String) as Map<String, dynamic>,
          ),
        )
        .toList();
  }

  Future<int> pendingCount() async {
    final db = await _db;
    final rows = await db.rawQuery('SELECT COUNT(*) AS n FROM $_table');
    return (rows.first['n'] as int?) ?? 0;
  }

  /// Clears rows the server has acknowledged.
  ///
  /// Only ever called with ids the server named. Clearing on "the request
  /// returned" would lose a check-in whenever a response was truncated, and
  /// that is a worker's day.
  Future<void> removeSettled(List<String> ids) async {
    if (ids.isEmpty) return;
    final db = await _db;
    final placeholders = List.filled(ids.length, '?').join(',');
    await db.delete(_table, where: 'id IN ($placeholders)', whereArgs: ids);
  }

  Future<void> recordAttempt(List<String> ids) async {
    if (ids.isEmpty) return;
    final db = await _db;
    final placeholders = List.filled(ids.length, '?').join(',');
    await db.rawUpdate(
      'UPDATE $_table SET attempts = attempts + 1 WHERE id IN ($placeholders)',
      ids,
    );
  }

  /// Rows that have failed repeatedly.
  ///
  /// Surfaced rather than dropped: a check-in that will never sync is something
  /// the worker needs told about, because they are relying on it having landed.
  Future<int> stuckCount({int threshold = 5}) async {
    final db = await _db;
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS n FROM $_table WHERE attempts >= ?',
      [threshold],
    );
    return (rows.first['n'] as int?) ?? 0;
  }

  Future<void> close() async {
    await _database?.close();
    _database = null;
  }
}
