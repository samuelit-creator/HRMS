import logging
from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from myapp.models import Employees, EmployeePassword

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Manage employee passwords: list missing, initialize default, or reset/set specific passwords.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--list-missing',
            action='store_true',
            help='List employees who do not have a password record.',
        )
        parser.add_argument(
            '--init-all',
            action='store_true',
            help='Create default password records (Welcome@123) for all employees who don\'t have one.',
        )
        parser.add_argument(
            '--reset',
            type=str,
            help='Reset the password for a specific EmployeeCode to the default (Welcome@123).',
        )
        parser.add_argument(
            '--set',
            nargs=2,
            metavar=('EMPLOYEE_CODE', 'PASSWORD'),
            help='Set a specific password for an employee.',
        )

    def handle(self, *args, **options):
        if options['list_missing']:
            self.list_missing()
        elif options['init_all']:
            self.init_all()
        elif options['reset']:
            self.reset_password(options['reset'])
        elif options['set']:
            self.set_password(options['set'][0], options['set'][1])
        else:
            self.print_help('manage.py', 'manage_passwords')

    def list_missing(self):
        # Employees who DON'T have a record in EmployeePassword
        employees_without_pw = Employees.objects.exclude(
            EmployeeCode__in=EmployeePassword.objects.values_list('Employee_id', flat=True)
        )
        
        if not employees_without_pw.exists():
            self.stdout.write(self.style.SUCCESS('All employees have password records.'))
            return

        self.stdout.write(f'Found {employees_without_pw.count()} employees without password records:')
        for emp in employees_without_pw:
            self.stdout.write(f'- {emp.EmployeeCode}: {emp.EmployeeName}')

    def init_all(self):
        default_pw = "Welcome@123"
        employees_without_pw = Employees.objects.exclude(
            EmployeeCode__in=EmployeePassword.objects.values_list('Employee_id', flat=True)
        )
        
        count = 0
        for emp in employees_without_pw:
            EmployeePassword.objects.create(
                Employee_id=emp.EmployeeCode,
                PasswordHash=make_password(default_pw)
            )
            count += 1
        
        if count > 0:
            self.stdout.write(self.style.SUCCESS(f'Successfully initialized {count} password records with default password.'))
        else:
            self.stdout.write(self.style.SUCCESS('No missing password records found.'))

    def reset_password(self, emp_code):
        default_pw = "Welcome@123"
        self.set_password(emp_code, default_pw)

    def set_password(self, emp_code, password):
        try:
            employee = Employees.objects.get(EmployeeCode=emp_code)
            obj, created = EmployeePassword.objects.update_or_create(
                Employee_id=emp_code,
                defaults={'PasswordHash': make_password(password)}
            )
            verb = "created" if created else "updated"
            self.stdout.write(self.style.SUCCESS(f'Successfully {verb} password for {emp_code} ({employee.EmployeeName}).'))
        except Employees.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Employee with code {emp_code} does not exist.'))
