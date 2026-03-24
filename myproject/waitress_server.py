import os
from waitress import serve

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

serve(
    application,
    host='0.0.0.0',
    port=8000,
    threads=4
)
