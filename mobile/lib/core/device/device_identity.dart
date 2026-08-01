import 'dart:io';

import 'package:device_info_plus/device_info_plus.dart';

/// Who this installation is, as far as the server is concerned.
///
/// -----------------------------------------------------------------------
/// ONE DEVICE ID, USED EVERYWHERE
/// -----------------------------------------------------------------------
/// The backend keys `DeviceSession` on `(user, device_id)`. Sign-in (Module
/// 1.5) opens that row; push registration (Module 10.1) writes the FCM token
/// onto it. If those two sent different ids the same phone would end up with
/// two session rows — and every notification would arrive twice, once per row.
///
/// So this is deliberately the only place the id is derived, and it is cached
/// for the process lifetime: it cannot change while the app is running, and a
/// platform channel round trip on every call would be waste.
class DeviceIdentity {
  const DeviceIdentity({
    required this.deviceId,
    required this.deviceName,
    required this.platform,
  });

  final String deviceId;
  final String deviceName;
  final String platform;

  /// The shape both `/auth/login/` and `/notifications/device/` expect.
  Map<String, String> toJson() => {
        'device_id': deviceId,
        'device_name': deviceName,
        'platform': platform,
      };

  /// The fallback when the platform will not identify itself.
  ///
  /// A constant rather than a random value on purpose: a random id would open a
  /// fresh session row on every launch and leave a trail of dead ones, whereas a
  /// shared constant at worst collides with another unidentifiable device
  /// belonging to *the same user*, which is harmless.
  static const DeviceIdentity unknown = DeviceIdentity(
    deviceId: 'unknown-device',
    deviceName: '',
    platform: '',
  );

  static DeviceIdentity? _cached;

  static Future<DeviceIdentity> current() async {
    final cached = _cached;
    if (cached != null) return cached;

    final resolved = await _resolve();
    _cached = resolved;
    return resolved;
  }

  static Future<DeviceIdentity> _resolve() async {
    final plugin = DeviceInfoPlugin();
    try {
      if (Platform.isAndroid) {
        final info = await plugin.androidInfo;
        return DeviceIdentity(
          deviceId: info.id,
          deviceName: '${info.manufacturer} ${info.model}',
          platform: 'android',
        );
      }
      if (Platform.isIOS) {
        final info = await plugin.iosInfo;
        return DeviceIdentity(
          deviceId: info.identifierForVendor ?? 'unknown-ios',
          deviceName: info.utsname.machine,
          platform: 'ios',
        );
      }
    } on Exception catch (_) {
      // Device info is a nice-to-have; never block sign-in or push on it.
    }
    return unknown;
  }
}
