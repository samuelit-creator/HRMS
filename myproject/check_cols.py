import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()
from django.db import connection

try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'Employees'")
        rows = cursor.fetchall()
        for row in rows:
            if 'image' in row[0].lower() or 'photo' in row[0].lower() or 'pic' in row[0].lower():
                print(f"FOUND: {row[0]} ({row[1]})")
except Exception as e:
    print(f"Error: {e}")
