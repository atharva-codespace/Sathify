"""Narrow the OTP to its two real jobs, and pin the role column.

* ``OtpPurpose`` drops ``login``. Sign-in is phone plus password; a code texted
  on demand beside a password would be a second, weaker way into the account.
  What remains is phone verification at sign-up and password reset.
* Any outstanding ``login`` code is consumed. Its redemption path no longer
  exists, so leaving one live would strand a valid code with nothing to spend
  it on.
* ``role`` gains a database-level check. See the model's Meta for why
  exhaustiveness is the half worth enforcing there.
"""

from django.db import migrations, models


def consume_login_codes(apps, schema_editor):
    """Mark legacy passwordless-login codes as spent."""
    from django.utils import timezone

    OtpCode = apps.get_model("accounts", "OtpCode")
    OtpCode.objects.filter(purpose="login", consumed_at__isnull=True).update(
        consumed_at=timezone.now()
    )


def unconsume_login_codes(apps, schema_editor):
    """No-op reverse: reviving a spent code on rollback would be worse than
    losing it, and a 2-minute code has almost certainly expired anyway."""


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_otpcode_devicesession'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('societies', '0003_resident_average_rating_resident_rating_count_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='otpcode',
            name='purpose',
            field=models.CharField(choices=[('registration', 'Phone verification at registration'), ('password_reset', 'Password reset for a forgotten password')], default='registration', max_length=20),
        ),
        migrations.RunPython(consume_login_codes, unconsume_login_codes),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.CheckConstraint(condition=models.Q(('role__in', ['resident', 'worker', 'guard', 'society_admin'])), name='user_role_is_one_of_the_four_roles'),
        ),
    ]
