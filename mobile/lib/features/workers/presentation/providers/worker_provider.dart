import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/worker_models.dart';
import '../../data/repositories/worker_repository.dart';

final workerRepositoryProvider =
    Provider<WorkerRepository>((ref) => WorkerRepository());

/// The catalogue a worker picks their services from.
///
/// Not `autoDispose`: it is short, changes rarely, and is read on every step of
/// onboarding, so keeping it for the session saves round trips on a connection
/// that may be poor.
final serviceTypesProvider = FutureProvider<List<ServiceType>>(
  (ref) => ref.read(workerRepositoryProvider).fetchServiceTypes(),
);

/// The worker's own profile. Null means they have not started onboarding —
/// a normal state, which is why the repository turns that 404 into null.
final myWorkerProfileProvider = FutureProvider.autoDispose<WorkerProfile?>(
  (ref) => ref.read(workerRepositoryProvider).fetchMyProfile(),
);

/// Every KYC attempt, newest first. A re-upload after a poor scan stays visible.
final myKycAttemptsProvider = FutureProvider.autoDispose<List<KycDocument>>(
  (ref) => ref.read(workerRepositoryProvider).fetchMyKycAttempts(),
);

/// One attempt — also the poll target while OCR runs.
final kycAttemptProvider = FutureProvider.autoDispose.family<KycDocument, int>(
  (ref, kycId) => ref.read(workerRepositoryProvider).fetchKycAttempt(kycId),
);

/// Module 3.6 — the worker's consent records, one per purpose.
final myConsentsProvider = FutureProvider.autoDispose<List<ConsentRecord>>(
  (ref) => ref.read(workerRepositoryProvider).fetchConsents(),
);

/// Module 3.5 — the administrator's worker approval queue.
final pendingWorkersProvider = FutureProvider.autoDispose<List<WorkerReview>>(
  (ref) => ref.read(workerRepositoryProvider).fetchPendingWorkers(),
);

/// Refreshes everything an onboarding step could have changed.
///
/// Profile and KYC are invalidated together because they gate each other:
/// uploading a document changes the profile's `kyc_status`, and adding a photo
/// changes whether the administrator can approve at all.
void invalidateOnboarding(WidgetRef ref) {
  ref.invalidate(myWorkerProfileProvider);
  ref.invalidate(myKycAttemptsProvider);
}
