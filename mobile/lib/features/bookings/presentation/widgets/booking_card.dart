import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../payments/presentation/providers/payment_provider.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';
import 'category_icon.dart';

/// One booking, with whatever the caller may do to it.
///
/// -----------------------------------------------------------------------
/// WHY THIS IS A SHARED WIDGET AND NOT A PRIVATE ONE
/// -----------------------------------------------------------------------
/// It was private to "My bookings". Two things now render bookings — that
/// screen, and the resident's unified "My requests" list, which shows one-day
/// jobs alongside hire requests. A second copy would mean the cancel rules, the
/// cash/app settlement split and the "Mark complete" gating all had to be kept
/// in step by hand across two files, and the last three bugs in this area were
/// all one copy of a rule disagreeing with another.
class BookingCard extends ConsumerStatefulWidget {
  const BookingCard({
    required this.booking,
    required this.isWorker,
    this.dense = false,
    super.key,
  });

  final Booking booking;
  final bool isWorker;

  /// Trims the card for a mixed list, where it sits next to hire requests and
  /// competes with them for the eye.
  final bool dense;

  @override
  ConsumerState<BookingCard> createState() => _BookingCardState();
}

class _BookingCardState extends ConsumerState<BookingCard> {
  bool _isBusy = false;

  Booking get booking => widget.booking;

  Future<void> _run(Future<void> Function() action, String message) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _isBusy = true);
    try {
      await action();
      if (!mounted) return;
      invalidateBookings(ref);
      showAppSnackBarOn(messenger, message, tone: AppTone.success);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  Future<void> _confirm() => _run(
        () => ref.read(bookingRepositoryProvider).confirmBooking(booking.id),
        'Booking confirmed.',
      );

  Future<void> _decline() async {
    final note = await showDialog<String>(
      context: context,
      builder: (_) => const BookingNoteDialog(
        title: 'Decline this booking?',
        hint: 'Optional — e.g. already committed that day',
        confirmLabel: 'Decline',
      ),
    );
    if (note == null) return;

    await _run(
      () => ref
          .read(bookingRepositoryProvider)
          .declineBooking(booking.id, note: note),
      'Booking declined.',
    );
  }

  Future<void> _complete() => _run(
        () => ref.read(bookingRepositoryProvider).completeBooking(booking.id),
        // Says what happens next rather than only what just happened: the
        // household is asked for the fee the moment this lands, so the worker
        // knows the money is in motion rather than wondering.
        'Marked complete. ₹${booking.quotedPrice} has been requested.',
      );

  /// Opens (or resumes) the payment for a completed job, then hands off to the
  /// payments screen to collect it — that screen owns the tested checkout flow.
  ///
  /// Idempotent on the server: tapping twice, or retrying after a poor
  /// connection, resumes the same payment rather than opening a second one.
  Future<void> _pay() async {
    final messenger = ScaffoldMessenger.of(context);
    final router = GoRouter.of(context);
    setState(() => _isBusy = true);

    try {
      await ref
          .read(paymentRepositoryProvider)
          .payBooking(bookingId: booking.id);
      if (!mounted) return;
      invalidateBookings(ref);
      invalidatePayments(ref);
      setState(() => _isBusy = false);
      unawaited(router.push(Routes.payments));
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  /// Cancels, having first shown exactly what it costs.
  ///
  /// -----------------------------------------------------------------------
  /// AN EMERGENCY HAS THREE DIFFERENT CANCELLATIONS, NOT ONE
  /// -----------------------------------------------------------------------
  /// They used to share a button and a dialog, which meant a resident
  /// abandoning a request they had not paid for saw the same warning as one
  /// pulling a worker off a job she had already accepted. The three are:
  ///
  /// * **Before payment** — nothing has been collected and nobody has been
  ///   told. Walking away costs nothing and needs no ceremony.
  /// * **Paid, still looking** — the fee has been taken but no worker has
  ///   accepted, so it comes back in full (see [_CancelDialog] for why).
  /// * **Accepted, or an ordinary booking** — somebody rearranged their day,
  ///   and the Module 5.4 fee ladder applies.
  ///
  /// The *fee* is always the server's figure, fetched first and echoed back on
  /// the cancel, so nobody is ever charged more than the number they agreed to.
  /// Only the wording is decided here.
  Future<void> _cancel() async {
    setState(() => _isBusy = true);

    final CancellationQuote quote;
    try {
      quote = await ref
          .read(bookingRepositoryProvider)
          .fetchCancellationQuote(booking.id);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(
        ScaffoldMessenger.of(context),
        error.message,
        tone: AppTone.danger,
      );
      return;
    }

    if (!mounted) return;
    setState(() => _isBusy = false);

    final reason = await showDialog<String>(
      context: context,
      builder: (_) => _CancelDialog(quote: quote, booking: booking),
    );
    if (reason == null) return;

    await _run(
      () => ref.read(bookingRepositoryProvider).cancelBooking(
            booking.id,
            acknowledgedFee: quote.fee,
            reason: reason,
          ),
      _cancelledMessage(quote),
    );
  }

  String _cancelledMessage(CancellationQuote quote) {
    if (booking.status == BookingStatus.paymentPending) {
      return 'Request cancelled. You were not charged.';
    }
    if (booking.status == BookingStatus.broadcast) {
      return 'Request cancelled. Your emergency fee has been refunded.';
    }
    return quote.isFree
        ? 'Booking cancelled.'
        : 'Booking cancelled. A fee of ₹${quote.fee} applies.';
  }

  AppTone get _statusTone {
    switch (booking.status) {
      case BookingStatus.confirmed:
        return AppTone.success;
      case BookingStatus.completed:
        return AppTone.info;
      case BookingStatus.pending:
      case BookingStatus.paymentPending:
      case BookingStatus.broadcast:
        return AppTone.warning;
      case BookingStatus.declined:
      case BookingStatus.cancelled:
      case BookingStatus.expired:
      case BookingStatus.unfulfilled:
        return AppTone.danger;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final counterparty =
        widget.isWorker ? booking.residentName : booking.workerName;
    final date = booking.scheduledDate;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: booking.isEmergency
                      ? AppColors.dangerSoft
                      : AppColors.primarySoft,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  iconForCategory(booking.category?.icon ?? ''),
                  size: AppIconSize.md,
                  color: booking.isEmergency
                      ? AppColors.danger
                      : AppColors.primary,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            booking.category?.name ?? 'Service',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.titleSmall,
                          ),
                        ),
                        if (booking.isEmergency) ...[
                          const SizedBox(width: AppSpacing.xs),
                          const AppStatusChip(
                            label: 'Emergency',
                            tone: AppTone.danger,
                            icon: Icons.bolt_rounded,
                            dense: true,
                          ),
                        ],
                      ],
                    ),
                    Text(
                      counterparty.isEmpty
                          // A broadcast has no worker yet, and saying "Unknown"
                          // reads as an error rather than as a stage of the
                          // process the household is watching.
                          ? (booking.isSeekingWorker ? 'Finding someone' : 'Unknown')
                          : counterparty,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              AppStatusChip(label: booking.status.label, tone: _statusTone),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          const Divider(height: 1),
          const SizedBox(height: AppSpacing.xs),
          BookingDetailRow(
            icon: Icons.event_outlined,
            text: '${date.day}/${date.month}/${date.year} · '
                '${booking.timeRangeLabel}',
          ),
          BookingDetailRow(
            icon: Icons.currency_rupee,
            text: '${booking.quotedPrice}',
          ),
          if (booking.isEmergency && booking.emergencySurchargePaise > 0)
            BookingDetailRow(
              icon: Icons.bolt_rounded,
              text: 'Emergency fee ₹${booking.emergencySurchargePaise ~/ 100} '
                  '(Sathify)',
            ),
          if (!widget.dense) ...[
            if (widget.isWorker && booking.residentFlat.isNotEmpty)
              BookingDetailRow(
                icon: Icons.home_outlined,
                text: booking.residentFlat,
              ),
            if (!widget.isWorker && booking.workerPhone.isNotEmpty)
              BookingDetailRow(
                icon: Icons.phone_outlined,
                text: booking.workerPhone,
              ),
            if (booking.notes.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.xs),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.sm),
                decoration: const BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: AppRadius.chip,
                ),
                child: Text(
                  '“${booking.notes}”',
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ],
            if (booking.responseNote.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                'Note: ${booking.responseNote}',
                style: theme.textTheme.bodySmall,
              ),
            ],
          ],
          if (booking.cancellationFee > 0) ...[
            const SizedBox(height: AppSpacing.xs),
            AppStatusChip(
              label: 'Cancellation fee ₹${booking.cancellationFee}',
              tone: AppTone.danger,
              icon: Icons.info_outline_rounded,
              dense: true,
            ),
          ],
          ..._actions(),
        ],
      ),
    );
  }

  /// The in-flight state lives on the buttons rather than replacing them with a
  /// centred spinner, so the card keeps its height and the user can still see
  /// what they just asked for.
  List<Widget> _actions() {
    final buttons = <Widget>[];

    if (widget.isWorker && booking.isActionable) {
      buttons.add(
        Row(
          children: [
            Expanded(
              child: AppButton.secondary(
                label: 'Decline',
                icon: Icons.close_rounded,
                onPressed: _isBusy ? null : _decline,
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: AppButton(
                label: 'Accept',
                icon: Icons.check_rounded,
                isLoading: _isBusy,
                onPressed: _confirm,
              ),
            ),
          ],
        ),
      );
    } else if (booking.canMarkDone) {
      // Above cancelling, and keyed on the server's own answer. This branch
      // used to sit last, behind an `else if (canBeCancelled)` that matched
      // every confirmed booking whose start time had not passed — so a job that
      // could legitimately be closed out showed "Cancel booking" and no way to
      // finish it at all.
      buttons.add(
        AppButton.secondary(
          label:
              'Mark complete',
          icon: Icons.task_alt_rounded,
          isLoading: _isBusy,
          onPressed: _complete,
        ),
      );
    } else if (!widget.isWorker && booking.canBeCancelled) {
      buttons.add(
        AppButton.secondary(
          label: _cancelLabel,
          icon: Icons.close_rounded,
          isLoading: _isBusy,
          onPressed: _cancel,
        ),
      );
    } else if (!widget.isWorker && booking.needsPayment) {
      // Done and not yet paid. The server refuses payment before this status is
      // reached, so this is the earliest paying becomes possible rather than a
      // courtesy prompt. Never shown on a cash job — `needsPayment` is false
      // there, because there is no in-app charge to settle.
      buttons.add(
        AppButton(
          label: 'Pay ₹${booking.quotedPrice}',
          icon: Icons.payments_outlined,
          isLoading: _isBusy,
          onPressed: _pay,
        ),
      );
    }

    if (buttons.isEmpty) return const [];
    return [const SizedBox(height: AppSpacing.sm), ...buttons];
  }

  /// Says what cancelling *this* booking does, at a glance.
  ///
  /// A refund named on the button is the difference between a resident who
  /// abandons a stuck request and one who sits on it because they assume
  /// cancelling forfeits what they paid.
  String get _cancelLabel {
    switch (booking.status) {
      case BookingStatus.paymentPending:
        return 'Cancel request';
      case BookingStatus.broadcast:
        return 'Cancel — refund ₹${booking.emergencySurchargePaise ~/ 100}';
      default:
        return 'Cancel booking';
    }
  }
}

/// One labelled line of booking detail.
class BookingDetailRow extends StatelessWidget {
  const BookingDetailRow({required this.icon, required this.text, super.key});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: AppIconSize.sm, color: AppColors.textTertiary),
          const SizedBox(width: AppSpacing.xs + 2),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Shows what cancelling costs before anything is cancelled, and returns the
/// reason typed.
class _CancelDialog extends StatefulWidget {
  const _CancelDialog({required this.quote, required this.booking});

  final CancellationQuote quote;
  final Booking booking;

  @override
  State<_CancelDialog> createState() => _CancelDialogState();
}

class _CancelDialogState extends State<_CancelDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// The headline figure, and the sentence explaining it.
  ///
  /// The refund wording is not decoration. A household that has paid an
  /// emergency fee and is watching a request nobody has taken needs to know,
  /// *before* they tap, that backing out returns the money — otherwise the
  /// rational move is to leave a dead request open, which helps nobody.
  (String, String, bool) get _headline {
    switch (widget.booking.status) {
      case BookingStatus.paymentPending:
        return (
          'Nothing has been charged',
          'You have not paid the emergency fee yet, and no worker has been '
              'contacted. Cancelling costs nothing.',
          true,
        );
      case BookingStatus.broadcast:
        final fee = widget.booking.emergencySurchargePaise ~/ 100;
        return (
          'Full refund of ₹$fee',
          'No worker has accepted yet, so the emergency fee comes back to you '
              'in full.',
          true,
        );
      default:
        return (
          widget.quote.isFree ? 'No cancellation fee' : 'Fee: ₹${widget.quote.fee}',
          // The server's own wording, verbatim, so the app and the server can
          // never disagree about why a fee applies.
          widget.quote.rationale,
          widget.quote.isFree,
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final (title, rationale, isFree) = _headline;

    return AlertDialog(
      title: Text(
        widget.booking.isSeekingWorker
            ? 'Cancel this request?'
            : 'Cancel this booking?',
      ),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(AppSpacing.sm),
            decoration: BoxDecoration(
              color: isFree ? AppColors.successSoft : AppColors.dangerSoft,
              borderRadius: AppRadius.chip,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: isFree ? AppColors.success : AppColors.danger,
                  ),
                ),
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  rationale,
                  style: const TextStyle(
                    fontSize: 13,
                    height: 1.4,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          TextField(
            controller: _controller,
            maxLines: 2,
            decoration: const InputDecoration(hintText: 'Reason (optional)'),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(
            widget.booking.isSeekingWorker ? 'Keep looking' : 'Keep booking',
          ),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          style: FilledButton.styleFrom(backgroundColor: AppColors.danger),
          child: const Text('Cancel it'),
        ),
      ],
    );
  }
}

/// Collects an optional note alongside a confirmation.
class BookingNoteDialog extends StatefulWidget {
  const BookingNoteDialog({
    required this.title,
    required this.hint,
    required this.confirmLabel,
    super.key,
  });

  final String title;
  final String hint;
  final String confirmLabel;

  @override
  State<BookingNoteDialog> createState() => _BookingNoteDialogState();
}

class _BookingNoteDialogState extends State<BookingNoteDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: TextField(
        controller: _controller,
        autofocus: true,
        maxLines: 3,
        decoration: InputDecoration(hintText: widget.hint),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: Text(widget.confirmLabel),
        ),
      ],
    );
  }
}
