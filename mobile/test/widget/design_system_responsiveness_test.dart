import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/routing/nav_destinations.dart';
import 'package:sathify/features/auth/data/models/user_model.dart';
import 'package:sathify/shared/design_system.dart';

/// Responsiveness guard for the design system.
///
/// The brief asks for "full responsiveness across phone sizes", and the class
/// of bug that breaks it — a `RenderFlex` overflowing by a few pixels — throws
/// no exception at runtime on a device and produces no analyzer warning. It
/// only shows as yellow stripes on a screen somebody happens to be looking at.
///
/// So these pump each shared component at **320dp**, the narrowest width in
/// common Android use, and fail if anything overflows. That is the width where
/// the five-tab bottom navigation and the long-label buttons are tightest.
///
/// `takeException()` is what does the work: Flutter reports an overflow as a
/// FlutterError during layout, and the test harness surfaces it here.
void main() {
  /// The narrowest phone worth supporting. Anything that survives this survives
  /// every larger device.
  const narrow = Size(320, 640);

  /// A mid-range phone, for the case where a layout is *only* wrong when it has
  /// room to spread out.
  const typical = Size(411, 891);

  Future<void> pumpAt(
    WidgetTester tester,
    Size size,
    Widget child,
  ) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = size;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(body: child),
      ),
    );
    await tester.pump(const Duration(milliseconds: 500));
  }

  group('bottom navigation', () {
    // The riskiest layout in the app: five tabs, each with an icon and a label,
    // sharing 320dp — 64dp per tab.
    for (final role in UserRole.values) {
      testWidgets('fits at 320dp for ${role.name}', (tester) async {
        final destinations = destinationsForRole(role);
        if (destinations.isEmpty) return;

        await pumpAt(
          tester,
          narrow,
          AppBottomNav(
            currentIndex: 0,
            onTap: (_) {},
            items: [
              for (final d in destinations)
                AppNavItem(
                  icon: d.icon,
                  activeIcon: d.activeIcon,
                  label: d.label,
                ),
            ],
          ),
        );

        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('shows a badge without overflowing', (tester) async {
      await pumpAt(
        tester,
        narrow,
        AppBottomNav(
          currentIndex: 1,
          onTap: (_) {},
          items: const [
            AppNavItem(icon: Icons.home, label: 'Home', badgeCount: 3),
            AppNavItem(icon: Icons.mail, label: 'Requests', badgeCount: 128),
            AppNavItem(icon: Icons.person, label: 'Account'),
          ],
        ),
      );

      expect(tester.takeException(), isNull);
      // Anything above nine collapses rather than widening the pill.
      expect(find.text('9+'), findsOneWidget);
    });
  });

  group('account tile', () {
    testWidgets('truncates a long name instead of overflowing', (tester) async {
      await pumpAt(
        tester,
        narrow,
        AppCardGroup(
          children: [
            AccountTile(
              name: 'Lakshmi Venkataraman Subramanian Iyer',
              subtitle: 'Administrator · Green Valley Residency, Wakad, Pune',
              seed: 3,
              onTap: () {},
              onForget: () {},
            ),
          ],
        ),
      );

      expect(tester.takeException(), isNull);
    });
  });

  group('buttons', () {
    testWidgets('long label with icon fits at 320dp', (tester) async {
      await pumpAt(
        tester,
        narrow,
        const Padding(
          padding: EdgeInsets.all(AppSpacing.gutter),
          child: Column(
            children: [
              AppButton(
                label: 'Scanning not working? Log by hand',
                icon: Icons.edit_note_rounded,
                onPressed: null,
              ),
              SizedBox(height: AppSpacing.xs),
              AppButton.text(
                label: 'Sign out and forget this device',
                expand: true,
                onPressed: null,
              ),
            ],
          ),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('two buttons side by side fit at 320dp', (tester) async {
      await pumpAt(
        tester,
        narrow,
        const Padding(
          padding: EdgeInsets.all(AppSpacing.gutter),
          child: Row(
            children: [
              Expanded(
                child: AppButton.secondary(
                  label: 'Decline',
                  icon: Icons.close_rounded,
                  onPressed: null,
                ),
              ),
              SizedBox(width: AppSpacing.sm),
              Expanded(
                child: AppButton(
                  label: 'Accept',
                  icon: Icons.check_rounded,
                  onPressed: null,
                ),
              ),
            ],
          ),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  });

  group('state views', () {
    testWidgets('empty state fits in a short viewport', (tester) async {
      // 320x480 is the worst case: a small phone with the keyboard up.
      await pumpAt(
        tester,
        const Size(320, 480),
        const AppEmptyState(
          icon: Icons.person_search_outlined,
          title: 'No one matches that yet',
          message: 'Try clearing your search or filters. Only workers your '
              'administrator has verified appear here.',
          actionLabel: 'Clear filters',
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('error state fits in a short viewport', (tester) async {
      await pumpAt(
        tester,
        const Size(320, 480),
        AppErrorState(
          message: 'The server took too long to respond. Please try again.',
          onRetry: () {},
        ),
      );

      expect(tester.takeException(), isNull);
    });
  });

  group('cards and chips', () {
    testWidgets('status chips wrap rather than overflow', (tester) async {
      await pumpAt(
        tester,
        narrow,
        const Padding(
          padding: EdgeInsets.all(AppSpacing.gutter),
          child: AppCard(
            child: Wrap(
              spacing: AppSpacing.xxs,
              runSpacing: AppSpacing.xxs,
              children: [
                AppStatusChip(label: 'House cleaning', dense: true),
                AppStatusChip(label: 'Cooking', dense: true),
                AppStatusChip(label: 'Elderly care', dense: true),
                AppStatusChip(label: 'Babysitting', dense: true),
                AppStatusChip(label: 'Laundry & ironing', dense: true),
              ],
            ),
          ),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('skeleton card fits at both widths', (tester) async {
      for (final size in [narrow, typical]) {
        await pumpAt(
          tester,
          size,
          const Padding(
            padding: EdgeInsets.all(AppSpacing.gutter),
            child: AppSkeletonCard(),
          ),
        );
        expect(tester.takeException(), isNull, reason: 'at ${size.width}dp');
      }
    });
  });

  group('avatar', () {
    testWidgets('falls back to initials with no photo', (tester) async {
      await pumpAt(
        tester,
        narrow,
        const AppAvatar(name: 'Priya Sharma', seed: 2, size: 56),
      );

      expect(find.text('PS'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('handles a phone number as the name', (tester) async {
      // UserModel.fullName returns the phone number when no name is set, so a
      // leading digit has to render rather than throw.
      await pumpAt(
        tester,
        narrow,
        const AppAvatar(name: '9800000002', seed: 5, size: 44),
      );

      expect(find.text('9'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
