import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// A circular avatar that degrades to initials.
///
/// [UserModel] carries no photo URL — only worker profiles have images, and
/// residents, guards and administrators have none at all. So initials are the
/// primary path here rather than a fallback, the same choice Gmail makes.
///
/// The tint is picked from [seed] (pass the user id) rather than from the name,
/// so one person keeps one colour across every screen and across launches. That
/// stability is what makes a list of avatars scannable at a glance.
class AppAvatar extends StatelessWidget {
  const AppAvatar({
    super.key,
    required this.name,
    this.imageUrl,
    this.seed = 0,
    this.size = 44,
    this.showRing = false,
  });

  final String name;
  final String? imageUrl;
  final int seed;
  final double size;

  /// A subtle brand-coloured ring, used to mark the currently active account
  /// in the switcher.
  final bool showRing;

  @override
  Widget build(BuildContext context) {
    final index = seed.abs() % AppColors.avatarTints.length;
    final tint = AppColors.avatarTints[index];
    final ink = AppColors.avatarInks[index];

    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: tint,
        shape: BoxShape.circle,
        border: showRing
            ? Border.all(color: AppColors.primary, width: 2)
            : Border.all(color: AppColors.border),
      ),
      clipBehavior: Clip.antiAlias,
      alignment: Alignment.center,
      child: imageUrl != null && imageUrl!.isNotEmpty
          ? CachedNetworkImage(
              imageUrl: imageUrl!,
              fit: BoxFit.cover,
              width: size,
              height: size,
              // Never a spinner inside an avatar: at this size it reads as
              // noise. A flat tint is calmer and the image usually wins the
              // race anyway once cached.
              placeholder: (_, __) => ColoredBox(color: tint),
              errorWidget: (_, __, ___) => _Initials(
                name: name,
                ink: ink,
                size: size,
              ),
            )
          : _Initials(name: name, ink: ink, size: size),
    );
  }
}

class _Initials extends StatelessWidget {
  const _Initials({required this.name, required this.ink, required this.size});

  final String name;
  final Color ink;
  final double size;

  /// First letter of the first two words — "Priya Sharma" gives "PS".
  ///
  /// Falls back to a single glyph rather than showing nothing, because
  /// [UserModel.fullName] returns the phone number when no name is set, and a
  /// leading digit is still a usable identifier.
  String get _initials {
    final parts = name.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty);
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts.first[0].toUpperCase();
    return (parts.first[0] + parts.elementAt(1)[0]).toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    return Text(
      _initials,
      style: TextStyle(
        // Tracks the avatar so initials stay optically centred at any size.
        fontSize: size * 0.36,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.2,
        color: ink,
      ),
    );
  }
}
