import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/society_models.dart';
import '../../data/repositories/society_repository.dart';

final societyRepositoryProvider =
    Provider<SocietyRepository>((ref) => SocietyRepository());

/// Societies available to register into.
///
/// `autoDispose` because this is only needed on the registration screen; keeping
/// the list alive for the whole session would be dead weight.
final publicSocietiesProvider =
    FutureProvider.autoDispose.family<List<SocietySummary>, String>(
  (ref, search) =>
      ref.read(societyRepositoryProvider).fetchPublicSocieties(search: search),
);

/// The caller's own society.
final mySocietyProvider = FutureProvider.autoDispose<Society>(
  (ref) => ref.read(societyRepositoryProvider).fetchMySociety(),
);

final towersProvider = FutureProvider.autoDispose<List<Tower>>(
  (ref) => ref.read(societyRepositoryProvider).fetchTowers(),
);

/// Flats in a given tower. Null tower means every tower in the society.
final flatsProvider = FutureProvider.autoDispose.family<List<Flat>, int?>(
  (ref, towerId) =>
      ref.read(societyRepositoryProvider).fetchFlats(towerId: towerId),
);

/// The administrator's pending-resident queue (Module 2.3).
final pendingResidentsProvider =
    FutureProvider.autoDispose<List<ResidentProfile>>(
  (ref) => ref.read(societyRepositoryProvider).fetchPendingResidents(),
);

/// The caller's own resident profile, if they have claimed a flat.
final myResidentProfileProvider = FutureProvider.autoDispose<ResidentProfile?>(
  (ref) async {
    try {
      return await ref.read(societyRepositoryProvider).fetchMyResidentProfile();
    } on Exception {
      // 404 simply means "no flat claimed yet", which is a normal state during
      // onboarding rather than an error worth surfacing.
      return null;
    }
  },
);
