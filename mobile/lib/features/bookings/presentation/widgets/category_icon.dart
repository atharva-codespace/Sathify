import 'package:flutter/material.dart';

/// Resolves the server's Material icon *name* to an actual [IconData].
///
/// Flutter tree-shakes icons, so there is no runtime lookup by name — a const
/// map is the only way to turn a string from the API into a drawable icon
/// without shipping the entire icon font. Unknown names fall back rather than
/// throwing, so an operator adding a category from the admin gets a generic
/// icon instead of a crash.
const Map<String, IconData> _iconsByName = {
  'cleaning_services': Icons.cleaning_services,
  'local_shipping': Icons.local_shipping,
  'celebration': Icons.celebration,
  'restaurant': Icons.restaurant,
  'emergency': Icons.emergency,
  'handyman': Icons.handyman,
  'home_repair_service': Icons.home_repair_service,
  'child_care': Icons.child_care,
  'yard': Icons.yard,
  'local_laundry_service': Icons.local_laundry_service,
};

IconData iconForCategory(String name) =>
    _iconsByName[name] ?? Icons.home_repair_service;
