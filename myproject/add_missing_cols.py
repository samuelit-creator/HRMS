from django.db import connection, transaction
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

def add_columns():
    desired_cols = {
        "Qualification": "NVARCHAR(200)",
        "MaritalStatus": "NVARCHAR(50)",
        "Designation": "NVARCHAR(200)",
        "TotalExperience": "NVARCHAR(100)",
        "PreviousOrganization": "NVARCHAR(200)",
        "BGVDetails": "NVARCHAR(MAX)",
        "AAPCCertification": "NVARCHAR(10)",
        "CredentialNumber": "NVARCHAR(100)",
        "EmergencyContactNumber": "NVARCHAR(20)",
        "PANNumber": "NVARCHAR(20)",
        "UANNumber": "NVARCHAR(20)"
    }

    with connection.cursor() as cursor:
        # Get existing columns
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'Employees'")
        existing_cols = [row[0] for row in cursor.fetchall()]
        print(f"Existing columns: {existing_cols}")

        for col, sql_type in desired_cols.items():
            if col not in existing_cols:
                print(f"Adding column {col}...")
                try:
                    cursor.execute(f"ALTER TABLE Employees ADD {col} {sql_type}")
                    print(f"Successfully added {col}")
                except Exception as e:
                    print(f"Error adding {col}: {e}")
            else:
                print(f"Column {col} already exists.")

if __name__ == "__main__":
    add_columns()
