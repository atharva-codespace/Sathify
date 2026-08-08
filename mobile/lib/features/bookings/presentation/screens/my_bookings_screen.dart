import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../payments/presentation/providers/payment_provider.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';
import '../widgets/category_icon.dart';

/// Module 5.2 / 5.4 — the caller's one-day bookings.
///
/// One screen serves both sides: the server returns the bookings the caller is
/// party to, and the role decides which actions are offered. A worker confirms
/// or declines; a resident cancels; either may cancel a confirmed job or mark a
/// finished one complete.
class MyBookingsScreen extends ConsumerWidget {
  const MyBookingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bookings = ref.watch(bookingsProvider);
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: Text(isWorker ? 'My jobs' : 'My bookings'),
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(bookingsProvider),
        child: AppSwitcher(
          child: bookings.when(
            loading: () => const AppSkeletonList(count: 3),
            error: (error, _) => AppErrorState(
              message: error is ApiException
                  ? error.message
                  : 'Could not load bookings.',
              onRetry: () => ref.invalidate(bookingsProvider),
            ),
            data: (items) {
              if (items.isEmpty) {
                return AppEmptyState(
                  icon: Icons.event_busy_outlined,
                  title: 'No bookings yet',
                  message: isWorker
                      ? 'Mark the days you can take one-off work and requests '
                          'will appear here.'
                      : 'Book a one-day service to get started.',
                );
              }

              return ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.gutter,
                  AppSpacing.sm,
                  AppSpacing.gutter,
                  AppSpacing.xxl,
                ),
                itemCount: items.length,
                itemBuilder: (context, index) => AppFadeIn(
                  index: index,
                  child: _BookingCard(
                    booking: items[index],
                    isWorker: isWorker,
                  ),
                ),
              );
            },
          ),
        ),
      ),
    );
  }
}

class _BookingCard extends ConsumerStatefulWidget {
  const _BookingCard({required this.booking, required this.isWorker});

  final Booking booking;
  final bool isWorker;

  @override
  ConsumerState<_BookingCard> createState() => _BookingCardState();
}

class _BookingCardState extends ConsumerState<_BookingCard> {
  bool _isBusy = false;

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
        () => ref
            .read(bookingRepositoryProvider)
            .confirmBooking(widget.booking.id),
        'Booking confirmed.',
      );

  Future<void> _decline() async {
    final note = await showDialog<String>(
      context: context,
      builder: (_) => const _NoteDialog(
        title: 'Decline this booking?',
        hint: 'Optional — e.g. already committed that day',
        confirmLabel: 'Decline',
      ),
    );
    if (note == null) return;

    await _run(
      () => ref
          .read(bookingRepositoryProvider)
          .declineBooking(widget.booking.id, note: note),
      'Booking declined.',
    );
  }

  Future<void> _complete() => _run(
        () => ref
            .read(bookingRepositoryProvider)
            .completeBooking(widget.booking.id),
        'Marked complete.',
      );

  /// Opens (or resumes) the payment for a completed job, then hands off to
  /// the payments screen to actually collect it — that screen already has the
  /// tested Razorpay checkout flow, so this does not duplicate it.
  ///
  /// The server call is idempotent on the booking: tapping this twice, or
  /// retrying after a poor connection, resumes the same payment rather than
  /// opening a second one.
  Future<void> _pay() async {
    final messenger = ScaffoldMessenger.of(context);
    final router = GoRouter.of(context);
    setState(() => _isBusy = true);

    try {
      await ref
          .read(paymentRepositoryProvider)
          .payBooking(bookingId: widget.booking.id);
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

  /// Fetches the fee first and shows it, then cancels with that exact figure.
  ///
  /// The server rejects the cancel if a threshold was crossed while the dialog
  /// was open, so nobody is ever charged more than the number they agreed to.
  Future<void> _cancel() async {
    setState(() => _isBusy = true);

    final CancellationQuote quote;
    try {
      quote = await ref
          .read(bookingRepositoryProvider)
          .fetchCancellationQuote(widget.booking.id);
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
      builder: (_) => _CancelDialog(quote: quote),
    );
    if (reason == null) return;

    await _run(
      () => ref.read(bookingRepositoryProvider).cancelBooking(
            widget.booking.id,
            acknowledgedFee: quote.fee,
            reason: reason,
          ),
      quote.isFree
          ? 'Booking cancelled.'
          : 'Booking cancelled. A fee of ₹${quote.fee} applies.',
    );
  }

  AppTone get _statusTone {
    switch (widget.booking.status) {
      case BookingStatus.confirmed:
        return AppTone.success;
      case BookingStatus.completed:
        return AppTone.info;
      case BookingStatus.pending:
        return AppTone.warning;
      case BookingStatus.declined:
      case BookingStatus.cancelled:
      case BookingStatus.expired:
        return AppTone.danger;
    }
  }

  @override
  Widget build(BuildContext context) {
    final booking = widget.booking;
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
                decoration: const BoxDecoration(
                  color: AppColors.primarySoft,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  iconForCategory(booking.category?.icon ?? ''),
                  size: AppIconSize.md,
                  color: AppColors.primary,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      booking.category?.name ?? 'Service',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleSmall,
                    ),
                    Text(
                      counterparty.isEmpty ? 'Unknown' : counterparty,
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
          _DetailRow(
            icon: Icons.event_outlined,
            text: '${date.day}/${date.month}/${date.year} · '
                '${booking.timeRangeLabel}',
          ),
          _DetailRow(
            icon: Icons.currency_rupee,
            text: '${booking.quotedPrice}',
          ),
          if (widget.isWorker && booking.residentFlat.isNotEmpty)
            _DetailRow(icon: Icons.home_outlined, text: booking.residentFlat),
          if (!widget.isWorker && booking.workerPhone.isNotEmpty)
            _DetailRow(icon: Icons.phone_outlined, text: booking.workerPhone),
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
          if (booking.cancellationFee > 0) ...[
            const SizedBox(height: AppSpacing.xs),
            AppStatusChip(
              label: 'Cancellation fee ₹${booking.cancellationFee}',
              tone: AppTone.danger,
              icon: Icons.info_outline_rounded,
              dense: true,
            ),
          ],
          ..._actions(booking),
        ],
      ),
    );
  }

  /// The in-flight state now lives on the buttons themselves rather than
  /// replacing them with a centred spinner, so the card keeps its height and
  /// the user can still see what they just asked for.
  List<Widget> _actions(Booking booking) {
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
    } else if (booking.canBeCancelled) {
      buttons.add(
        AppButton.secondary(
          label: 'Cancel booking',
          icon: Icons.cancel_outlined,
          isLoading: _isBusy,
          onPressed: _cancel,
        ),
      );
    } else if (booking.isConfirmed) {
      // Confirmed and already under way: the remaining action is to close it
      // out. Module 7 will do this from gate attendance instead.
      buttons.add(
        AppButton.secondary(
          label: 'Mark complete',
          icon: Icons.task_alt_rounded,
          isLoading: _isBusy,
          onPressed: _complete,
        ),
      );
    } else if (!widget.isWorker && booking.needsPayment) {
      // Done and not yet paid. The server refuses payment before this status
      // is reached — see payments.views.CreateBookingPaymentView — so this is
      // the earliest point paying becomes possible, not a courtesy prompt.
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
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.icon, required this.text});

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

/// Shows the fee before anything is cancelled, and returns the reason typed.
class _CancelDialog extends StatefulWidget {
  const _CancelDialog({required this.quote});

  final CancellationQuote quote;

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

  @override
  Widget build(BuildContext context) {
    final quote = widget.quote;

    return AlertDialog(
      title: const Text('Cancel this booking?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(AppSpacing.sm),
            decoration: BoxDecoration(
              color:
                  quote.isFree ? AppColors.successSoft : AppColors.dangerSoft,
              borderRadius: AppRadius.chip,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  quote.isFree ? 'No cancellation fee' : 'Fee: ₹${quote.fee}',
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: quote.isFree ? AppColors.success : AppColors.danger,
                  ),
                ),
                const SizedBox(height: AppSpacing.xxs),
                // The server's own wording, shown verbatim so the app and the
                // server can never disagree about why a fee applies.
                Text(
                  quote.rationale,
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
            decoration: const InputDecoration(
              hintText: 'Reason (optional)',
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Keep booking'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          style: FilledButton.styleFrom(backgroundColor: AppColors.danger),
          child: const Text('Cancel booking'),
        ),
      ],
    );
  }
}

class _NoteDialog extends StatefulWidget {
  const _NoteDialog({
    required this.title,
    required this.hint,
    required this.confirmLabel,
  });

  final String title;
  final String hint;
  final String confirmLabel;

  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
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
