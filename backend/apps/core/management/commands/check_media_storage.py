"""Prove — or disprove — that uploaded media can actually be stored.

    python manage.py check_media_storage

Runs the exact sequence a worker's Aadhaar upload runs: write a file through
Django's configured storage, read it back, ask for its URL, delete it. Reports
which step failed and why.

Why this exists as a command rather than a test: the failure it diagnoses is
*environmental*, and the environment that matters is the deployed one. The
whole onboarding flow passes locally and fails on the server, which narrows the
cause to configuration this command can read and a filesystem it can poke —
neither of which a test suite on a developer's laptop can see.

Two failure modes it separates, because the remedies are opposite:

* **No credentials.** ``prod.py`` only switches to Supabase Storage when all
  three of ``SUPABASE_STORAGE_ENDPOINT``/``_ACCESS_KEY``/``_SECRET_KEY`` are
  set, and falls back to the local disk otherwise. On Render that disk is
  ephemeral: uploads appear to work and vanish on the next deploy.
* **Bad credentials, or an unwritable path.** The upload raises, and Module 3's
  KYC view turns that into "we could not save your document just now".
"""

from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

_PROBE = b"sathify storage probe"


class Command(BaseCommand):
    help = "Check that uploaded media can be written, read, addressed and deleted."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Leave the probe file behind instead of deleting it.",
        )

    def handle(self, *args, **options):
        self._report_configuration()

        name = f"diagnostics/storage-probe-{uuid.uuid4().hex}.txt"
        stored = None

        stored = self._step(
            "write", lambda: default_storage.save(name, ContentFile(_PROBE))
        )
        if stored is None:
            self._verdict(False)
            return

        content = self._step(
            "read back", lambda: default_storage.open(stored).read()
        )
        if content is not None and content != _PROBE:
            self.stdout.write(
                self.style.ERROR("  read back  : content did not match what was written")
            )

        # A URL is not cosmetic: every profile response serialises the photo
        # through it, so a backend that stores happily but cannot address a
        # file still takes down the screens that show one.
        self._step("build url", lambda: default_storage.url(stored))

        if options["keep"]:
            self.stdout.write(f"  cleanup    : skipped, probe left at {stored}")
        else:
            self._step("delete", lambda: default_storage.delete(stored))

        self._verdict(True)

    # -- pieces ------------------------------------------------------------

    def _report_configuration(self):
        backend = settings.STORAGES["default"]["BACKEND"]
        is_s3 = "s3" in backend.lower()

        self.stdout.write("=" * 72)
        self.stdout.write(self.style.SUCCESS("  MEDIA STORAGE CHECK"))
        self.stdout.write("=" * 72)
        self.stdout.write(f"  settings   : {os.environ.get('DJANGO_SETTINGS_MODULE', '?')}")
        self.stdout.write(f"  backend    : {backend}")
        self.stdout.write(f"  class      : {type(default_storage).__name__}")

        if is_s3:
            options = settings.STORAGES["default"].get("OPTIONS", {})
            self.stdout.write(f"  bucket     : {options.get('bucket_name')}")
            self.stdout.write(f"  endpoint   : {options.get('endpoint_url')}")
            self.stdout.write(f"  region     : {options.get('region_name')}")
        else:
            root = getattr(settings, "MEDIA_ROOT", None)
            self.stdout.write(f"  MEDIA_ROOT : {root}")
            self.stdout.write(f"  exists     : {os.path.isdir(root) if root else False}")
            self.stdout.write(
                f"  writable   : {os.access(root, os.W_OK) if root and os.path.isdir(root) else False}"
            )
            # Worth saying plainly: this configuration loses every upload on
            # the next restart, which is a different problem from a crash and
            # is easy to miss because nothing errors.
            for key in (
                "SUPABASE_STORAGE_ENDPOINT",
                "SUPABASE_STORAGE_ACCESS_KEY",
                "SUPABASE_STORAGE_SECRET_KEY",
            ):
                if not os.environ.get(key):
                    self.stdout.write(
                        self.style.WARNING(f"  unset      : {key}")
                    )
            self.stdout.write(
                self.style.WARNING(
                    # ASCII only: this prints to whatever console the operator
                    # has, and Windows' default code page turns an em dash into
                    # a replacement character.
                    "  NOTE       : local-disk storage. On Render this disk is "
                    "ephemeral - uploads survive until the next deploy or sleep."
                )
            )
        self.stdout.write("-" * 72)

    def _step(self, label, action):
        """Run one storage operation, reporting the exception rather than raising.

        The point of the command is to report *all* the information available,
        so one failing step must not hide the ones after it.
        """
        try:
            result = action()
        except Exception as exc:  # noqa: BLE001 — reporting is the whole job
            self.stdout.write(
                self.style.ERROR(f"  {label:<11}: FAILED  {type(exc).__name__}: {exc}")
            )
            return None

        # `delete` returns None; `read` returns bytes; the rest return a str.
        if result is None:
            shown = ""
        elif isinstance(result, str):
            shown = result
        else:
            shown = f"{len(result)} bytes"

        self.stdout.write(self.style.SUCCESS(f"  {label:<11}: ok      {shown}"))
        return result

    def _verdict(self, ok):
        self.stdout.write("-" * 72)
        if ok:
            self.stdout.write(
                self.style.SUCCESS(
                    "  Storage is usable. If uploads still fail from the app, the "
                    "cause is not the media backend."
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "  Storage cannot be written to. This is what Module 3's "
                    '"we could not save your document" is reporting.'
                )
            )
        self.stdout.write("=" * 72)
