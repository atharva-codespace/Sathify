import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/notification_models.dart';
import '../providers/notification_provider.dart';

/// Module 10.4 — per-category notification settings.
///
/// -----------------------------------------------------------------------
/// LOCKED, NOT SILENTLY REFUSED
/// -----------------------------------------------------------------------
/// Gate entry, urgent leave and account status cannot be switched off. The
/// server refuses to mute them, and the model refuses underneath the serializer
/// — but a switch that flips back with an error message is a bad way to learn
/// that. The API returns `can_mute` for exactly this reason, so those rows
/// render as a lock with the reason spelt out instead of as a control.
///
/// Muting is also not the same as hiding: a muted category still lands in the
/// notification centre. The user asked for quiet, not for ignorance.
class NotificationPreferencesScreen extends ConsumerStatefulWidget {
  const NotificationPreferencesScreen({super.key});

  @override
  ConsumerState<NotificationPreferencesScreen> createState() =>
      _NotificationPreferencesScreenState();
}

class _NotificationPreferencesScreenState
    extends ConsumerState<NotificationPreferencesScreen> {
  /// Categories with a request in flight, so a row cannot be toggled twice.
  final Set<String> _saving = {};

  Future<void> _setMuted(NotificationPreference preference, bool muted) async {
    setState(() => _saving.add(preference.category));
    final messenger = ScaffoldMessenger.of(context);

    try {
      await ref.read(notificationRepositoryProvider).setPreference(
            category: preference.category,
            muted: muted,
          );
      ref.invalidate(notificationPreferencesProvider);
    } on ApiException catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      if (mounted) setState(() => _saving.remove(preference.category));
    }
  }

  @override
  Widget build(BuildContext context) {
    final preferences = ref.watch(notificationPreferencesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Notification settings')),
      body: preferences.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              error is ApiException
                  ? error.message
                  : 'Could not load your settings.',
              textAlign: TextAlign.center,
            ),
          ),
        ),
        data: (items) => ListView(
          children: [
            const _Explainer(),
            for (final preference in items)
              _PreferenceTile(
                preference: preference,
                isSaving: _saving.contains(preference.category),
                onChanged: (muted) => _setMuted(preference, muted),
              ),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}

class _Explainer extends StatelessWidget {
  const _Explainer();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Text(
        'Switching a category off stops the alerts on your phone. '
        'Those messages still appear in your notification list.',
        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
              color: AppColors.textSecondary,
            ),
      ),
    );
  }
}

class _PreferenceTile extends StatelessWidget {
  const _PreferenceTile({
    required this.preference,
    required this.isSaving,
    required this.onChanged,
  });

  final NotificationPreference preference;
  final bool isSaving;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    if (!preference.canMute) {
      return ListTile(
        title: Text(preference.label),
        subtitle: const Text(
          'Always on — this covers your safety and your account.',
        ),
        trailing:
            const Icon(Icons.lock_outline, color: AppColors.textSecondary),
        // No onTap: there is nothing to change, and a tappable row that does
        // nothing reads as a bug.
      );
    }

    return SwitchListTile(
      title: Text(preference.label),
      subtitle: Text(preference.muted ? 'Alerts off' : 'Alerts on'),
      // The switch reads as "notify me", so it is the inverse of `muted` — the
      // server stores the mute, the user thinks in terms of being told.
      value: !preference.muted,
      onChanged: isSaving ? null : (notify) => onChanged(!notify),
      secondary: isSaving
          ? const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : null,
    );
  }
}
