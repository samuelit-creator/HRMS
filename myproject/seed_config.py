import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from myapp.models import AppConfiguration

config, created = AppConfiguration.objects.get_or_create(
    ConfigKey='show_candidate_register',
    defaults={'ConfigValue': 'True', 'Description': 'Toggle visibility of Candidate Register in sidebar'}
)

if not created:
    config.ConfigValue = 'True'
    config.save()

print(f"Configuration seeded: {config}")
