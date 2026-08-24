"""Module 14 — Platform Operations: the Superadmin console."""

from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.console"
    verbose_name = "Platform Operations Console"
