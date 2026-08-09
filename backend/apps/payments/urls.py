"""Module 8 — Payments & Payouts: routes (mounted at /api/v1/payments/)."""

from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    # --- 8.1 Razorpay --------------------------------------------------------
    # Declared before the "<uuid:pk>/" routes so these literals are never
    # mistaken for a payment id.
    path("webhook/", views.RazorpayWebhookView.as_view(), name="webhook"),
    path("salary-basis/", views.SalaryBasisView.as_view(), name="salary-basis"),
    path(
        "engagement/",
        views.CreateEngagementPaymentView.as_view(),
        name="pay-engagement",
    ),
    path("booking/", views.CreateBookingPaymentView.as_view(), name="pay-booking"),

    # --- 8.7 Fees, subscription, tip settlement ------------------------------
    path("fees/quote/", views.FeeQuoteView.as_view(), name="fee-quote"),
    path(
        "subscription/",
        views.SocietySubscriptionView.as_view(),
        name="society-subscription",
    ),
    path("tips/owed/", views.TipsOwedView.as_view(), name="tips-owed"),

    # --- 8.3 Summaries -------------------------------------------------------
    path("summary/", views.MonthlySummaryView.as_view(), name="summary"),
    path("summary/csv/", views.MonthlySummaryCsvView.as_view(), name="summary-csv"),
    path("summary/pdf/", views.MonthlySummaryPdfView.as_view(), name="summary-pdf"),

    # --- 8.6 Disputes --------------------------------------------------------
    path("disputes/", views.DisputeListView.as_view(), name="dispute-list"),
    path(
        "disputes/<int:pk>/resolve/",
        views.ResolveDisputeView.as_view(),
        name="dispute-resolve",
    ),

    # --- 8.5 Replacement split ----------------------------------------------
    path(
        "split/<int:engagement_id>/",
        views.ReplacementSplitView.as_view(),
        name="replacement-split",
    ),

    # --- 8.2 Ledger ----------------------------------------------------------
    path("", views.PaymentListView.as_view(), name="payment-list"),
    path("<uuid:pk>/", views.PaymentDetailView.as_view(), name="payment-detail"),
    path("<uuid:pk>/receipt/", views.ReceiptView.as_view(), name="receipt"),
    path("<uuid:pk>/checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("<uuid:pk>/confirm/", views.ConfirmCheckoutView.as_view(), name="confirm"),
    # 8.9 — the UPI link/QR for this payment. Any payment, not only emergencies.
    path("<uuid:pk>/upi/", views.PaymentUpiView.as_view(), name="payment-upi"),
    # 8.9 — an administrator confirms the transfer arrived. The only settlement
    # path in this module that is not backed by a verified signature.
    path(
        "<uuid:pk>/settle-upi/",
        views.ConfirmUpiSettlementView.as_view(),
        name="settle-upi",
    ),
    path("<uuid:pk>/dispute/", views.RaiseDisputeView.as_view(), name="raise-dispute"),
]
