import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from myapp.models import AppConfiguration

def check_config():
    config = AppConfiguration.objects.get(ConfigKey='show_candidate_register')
    print(f"Current Value: {config.ConfigValue}")

print("Before toggle check:")
check_config()

# Simulate toggle (we don't have a request object to call the view, but we can test the logic)
config = AppConfiguration.objects.get(ConfigKey='show_candidate_register')
config.ConfigValue = 'False' if config.ConfigValue == 'True' else 'True'
config.save()

print("After simulated toggle:")
check_config()

# Set back to True for user
config.ConfigValue = 'True'
config.save()
print("Reset to True.")
