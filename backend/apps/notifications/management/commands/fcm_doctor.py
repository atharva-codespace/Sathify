"""Diagnose FCM setup end to end, and optionally send a real push.

    python manage.py fcm_doctor                      # report only
    python manage.py fcm_doctor --send 9800000002    # actually push to that user

Written because every FCM failure looks identical from the app's side — the
phone registers a token and then simply never hears anything. This separates
the four things that can be wrong: server config missing, key file unreadable,
Google rejecting the credentials, or no device token stored for the user.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.models import DeviceSession, User
from apps.notifications import push

OK = "  [OK]  "
BAD = "  [--]  "


class Command(BaseCommand):
    help = "Check the FCM service-account setup and optionally send a test push."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send",
            metavar="PHONE",
            help="Phone number of a user to send a real test notification to.",
        )

    def handle(self, *args, **options):
        cfg = getattr(settings, "FCM_SETTINGS", {})

        self.stdout.write("\n=== 1. Server configuration ===")
        enabled = bool(cfg.get("ENABLED"))
        project = cfg.get("PROJECT_ID") or ""
        path = cfg.get("CREDENTIALS_PATH") or ""

        self.stdout.write(f"{OK if enabled else BAD}FCM_ENABLED         = {enabled}")
        self.stdout.write(f"{OK if project else BAD}FCM_PROJECT_ID      = {project or '(empty)'}")
        self.stdout.write(f"{OK if path else BAD}FCM_CREDENTIALS_PATH = {path or '(empty)'}")

        if not push.is_configured():
            self.stdout.write(
                self.style.ERROR(
                    "\nis_configured() is False -> the server will not even attempt a send.\n"
                    "Set all three in backend/.env, then restart the server."
                )
            )
            return

        self.stdout.write("\n=== 2. Service-account key file ===")
        import json
        import os

        if not os.path.exists(path):
            self.stdout.write(self.style.ERROR(f"{BAD}File does not exist: {path}"))
            self.stdout.write(
                "  Firebase Console > Project Settings > Service accounts >\n"
                "  Generate new private key, then save it at exactly that path."
            )
            return
        self.stdout.write(f"{OK}File exists ({os.path.getsize(path)} bytes)")

        try:
            with open(path, encoding="utf-8") as handle:
                key = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"{BAD}Not valid JSON: {exc}"))
            return

        if key.get("type") != "service_account":
            self.stdout.write(
                self.style.ERROR(
                    f'{BAD}type is "{key.get("type")}", expected "service_account".\n'
                    "  This looks like google-services.json (the CLIENT file), not a\n"
                    "  service-account key. They are different downloads."
                )
            )
            return
        self.stdout.write(f"{OK}type = service_account")
        self.stdout.write(f"{OK}client_email = {key.get('client_email')}")

        key_project = key.get("project_id")
        if key_project != project:
            self.stdout.write(
                self.style.ERROR(
                    f"{BAD}project_id mismatch: key says '{key_project}', "
                    f"FCM_PROJECT_ID says '{project}'. Pushes will 404."
                )
            )
            return
        self.stdout.write(f"{OK}project_id matches FCM_PROJECT_ID ({key_project})")

        self.stdout.write("\n=== 3. Authenticating with Google ===")
        token = push._access_token()  # noqa: SLF001 — diagnosing this exact call
        if not token:
            self.stdout.write(
                self.style.ERROR(
                    f"{BAD}Could not mint an access token. The key may be revoked,\n"
                    "  or this machine's clock may be skewed (JWT signing is time-sensitive)."
                )
            )
            return
        self.stdout.write(f"{OK}Access token obtained ({len(token)} chars)")

        self.stdout.write("\n=== 4. Registered device tokens ===")
        sessions = DeviceSession.objects.exclude(fcm_token="").filter(revoked_at__isnull=True)
        if not sessions:
            self.stdout.write(
                f"{BAD}No device has registered an FCM token yet.\n"
                "  Sign in on the phone — push_service.start() runs after sign-in."
            )
        for session in sessions.select_related("user")[:10]:
            self.stdout.write(
                f"{OK}{session.user.phone_number:<12} {session.device_name or session.device_id} "
                f"token=...{session.fcm_token[-12:]}"
            )

        phone = options.get("send")
        if not phone:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nConfiguration is valid. Re-run with --send <phone> to push for real.\n"
                )
            )
            return

        self.stdout.write(f"\n=== 5. Sending a test push to {phone} ===")
        try:
            user = User.objects.get(phone_number=phone)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"{BAD}No user with phone {phone}"))
            return

        tokens = list(
            DeviceSession.objects.filter(user=user, revoked_at__isnull=True)
            .exclude(fcm_token="")
            .values_list("fcm_token", flat=True)
        )
        if not tokens:
            self.stdout.write(
                self.style.ERROR(f"{BAD}{phone} has no device token. Sign in on the phone first.")
            )
            return

        result = push.send(
            tokens=tokens,
            title="Sathify test",
            body="If you can see this, push notifications are working.",
            data={"route": "/notifications"},
        )
        if result.succeeded:
            self.stdout.write(
                self.style.SUCCESS(f"{OK}Sent to {result.sent} device(s). Check the phone.")
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"{BAD}Not sent. reason={result.reason!r}")
            )
