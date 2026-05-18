from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_add_user_department'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(
                condition=~models.Q(email=''),
                fields=['email'],
                name='unique_user_email_when_set',
            ),
        ),
    ]
