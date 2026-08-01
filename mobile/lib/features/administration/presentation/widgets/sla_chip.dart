import 'package:flutter/material.dart';

import '../../../../shared/design_system.dart';
import '../../data/models/admin_models.dart';

/// How long is left on a complaint's response deadline.
///
/// -----------------------------------------------------------------------
/// SHOWN TO BOTH SIDES, NOT JUST THE ADMINISTRATOR
/// -----------------------------------------------------------------------
/// The server sends the SLA fields to whoever can see the complaint, and this
/// chip renders them the same way for everybody. A response time only the
/// committee can see is an internal metric; one the person who raised the
/// complaint can read is a commitment.
///
/// The hours are *active* hours — the server's clock stops overnight — so "4 h
/// left" on a complaint raised at 22:00 means four hours of the next working
/// day, not four hours from now. Labelling them plainly avoids implying a
/// wall-clock countdown the app is not running.
class SlaChip extends StatelessWidget {
  const SlaChip({super.key, required this.complaint});

  final Complaint complaint;

  @override
  Widget build(BuildContext context) {
    final (label, colour, icon) = _describe();

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: colour),
          const SizedBox(width: 4),
          Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              color: colour,
            ),
          ),
        ],
      ),
    );
  }

  (String, Color, IconData) _describe() {
    if (!complaint.isOpen) {
      return ('Closed', AppColors.textSecondary, Icons.check);
    }

    final remaining = complaint.hoursRemaining;

    // Keyed off the server's `isOverdue`, not off the sign of `hoursRemaining`.
    // The two disagree overnight and the difference matters: the SLA clock
    // stops at 21:00, so a complaint that breached at 21:35 and is opened at
    // 22:35 has burned *zero active hours* since. Inferring from the sign would
    // render it "0 h left" in amber — an overdue complaint dressed up as one
    // that is just in time. `isOverdue` is wall-clock and always right.
    if (complaint.isOverdue) {
      final over = remaining.abs().round();
      return (
        over == 0 ? 'Overdue' : '$over h over',
        AppColors.danger,
        Icons.priority_high,
      );
    }
    if (remaining < 4) {
      return ('${remaining.round()} h left', AppColors.warning, Icons.schedule);
    }
    return ('${remaining.round()} h left', AppColors.success, Icons.schedule);
  }
}
