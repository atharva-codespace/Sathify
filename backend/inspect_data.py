"""Throwaway inspection helper: what reference data already exists?"""

from apps.accounts.models import User
from apps.bookings.models import ServiceCategory
from apps.societies.models import Flat, Gate, Society, Tower
from apps.workers.models import ServiceType, WorkerProfile

print("Society        :", Society.objects.count())
for s in Society.objects.all():
    print("   -", s.id, s.name, s.city, s.status)
print("Tower          :", Tower.objects.count())
print("Flat           :", Flat.objects.count())
print("Gate           :", Gate.objects.count())
print("ServiceType    :", ServiceType.objects.count())
for t in ServiceType.objects.all()[:20]:
    print("   -", t.slug, t.name)
print("ServiceCategory:", ServiceCategory.objects.count())
for c in ServiceCategory.objects.all()[:20]:
    print("   -", c.slug, c.name, c.price_min, "-", c.price_max)
print("User           :", User.objects.count())
for u in User.objects.all():
    print("   -", u.phone_number, u.role, "approved=", u.is_approved, "society=", u.society_id)
print("WorkerProfile  :", WorkerProfile.objects.count())
