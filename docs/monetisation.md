# How Sathify earns

A revenue model for a two-sided marketplace where one side is affluent and the
other is not. That asymmetry decides almost everything below, so it is worth
stating before the numbers: **a rupee taken from a domestic worker's wage costs
far more than it earns.** It is a large fraction of their income, it is invisible
to the resident, and it is trivially avoided — the informal market charges zero
commission and is one phone call away.

Everything here follows from that.

---

## The structural problem, first

Recurring domestic help disintermediates faster than almost any other
marketplace category. After the first month, the resident and the worker have
each other's numbers, a routine, and a cash relationship. A platform charging
per-transaction fees on a recurring engagement is charging for a match it made
once, and both sides can stop paying by simply not telling it.

This is not a pricing problem to solve with a lower percentage. It means
**per-transaction commission cannot be the primary revenue line.** The durable
revenue is what stays useful every month:

| Revenue line | Who pays | Why it survives disintermediation |
| --- | --- | --- |
| **Society subscription** | The society (B2B) | The gate log, attendance record and complaint queue are the society's own operational system. Leaving means losing their records. |
| Verification services | Worker or society | A background check is a one-off product, not a rent. |
| Convenience fee on one-day bookings | Resident | Genuinely transactional — there is no relationship to disintermediate. |
| Digital payment rail | Resident | Only works while it is *easier* than cash. |
| Ads | Advertiser | Independent of the transaction entirely. |
| Commission on recurring wages | — | **Do not build this.** |

**Ranked by what to build first: society subscription → convenience fee on
bookings → verification → ads.** Tipping is not a revenue line at all; it is
below because it must be built correctly, not because it earns anything.

---

## 1. Society subscription — the main line

Sold to the society's managing committee, not to residents. What they are buying
is the administrator's side of the product, which already exists: the approval
queues, the gate log, the complaint SLA queue, the directory, the monthly
reports (`apps/administration/`).

```
FREE          up to 25 workers · gate log · complaints · 30-day history
              (the whole point: a society must be able to run on this
               indefinitely, or they will never migrate their records in)

STANDARD      ₹1,500/month or ₹15,000/year
              unlimited workers · 12-month history · monthly PDF reports
              · attendance exports · SLA escalation · 3 admin accounts

PLUS          ₹4,000/month
              + background verification credits (5/month)
              + multi-gate support · API access for the society's ERP
              + priority replacement matching for residents
```

Price anchor: a 200-flat society already spends ₹25,000–40,000/month on security
staffing. ₹1,500 is a rounding error against that, and the pitch is a register
that cannot be lost, back-dated or argued with.

```python
# apps/payments/models.py — additions

class SubscriptionTier(models.TextChoices):
    FREE = "free", _("Free")
    STANDARD = "standard", _("Standard")
    PLUS = "plus", _("Plus")


class SocietySubscription(TimeStampedModel):
    """What a society is entitled to. One row per society, always.

    A society with no row is FREE — the absence of a subscription is a valid,
    fully functional state and must never be a broken one. Every gate check,
    every attendance write and every complaint keeps working when a
    subscription lapses; only the administrator's reporting surface narrows.
    Locking a society out of its own attendance record for an unpaid invoice
    would put workers' wages behind a billing dispute.
    """

    society = models.OneToOneField(
        "societies.Society", on_delete=models.CASCADE, related_name="subscription"
    )
    tier = models.CharField(
        max_length=20, choices=SubscriptionTier.choices, default=SubscriptionTier.FREE
    )
    valid_until = models.DateField(null=True, blank=True)
    #: Razorpay subscription id, when billed automatically rather than by invoice.
    provider_reference = models.CharField(max_length=64, blank=True)

    @property
    def is_active(self) -> bool:
        if self.tier == SubscriptionTier.FREE:
            return True
        return self.valid_until is not None and self.valid_until >= timezone.localdate()

    @property
    def effective_tier(self) -> str:
        """Lapsed paid tiers read as FREE rather than as themselves."""
        return self.tier if self.is_active else SubscriptionTier.FREE


#: What each tier unlocks. A dict rather than per-tier `if` branches scattered
#: through views, so "what does Standard get?" has exactly one answer.
TIER_LIMITS = {
    SubscriptionTier.FREE:     {"workers": 25,  "history_days": 30,  "admins": 1, "reports": False},
    SubscriptionTier.STANDARD: {"workers": None, "history_days": 365, "admins": 3, "reports": True},
    SubscriptionTier.PLUS:     {"workers": None, "history_days": 1095, "admins": 10, "reports": True},
}
```

**Individual homes** (a resident in a society with no subscription) are not sold
a subscription. They are the acquisition channel: they use the product free,
and the pitch to their committee writes itself once thirty flats are on it.
Charge them per booking (below) and nothing else.

---

## 2. Convenience fee on one-day bookings

The only per-transaction fee worth charging, because a one-day booking is a
genuine transaction with no relationship to disintermediate.

- **8% of the booking value, paid by the resident, capped at ₹40.**
- Charged **on top of** the worker's quoted rate, never deducted from it. The
  worker sees "you will receive ₹500" and receives ₹500.
- Shown as its own line before payment. A fee discovered on the receipt is worse
  than a larger fee disclosed up front.
- **Zero fee on recurring salary payments**, `PaymentKind.ENGAGEMENT_SALARY`.
  That is a wage transfer, not a marketplace transaction.

```python
# apps/payments/fees.py — new

"""Platform fees. One module, so "what does Sathify charge?" is greppable."""

from __future__ import annotations

#: Fraction of a one-day booking added as a convenience fee.
BOOKING_FEE_RATE = 0.08

#: Ceiling, in paise. Without it a ₹2,000 deep-clean carries a ₹160 fee, which
#: is not 20× more service than a ₹100 one.
BOOKING_FEE_CAP_PAISE = 4_000

#: Kinds that are never charged a fee, and why:
#:   ENGAGEMENT_SALARY — a wage transfer between two people.
#:   TIP               — see the tipping section; 100% reaches the worker.
#:   REPLACEMENT       — already a deduction from someone's wage.
#:   REFUND            — charging a fee to undo a charge is indefensible.
FEE_EXEMPT_KINDS = frozenset({
    PaymentKind.ENGAGEMENT_SALARY,
    PaymentKind.TIP,
    PaymentKind.REPLACEMENT,
    PaymentKind.REFUND,
})


def platform_fee_paise(*, kind: str, amount_paise: int, society=None) -> int:
    """The fee on one payment. Returns 0 for every exempt case.

    Rounds **down**: where a fraction of a paise exists, it stays with the
    people in the transaction rather than the platform. Consistent with
    ``ReplacementSplit.split``, which sends its remainder to whoever did the
    work.
    """
    if kind in FEE_EXEMPT_KINDS or amount_paise <= 0:
        return 0

    subscription = getattr(society, "subscription", None)
    if subscription and subscription.effective_tier == SubscriptionTier.PLUS:
        return 0  # bundled into the society's plan

    return min(int(amount_paise * BOOKING_FEE_RATE), BOOKING_FEE_CAP_PAISE)
```

`Payment` needs one column, `platform_fee_paise`, frozen at creation. Deriving
the fee at read time means a rate change silently rewrites historical receipts.

---

## 3. Tipping — where 100% must actually mean 100%

`PaymentKind.TIP` and `Payment.tip_paise` already exist
(`apps/payments/models.py:71,136`), and `charge` already accepts `tip_paise`
alongside a salary or booking so the tip rides the same Razorpay order. What is
missing is the routing that gets it to the worker whole.

### The honest arithmetic

A ₹100 tip paid by UPI does not deliver ₹100 unless somebody absorbs the gateway
fee. Razorpay charges roughly 2% + GST on the transaction regardless of who the
money is destined for. There are exactly three options:

| Option | Worker receives | Honest? |
| --- | --- | --- |
| Deduct the gateway fee from the tip | ₹97.6 | Only if the UI says "₹97.60 will reach them" |
| **Platform absorbs the gateway fee** | **₹100** | **Yes — recommended** |
| Gross the tip up at checkout | ₹100 (resident pays ₹102.40) | Yes, but it prices tipping oddly |

**Absorb it.** A tip averages ₹50–200; at 2% the platform pays ₹1–4 to keep a
promise that is doing real reputational work. Budget it as marketing, not as
payments. And never write "100% goes to your helper" unless option 2 or 3 is
live — that claim is checkable, and getting caught on it is worse than not
making it.

### The split, mechanically

Razorpay **Route** is the mechanism. Each worker becomes a *linked account*; a
payment is created with `transfers[]` specifying who gets what, and Razorpay
settles to each account directly. Funds never sit in Sathify's account, which
matters legally: a marketplace that pools and redistributes other people's money
is doing something the RBI has opinions about, and Route exists precisely to
avoid it.

```python
# apps/payments/services.py — the transfer construction

def _transfers_for(payment) -> list[dict]:
    """Route instructions for one order.

    The tip is a separate transfer with `on_hold: False` and no commission, so
    it is visibly untouched in the Razorpay dashboard as well as in our own
    ledger. Two transfers rather than one summed amount, because a worker
    asking "did my tip arrive?" deserves a row that says so.
    """
    worker_account = payment.payee.worker_profile.razorpay_account_id
    if not worker_account:
        return []  # falls back to the manual payout path below

    transfers = []

    if payment.amount_paise:
        transfers.append({
            "account": worker_account,
            "amount": payment.amount_paise - payment.platform_fee_paise,
            "currency": "INR",
            "notes": {"payment": str(payment.pk), "kind": payment.kind},
        })

    if payment.tip_paise:
        transfers.append({
            "account": worker_account,
            "amount": payment.tip_paise,          # untouched, deliberately
            "currency": "INR",
            "notes": {"payment": str(payment.pk), "kind": "tip"},
        })

    return transfers
```

### The onboarding cost nobody mentions

Route requires each worker to have a **linked account with a bank account and
PAN**. For this user population that is a real barrier — many have a Jan Dhan
account and no PAN. Plan for it:

1. **Phase 1 (now, test mode):** no Route. Tips accumulate against the worker's
   `Payment` rows and are settled by the society administrator in cash, with the
   app producing the list. Honest, works today, needs no KYC.
2. **Phase 2:** RazorpayX **payouts** to a UPI ID or bank account. Needs an
   account number or VPA, not a PAN. This clears most of the population.
3. **Phase 3:** full Route linked accounts for workers who can complete it.

The `Payment` ledger is identical in all three. Only the settlement mechanism
changes, which is the point of keeping it behind one function.

### The UI

Tipping fails when it is a form. It works when it is one tap at a moment of
gratitude:

- Three chips — ₹50 / ₹100 / ₹200 — on the rating screen after a 5-star rating,
  and nowhere else. Never a text field first.
- Never prompted after a low rating. Asking someone to tip the person they just
  complained about reads as tone-deaf.
- Festival prompt once a year (Diwali), dismissible permanently on first
  dismissal, and never re-shown that year.
- Never a default-on checkbox. A tip that has to be un-ticked is not a tip.

---

## 4. Premium worker badges — carefully

The obvious product is "pay to rank higher." **Do not sell that**, at least not
as such. `apps/hiring/scoring.py` is a trust score built from attendance,
ratings and response rate, and residents are being asked to hire someone into
their home on the strength of it. A paid position inside that ranking makes the
whole score advisory at best and dishonest at worst — and it inverts the
platform's purpose by selling the top of the list to whoever can pay, which is
the opposite of the worker it is meant to lift.

Sell the *thing*, not the *position*:

| Product | Price | What the resident sees | Who pays |
| --- | --- | --- | --- |
| Police verification | ₹250 one-off | "Police verified · Mar 2026" | Worker or society |
| Skills certification | ₹150/module | "Certified: elder care" | Worker, often sponsored |
| Featured placement | ₹99/month | "Promoted" label, capped at 1 in 5 results | Worker |

Rules that make this survivable:

- **Verification badges are earned, and expire.** A badge with no date on it is
  a claim, not a fact.
- **Featured placement is disclosed and bounded.** Labelled "Promoted",
  never more than one in five results, and it re-orders *within* the trust band
  it already qualified for — it can never lift a 2-star worker above a 4-star
  one. Implement as a post-sort insertion, not a term in `WEIGHTS`. The scoring
  formula stays pure and auditable.
- **A worker who cannot pay anything must still be findable and hireable.** If
  the free experience is bad, the model has quietly become a tax on poverty.

```python
# apps/hiring/scoring.py stays untouched. Promotion is a separate, later step:

def apply_promotions(ranked: list[MatchScore], *, slot_every: int = 5) -> list[MatchScore]:
    """Insert promoted workers into an already-scored list.

    Deliberately *after* scoring, never inside it: the trust score is what the
    resident is told they are reading, and money must not be one of its inputs.
    A promoted worker is only ever moved up within their own trust band.
    """
```

---

## 5. Ads

Sathify has two audiences and only one of them should ever see an ad.

**Never show ads to workers.** Their screens are earnings, schedule, gate pass
and KYC. Serving a loan advertisement to someone checking whether they were paid
is predatory, and the "personal loan / instant credit" inventory that targets
this demographic is exactly what would fill it. It also earns almost nothing:
their eCPM is a fraction of the resident's.

**Never show ads on:** the gate scanner, any KYC or face-check screen, the
payment flow, the complaint form, or anything a guard uses. Two reasons: an
interstitial during a gate scan costs someone their entry, and an ad next to an
Aadhaar upload destroys the credibility of the consent notice sitting beside it.

Where ads are acceptable, for residents only:

| Placement | Format | Rule |
| --- | --- | --- |
| Below the resident home feed | Native, styled as a card | One per session |
| Service catalogue, after the 6th result | Native inline | Labelled "Sponsored" |
| Completed-booking screen | Banner | Never interstitial |

```dart
/// Ads are opt-out by role and by screen, decided in one place so a new screen
/// cannot accidentally inherit them.
class AdPolicy {
  const AdPolicy._();

  /// Workers and guards never see advertising. Not a setting — a rule.
  static bool allowedFor(UserRole role) => role == UserRole.resident;

  static const Set<String> _forbiddenRoutes = {
    Routes.gateScanner, Routes.gateLog, Routes.kycUpload,
    Routes.selfCheckIn, Routes.payments, Routes.raiseComplaint,
  };

  static bool allowedOn(String route, UserRole role) =>
      allowedFor(role) && !_forbiddenRoutes.contains(route);
}
```

Practical note: AdMob needs a privacy policy URL, a DPDP-compliant consent
prompt for personalised ads in India, and `AdMob → app-ads.txt`. At pilot scale
(a few hundred residents) ad revenue will be **under ₹500/month**. Build it last;
it is a scale play, not a runway play.

---

## What this adds up to

One 200-flat society, twelve months in, on Standard:

| Line | Monthly |
| --- | --- |
| Society subscription | ₹1,500 |
| ~60 one-day bookings × ₹28 avg fee | ₹1,680 |
| 4 verifications | ₹1,000 |
| Ads (~180 active residents) | ₹300 |
| **Total per society** | **≈ ₹4,480** |
| Tips processed | ₹12,000 → **₹0 revenue, by design** |

Infrastructure cost at that scale is roughly ₹600/month (`docs/free-tier-constraints.md`).
**One paying society covers the platform's running costs with room to spare**,
which is the number that actually matters — it means the model does not require
scale to stop losing money.

---

## Sequencing

1. `platform_fee_paise` + the `Payment.platform_fee_paise` column. Set the rate
   to **0** and ship it. Fees existing but zero is a schema change; fees
   appearing later is a pricing change, and doing them separately means the
   receipt layout is already right when the rate moves.
2. `SocietySubscription` with everyone on FREE. Gate reporting on
   `effective_tier`.
3. Sell Standard to one society by hand. No self-serve checkout until somebody
   has actually paid.
4. Turn the booking fee on, disclosed, at 8%.
5. Tips: phase 1 settlement (administrator cash list) → RazorpayX payouts.
6. Verification badges.
7. Ads, last.
