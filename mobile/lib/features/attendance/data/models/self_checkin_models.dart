/// Module 13.3 tier 2 — the worker's own arrival record.
///
/// -----------------------------------------------------------------------
/// THIS TIER CANNOT REFUSE ANYBODY
/// -----------------------------------------------------------------------
/// [SelfCheckInResult.decision] is `allowed` or `pending_review`, never
/// `denied`. That is a property of the server, and this model has no state for
/// a refusal because there is no refusal to represent.
///
/// The reason matters: a GPS fix in a courtyard between two towers is routinely
/// 150 m out. A worker turned away by that loses a day's wages for a
/// measurement error, and the measurement is nowhere near good enough to
/// justify it. So a position that does not check out sends the record to an
/// administrator instead — which is a delay, not a denial.
library;

/// A device's reading of where it is, if it has one.
///
/// All three fields are nullable together. A phone with location switched off
/// still produces a usable check-in: it goes to review rather than standing on
/// its own, because refusing it would leave a worker who did the job with no
/// evidence of having done it.
class DevicePosition {
  const DevicePosition({
    required this.latitude,
    required this.longitude,
    this.accuracyMetres,
  });

  final double latitude;
  final double longitude;

  /// What the device thinks its own fix is worth, in metres. Sent rather than
  /// hidden — the server widens its allowance to match, so an honest phone
  /// reporting a poor fix helps the worker rather than hurting them.
  final double? accuracyMetres;

  Map<String, dynamic> toJson() => {
        'latitude': latitude,
        'longitude': longitude,
        if (accuracyMetres != null) 'accuracy_metres': accuracyMetres,
      };
}

/// A queued self check-in, addressed by an id this device minted (13.1).
class SelfCheckInDraft {
  const SelfCheckInDraft({
    required this.id,
    required this.occurredAt,
    this.direction = 'entry',
    this.position,
    this.deviceId = '',
    this.wasOffline = false,
  });

  /// Generated before the server has seen it, so a retry after a lost
  /// connection produces one record rather than two.
  final String id;

  final DateTime occurredAt;
  final String direction;
  final DevicePosition? position;
  final String deviceId;
  final bool wasOffline;

  SelfCheckInDraft copyWith({bool? wasOffline}) => SelfCheckInDraft(
        id: id,
        occurredAt: occurredAt,
        direction: direction,
        position: position,
        deviceId: deviceId,
        wasOffline: wasOffline ?? this.wasOffline,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'occurred_at': occurredAt.toUtc().toIso8601String(),
        'direction': direction,
        if (position != null) ...position!.toJson(),
        if (deviceId.isNotEmpty) 'device_id': deviceId,
        'was_offline': wasOffline,
      };

  factory SelfCheckInDraft.fromJson(Map<String, dynamic> json) {
    final latitude = json['latitude'];
    final longitude = json['longitude'];

    return SelfCheckInDraft(
      id: json['id'] as String,
      occurredAt: DateTime.tryParse(json['occurred_at'] as String? ?? '') ??
          DateTime.now(),
      direction: json['direction'] as String? ?? 'entry',
      position: latitude is num && longitude is num
          ? DevicePosition(
              latitude: latitude.toDouble(),
              longitude: longitude.toDouble(),
              accuracyMetres: (json['accuracy_metres'] as num?)?.toDouble(),
            )
          : null,
      deviceId: json['device_id'] as String? ?? '',
      wasOffline: json['was_offline'] as bool? ?? false,
    );
  }
}

/// What the server made of a check-in.
class SelfCheckInResult {
  const SelfCheckInResult({
    required this.id,
    required this.decision,
    this.created = false,
    this.decisionReason = '',
    this.wasExpected = false,
    this.needsReview = false,
    this.distanceMetres,
    this.locationChecked = false,
  });

  final String id;

  /// `allowed` or `pending_review`. Never `denied` — see the file docstring.
  final String decision;

  /// False when this id had already been recorded. Not an error: it is the
  /// expected outcome of a device that synced and lost the response.
  final bool created;

  final String decisionReason;
  final bool wasExpected;
  final bool needsReview;

  /// How far the device was from the society, when that could be measured.
  final double? distanceMetres;

  /// False when no position was sent, or the society has no coordinates. A
  /// third state: unmeasured is not the same as outside.
  final bool locationChecked;

  bool get isAllowed => decision == 'allowed';

  factory SelfCheckInResult.fromJson(Map<String, dynamic> json) =>
      SelfCheckInResult(
        id: json['id'] as String? ?? '',
        decision: json['decision'] as String? ?? 'pending_review',
        created: json['created'] as bool? ?? false,
        decisionReason: json['decision_reason'] as String? ?? '',
        wasExpected: json['was_expected'] as bool? ?? false,
        needsReview: json['needs_review'] as bool? ?? false,
        distanceMetres: (json['distance_metres'] as num?)?.toDouble(),
        locationChecked: json['location_checked'] as bool? ?? false,
      );
}
