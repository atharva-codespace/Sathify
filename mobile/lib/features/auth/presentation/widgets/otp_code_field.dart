import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// The 6-digit code input, shared by phone verification and password reset.
///
/// Both screens need identical behaviour — digits only, auto-submit on the
/// sixth, SMS autofill — and the two drifted apart the moment they were written
/// twice.
class OtpCodeField extends StatelessWidget {
  const OtpCodeField({
    required this.controller,
    this.errorText,
    this.onCompleted,
    this.autofocus = true,
    super.key,
  });

  final TextEditingController controller;
  final String? errorText;

  /// Called once six digits have been entered. Null disables auto-submit, which
  /// is what stops a second request firing while one is already in flight.
  final VoidCallback? onCompleted;

  final bool autofocus;

  static const length = 6;

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: controller,
      autofocus: autofocus,
      keyboardType: TextInputType.number,
      textInputAction: TextInputAction.done,
      textAlign: TextAlign.center,
      // Lets Android fill the code straight from the SMS.
      autofillHints: const [AutofillHints.oneTimeCode],
      maxLength: length,
      style: const TextStyle(
        fontSize: 28,
        letterSpacing: 12,
        fontWeight: FontWeight.w600,
      ),
      inputFormatters: [
        FilteringTextInputFormatter.digitsOnly,
        LengthLimitingTextInputFormatter(length),
      ],
      decoration: InputDecoration(
        labelText: '$length-digit code',
        counterText: '',
        errorText: errorText,
      ),
      onChanged: (value) {
        if (value.trim().length == length) onCompleted?.call();
      },
      validator: (value) =>
          (value ?? '').trim().length != length ? 'Enter the $length-digit code' : null,
    );
  }
}

/// Counts down the resend cooldown, mirroring the server's 60 seconds.
///
/// The server stays the authority and will still refuse an early request; this
/// only keeps the user from tapping into a 429 that reads as a broken button.
class OtpResendTimer {
  OtpResendTimer({required this.onTick});

  /// Called on each tick so the owning widget can rebuild its label.
  ///
  /// Never fired synchronously from [start] — callers run that from
  /// `initState`, where a `setState` would be too early to be legal.
  final VoidCallback onTick;

  static const cooldownSeconds = 60;

  int _remaining = 0;
  Timer? _ticker;

  bool get isWaiting => _remaining > 0;

  String get label => isWaiting ? 'Resend code in ${_remaining}s' : 'Resend code';

  void start() {
    _ticker?.cancel();
    _remaining = cooldownSeconds;
    _ticker = Timer.periodic(const Duration(seconds: 1), (timer) {
      _remaining -= 1;
      if (_remaining <= 0) timer.cancel();
      onTick();
    });
  }

  void dispose() => _ticker?.cancel();
}
