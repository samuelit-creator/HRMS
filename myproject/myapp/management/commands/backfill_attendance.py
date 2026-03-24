from django.core.management.base import BaseCommand
from django.db import connection
from collections import defaultdict
from myapp.models import EmployeeAttendance, Employees

class Command(BaseCommand):
    help = "Backfill EmployeeAttendance from DeviceLogs"

    def handle(self, *args, **kwargs):

        sql = """
        SELECT
            CAST(LogDate AS DATE) AS log_day,
            UserId,
            LogDate
        FROM (
            SELECT * FROM dbo.DeviceLogs_12_2025
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_11_2025
        ) dl
        ORDER BY UserId, log_day, LogDate
        """

        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()

        grouped = defaultdict(list)
        for day, emp_code, log_time in rows:
            grouped[(emp_code, day)].append(log_time)

        created = 0

        for (emp_code, day), punches in grouped.items():
            try:
                emp = Employees.objects.get(EmployeeCode=emp_code)
            except Employees.DoesNotExist:
                continue

            punches.sort()

            working = 0
            breaking = 0

            for i in range(len(punches) - 1):
                diff = (punches[i + 1] - punches[i]).total_seconds()
                if i % 2 == 0:
                    working += diff
                else:
                    breaking += diff

            EmployeeAttendance.objects.update_or_create(
                Employee=emp,
                AttendanceDate=day,
                defaults={
                    "PunchIn": punches[0],
                    "PunchOut": punches[-1],
                    "WorkingSeconds": int(working),
                    "BreakSeconds": int(breaking),
                    "Status": "PRESENT"
                }
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Backfill completed. Rows processed: {created}"
        ))
