from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone
from datetime import timedelta
from collections import defaultdict
from myapp.models import EmployeeAttendance, Employees

class Command(BaseCommand):
    help = "Continuous sync for EmployeeAttendance"

    def handle(self, *args, **kwargs):

        since = timezone.now() - timedelta(minutes=1)

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
        WHERE LogDate >= %s
        ORDER BY UserId, log_day, LogDate
        """

        with connection.cursor() as cursor:
            cursor.execute(sql, [since])
            rows = cursor.fetchall()

        grouped = defaultdict(list)
        for day, emp_code, log_time in rows:
            grouped[(emp_code, day)].append(log_time)

        for (emp_code, day), punches in grouped.items():
            try:
                emp = Employees.objects.get(EmployeeCode=emp_code)
            except Employees.DoesNotExist:
                continue

            punches.sort()

            att, _ = EmployeeAttendance.objects.get_or_create(
                Employee=emp,
                AttendanceDate=day
            )

            working = 0
            breaking = 0
            for i in range(len(punches) - 1):
                diff = (punches[i + 1] - punches[i]).total_seconds()
                if i % 2 == 0:
                    working += diff
                else:
                    breaking += diff

            att.PunchIn = punches[0]
            att.PunchOut = punches[-1]
            att.WorkingSeconds = int(working)
            att.BreakSeconds = int(breaking)
            att.Status = "PRESENT"
            att.save()
