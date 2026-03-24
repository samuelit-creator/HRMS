from django.shortcuts import render,redirect
from typing import cast, Any, Dict
from django.utils import timezone
from datetime import timedelta, time as dtime
from datetime import time 
from .models import DeviceLogs122025, Employees, EmployeePassword, MasterData, CompanyAnnouncement, OnboardingRequest, AppConfiguration, ExpenseClaim, Asset, AssetAllocation, HelpdeskTicket, Kudos, KudosLike, PulseSurvey, SurveyResponse, SurveyQuestion, SurveyAnswer
import pandas as pd
from django.db import connection
from datetime import date
from datetime import datetime
from django.contrib.auth.hashers import check_password
from myapp.models import Employees, EmployeePassword, MasterData
from django.contrib.auth.hashers import make_password
import base64
from calendar import monthrange
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
import json
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from collections import defaultdict
import urllib.parse
from functools import wraps
import calendar
import io
from django.urls import reverse
from django.core.cache import cache 
import random
from django.core.mail import send_mail
from django.conf import settings 

from decimal import Decimal, ROUND_HALF_UP

def live_punch_dashboard(request):
    now = timezone.now()
    last_10_minutes = now - timedelta(minutes=10)

    punches = DeviceLogs122025.objects.filter(
        LogDate__gte=last_10_minutes
    ).order_by('-LogDate')[:1000]

    employees = {
        e.EmployeeCode: e.EmployeeName
        for e in Employees.objects.all()
    }

    data = []
    for p in punches:
        data.append({
            "user_id": p.UserId,
            "employee_name": employees.get(p.UserId, "Unknown"),
            "time": p.LogDate,
            "direction": p.Direction or p.AttDirection,
            "device": p.DeviceId
        })

    return render(request, "myapp/logs.html", {
        "punches": data,
        "now": now
    })




def monthly_attendance(request):

    # Determine period: 26 to 25
    today = date.today()
    
    # Get filters from request
    selected_month = request.GET.get("month")
    selected_year = request.GET.get("year")
    
    if selected_month and selected_year:
        try:
            m = int(selected_month)
            y = int(selected_year)
            # The billing cycle for month M is 26th of (M-1) to 25th of M
            end_date = date(y, m, 25)
            # Calculate start date (26th of previous month)
            if m == 1:
                start_date = date(y - 1, 12, 26)
            else:
                start_date = date(y, m - 1, 26)
        except ValueError:
            # Fallback to current cycle if invalid
            if today.day >= 26:
                start_date = date(today.year, today.month, 26)
                end_date = (date(today.year, today.month, 25) + timedelta(days=30))
            else:
                prev_month = today.month - 1 or 12
                year = today.year if today.month != 1 else today.year - 1
                start_date = date(year, prev_month, 26)
                end_date = date(today.year, today.month, 25)
    else:
        # Default logic
        if today.day >= 26:
            start_date = date(today.year, today.month, 26)
            end_date = (date(today.year, today.month, 25) + timedelta(days=30))
        else:
            prev_month = today.month - 1 or 12
            year = today.year if today.month != 1 else today.year - 1
            start_date = date(year, prev_month, 26)
            end_date = date(today.year, today.month, 25)
            
    # Normalize month/year for context
    m_val = end_date.month
    y_val = end_date.year


    emp_code_filter = request.GET.get("emp_code")
    dept_filter = request.GET.get("department")

    sql = """
    SELECT
        dl.LogDate AS log_datetime,
        CAST(dl.LogDate AS DATE) AS log_day,
        dl.UserId,
        e.EmployeeName,
        d.DepartmentFName AS Department
    FROM (
        SELECT * FROM dbo.DeviceLogs_3_2026
        UNION ALL
        SELECT * FROM dbo.DeviceLogs_2_2026
        UNION ALL
        SELECT * FROM dbo.DeviceLogs_1_2026
        UNION ALL
        SELECT * FROM dbo.DeviceLogs_12_2025
    ) dl
    LEFT JOIN dbo.Employees e ON e.EmployeeCode = dl.UserId
    LEFT JOIN dbo.EmployeeDepartments ed ON ed.EmployeeId = e.EmployeeId
    LEFT JOIN dbo.Departments d ON d.DepartmentId = e.DepartmentId
    WHERE CAST(dl.LogDate AS DATE) BETWEEN %s AND %s
    """

    params = [start_date, end_date]

    if emp_code_filter:
        sql += " AND dl.UserId = %s"
        params.append(emp_code_filter)
    if dept_filter:
        sql += " AND d.DepartmentFName = %s"
        params.append(dept_filter)

    sql += " ORDER BY dl.UserId, log_day, log_datetime"

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    # Create day list for the period
    day_list = []
    current = start_date
    while current <= end_date:
        day_list.append(current)
        current += timedelta(days=1)

    # ----------------------------
    # Attendance + hours tracking
    # ----------------------------
    attendance = defaultdict(lambda: {
        "name": "",
        "department": "",
        "days": {},
        "present_count": 0,
        "total_seconds": 0
    })

    daily_punches = defaultdict(lambda: defaultdict(list))

    for log_datetime, log_day, user_id, emp_name, dept_name in rows:
        attendance[user_id]["name"] = emp_name
        attendance[user_id]["department"] = dept_name or "—"
        daily_punches[user_id][log_day].append(log_datetime)

    # Fetch Holidays and Weekly Offs
    company_hols = set()
    with connection.cursor() as cursor:
        cursor.execute("SELECT Date FROM CompanyHolidayWeekOff WHERE Date BETWEEN %s AND %s", [start_date, end_date])
        company_hols = {r[0] for r in cursor.fetchall()}

    emp_ids = list(attendance.keys())
    emp_hols = defaultdict(set)
    emp_leaves = defaultdict(dict)
    if emp_ids:
        placeholders = ",".join(["%s"] * len(emp_ids))
        with connection.cursor() as cursor:
            # Employee-specific holidays/weekly offs
            cursor.execute(f"SELECT EmployeeCode, Date FROM EmployeeHolidayWeekOff WHERE EmployeeCode IN ({placeholders}) AND Date BETWEEN %s AND %s", [*emp_ids, start_date, end_date])
            for ec, dt in cursor.fetchall():
                emp_hols[ec].add(dt)
            
            # Approved leaves
            cursor.execute(f"SELECT EmployeeCode, FromDate, ToDate, LeaveType FROM LeaveRequests WHERE EmployeeCode IN ({placeholders}) AND Status='APPROVED' AND LeaveType!='Permission'", emp_ids)
            for ec, f, t, lt in cursor.fetchall():
                curr = f.date() if isinstance(f, datetime) else f
                last = t.date() if isinstance(t, datetime) else t
                # "Casual Leave","Sick Leave","Optional Leave" -> "AL", others -> "L"
                status_char = "AL" if lt in ["Casual Leave", "Sick Leave", "Optional Leave"] else "L"
                while curr <= last:
                    if start_date <= curr <= end_date:
                        emp_leaves[ec][curr] = status_char
                    curr += timedelta(days=1)

    # Calculate present count & working hours
    for user_id in attendance.keys():
        info = attendance[user_id]
        rest_days = company_hols.union(emp_hols[user_id])
        leaves = emp_leaves[user_id]
        punches = daily_punches[user_id]
        
        day_statuses = {}
        for day in day_list:
            if day in punches:
                day_statuses[day] = "P"
            elif day in leaves:
                day_statuses[day] = leaves[day] # "AL" or "L"
            elif day in rest_days:
                day_statuses[day] = "H"
            else:
                day_statuses[day] = "A"
                
        # Apply Sandwich Logic (Holidays between two absences become Absences)
        for day in day_list:
            if day_statuses[day] == "H":
                # Find nearest working day before
                prev_wd = day - timedelta(days=1)
                while prev_wd >= start_date and prev_wd in rest_days:
                    prev_wd -= timedelta(days=1)
                
                # Find nearest working day after
                next_wd = day + timedelta(days=1)
                while next_wd <= end_date and next_wd in rest_days:
                    next_wd += timedelta(days=1)
                
                # Assume present if outside range
                prev_status = day_statuses.get(prev_wd, "P") if prev_wd >= start_date else "P"
                next_status = day_statuses.get(next_wd, "P") if next_wd <= end_date else "P"
                
                if prev_status == "A" and next_status == "A":
                    day_statuses[day] = "A"
                    
        info["days"] = day_statuses
        # Present, Approved Leave ("AL"), and paid Holidays all count towards "present_count" 
        # "L" (other leaves) counts as absent.
        info["present_count"] = sum(1 for s in day_statuses.values() if s in ["P"])
        info["pay_count"] = sum(1 for s in day_statuses.values() if s in ["P", "AL", "H"])
        
        # Calculate Pending Leave Balance (Casual + Sick + Optional)
        balances = get_current_balances(user_id, end_date)
        info["leave_balance"] = sum(balances.values()) if balances else 0

        for day, p_list in punches.items():
            p_list.sort()
            if len(p_list) >= 2:
                info["total_seconds"] += (p_list[-1] - p_list[0]).total_seconds()

    # Prepare table
    table_data = []

    for user_id, info in attendance.items():
        row = {
            "emp_code": user_id,
            "emp_name": info["name"],
            "department": info["department"],
            "days": [info["days"].get(day, "A") for day in day_list],
            "present_count": info["present_count"], 
            "pay_count": info["pay_count"],
            "leave_balance": info.get("leave_balance", 0),
            "working_hours": round(info["total_seconds"] / 3600, 2)
        }
        table_data.append(row)

    return render(request, "myapp/monthly_attendance.html", {
        "table_data": table_data,
        "day_list": day_list,
        "start_date": start_date,
        "end_date": end_date,
        "selected_month": m_val,
        "selected_year": y_val,
        "years": range(2024, today.year + 2),
        "months_range": range(1, 13),
        "dept_filter": dept_filter,
        "emp_code_filter": emp_code_filter,
    })
# views.py
from django.shortcuts import render, redirect
from django.db import connection

from django.shortcuts import render, redirect
from django.db import connection
from django.contrib.auth.hashers import check_password
from functools import wraps
from myapp.models import EmployeePassword

def emp_login(request):
    error = None

    if request.method == "POST":
        emp_code = request.POST.get("emp_code")
        password = request.POST.get("password")

        sql = """
        SELECT e.EmployeeId, e.EmployeeName, e.AllocatePosition, e.EmployeePhoto
        FROM dbo.Employees e
        WHERE e.EmployeeCode = %s

        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [emp_code])
            row = cursor.fetchone()

        if not row:
            error = "Invalid Employee Code"
        else:
            emp_id, emp_name, allow_name, photo_bin = row

            pwd_obj = EmployeePassword.objects.filter(Employee_id=emp_code).first()
            if not pwd_obj or not check_password(password, pwd_obj.PasswordHash):
                error = "Invalid Password"
            else:
                # Process Photo
                photo_b64 = None
                if photo_bin:
                    try:
                        photo_bytes = photo_bin
                        if isinstance(photo_bytes, memoryview):
                            photo_bytes = photo_bytes.tobytes()
                        photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
                    except Exception:
                        photo_b64 = None

                # Save session
                request.session["emp_id"] = emp_id
                request.session["emp_code"] = emp_code
                request.session["emp_name"] = emp_name
                request.session["allocate_position"] = allow_name
                request.session["emp_photo"] = photo_b64


                return redirect("dashboard")  # default dashboard

    return render(request, "myapp/login.html", {"error": error})

def department_required(allowed_departments):
    """
    Decorator to restrict views by department name.
    allowed_departments: list or tuple of department names
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            dept = request.session.get("allocate_position")
            if not dept or dept not in allowed_departments:
                # Redirect to login or error page
                return redirect("login")  # or a "permission denied" page
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def security_showcase(request):
    """
    Showcase Role-based security for internal and external users.
    Determines role based on session and AllocatePosition.
    """
    emp_code = request.session.get("emp_code")
    role = request.session.get("allocate_position", "EXTERNAL")
    
    # Determine user type
    is_super_admin = role in ["IT", "Management", "HR"] # Admin-level positions
    is_internal = emp_code is not None
    is_external = not is_internal
    
    # Mock data for demonstration
    security_features = [
        {"feature": "Leave Management", "access": "FULL" if is_super_admin else "SELF" if is_internal else "NONE"},
        {"feature": "Asset Requests", "access": "APPROVE" if is_super_admin else "REQUEST" if is_internal else "NONE"},
        {"feature": "User Profile Editing", "access": "ANY" if is_super_admin else "OWN" if is_internal else "NONE"},
        {"feature": "External Registration", "access": "ENABLED" if is_external or is_super_admin else "N/A"},
    ]
    
    context = {
        "current_role": role,
        "is_super_admin": is_super_admin,
        "is_internal": is_internal,
        "is_external": is_external,
        "security_features": security_features,
        "emp_name": request.session.get("emp_name", "Guest User"),
    }
    
    return render(request, "myapp/role_security_showcase.html", context)
def emp_logout(request):
    request.session.flush()
    return redirect("emp_login")

def change_password(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    error = None
    success = None
    emp_code = str(request.session["emp_code"])

    if request.method == "POST":
        current = request.POST.get("current_password")
        new = request.POST.get("new_password")
        confirm = request.POST.get("confirm_password")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT PasswordHash
                FROM dbo.EmployeePasswords
                WHERE LTRIM(RTRIM(UPPER(EmployeeCode)))
                      = LTRIM(RTRIM(UPPER(%s)))
                """,
                [emp_code]
            )
            row = cursor.fetchone()

        if not row:
            error = "Employee not found"

        elif not check_password(current, row[0]):
            error = "Current password incorrect"

        elif new != confirm:
            error = "Passwords do not match"

        else:
            new_hash = make_password(new)

            EmployeePassword.objects.update_or_create(
                Employee_id=emp_code,
                defaults={'PasswordHash': new_hash}
            )

            success = "Password changed successfully"
    print("SESSION EMP CODE:", repr(request.session["emp_code"]))

    return render(request, "myapp/change_password.html", {
        "error": error,
        "success": success
    })

COMMON_OTP_EMAIL = "dineshit@medlearnvision.com"  # or samuel.it@medlearnvision.co.in

def send_otp_email(emp_code, otp):
    subject = 'MedLearn OTP Verification'
    message = f"Employee Code: {emp_code}\nOTP: {otp}\nThis OTP is valid for 10 minutes."

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,  # <-- use settings
        [COMMON_OTP_EMAIL],
        fail_silently=False
    )

# -----------------------------
# Forgot Password View
# -----------------------------
def forgot_password(request):
    error = None
    
    if request.method == "POST":
        emp_code = request.POST.get("emp_code").strip()

        # Check if employee exists
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EmployeeId
                FROM dbo.Employees
                WHERE EmployeeCode = %s
            """, [emp_code])
            row = cursor.fetchone()
        
        if not row:
            error = "Employee not found"
        else:
            # Generate 6-digit OTP
            otp = str(random.randint(100000, 999999))
            
            # Store OTP in cache for 10 minutes keyed by employee code
            cache.set(f"pwd_reset_{emp_code}", otp, timeout=600)
            
            # Send OTP to common email
            send_otp_email(emp_code, otp)
            
            # Store employee code in session for verification step
            request.session["emp_code_reset"] = emp_code
            return redirect("verify_code")
    
    return render(request, "myapp/forgot_password.html", {"error": error})

# -----------------------------
# Verify Code & Reset Password
# -----------------------------
def verify_code(request):
    error = None
    emp_code = request.session.get("emp_code_reset")
    
    if not emp_code:
        return redirect("forgot_password")
    
    if request.method == "POST":
        code_entered = request.POST.get("verification_code").strip()
        new_password = request.POST.get("new_password")
        confirm_password = request.POST.get("confirm_password")
        
        # Check OTP from cache
        stored_code = cache.get(f"pwd_reset_{emp_code}")
        
        if not stored_code:
            error = "OTP expired. Please try again."
        elif code_entered != stored_code:
            error = "Incorrect OTP."
        elif new_password != confirm_password:
            error = "Passwords do not match."
        else:
            # Update password in database (UPSERT logic)
            new_hash = make_password(new_password)
            EmployeePassword.objects.update_or_create(
                Employee_id=emp_code,
                defaults={'PasswordHash': new_hash}
            )
            
            # Clear cache and session
            cache.delete(f"pwd_reset_{emp_code}")
            request.session.pop("emp_code_reset", None)
            return redirect("emp_login")
    
    return render(request, "myapp/verify_code.html", {"error": error, "emp_code": emp_code})
def is_trainee(emp_code):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EmployementType
            FROM Employees
            WHERE EmployeeCode = %s
        """, [emp_code])
        row = cursor.fetchone()
        return row and row[0] == 'Trainee'


MONTHLY_LEAVE_POLICY = {
    "Grace Time": 3,
    "Permission": 4,
}
def get_used_permission_hours(emp_code, start_date, end_date):
    """Return total permission hours used in a custom month range"""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COALESCE(SUM(ISNULL(PermissionHours,0)),0)
            FROM LeaveRequests
            WHERE EmployeeCode=%s
              AND LeaveType='Permission'
              AND Status IN ('PENDING','APPROVED')
              AND FromDate BETWEEN %s AND %s
        """, [emp_code, start_date, end_date])
        return cursor.fetchone()[0] or 0


def ensure_monthly_balance(emp_code, today):
    year = today.year
    month = today.month

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 1 FROM EmployeeMonthlyLeave
            WHERE EmployeeCode=%s AND Year=%s AND Month=%s
        """, [emp_code, year, month])

        if cursor.fetchone():
            return  # 🔒 already created

        cursor.execute("""
            INSERT INTO EmployeeMonthlyLeave
            (EmployeeCode, Year, Month, GraceTime, Permission)
            VALUES (%s,%s,%s,%s,%s)
        """, [
            emp_code, year, month,
            MONTHLY_LEAVE_POLICY["Grace Time"],
            MONTHLY_LEAVE_POLICY["Permission"]
        ])

def get_used_monthly_leaves(emp_code, leave_type, start_date, end_date):
    """Return total monthly leaves used in a custom month range"""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*)
            FROM LeaveRequests
            WHERE EmployeeCode=%s
              AND LeaveType=%s
              AND Status IN ('PENDING','APPROVED')
              AND FromDate BETWEEN %s AND %s
        """, [emp_code, leave_type, start_date, end_date])
        return cursor.fetchone()[0]

def get_current_monthly_balances(emp_code, start_date, end_date):
    """Return remaining monthly balances for Grace Time and Permission"""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT GraceTime, PermissionHours
            FROM EmployeeMonthlyLeave
            WHERE EmployeeCode=%s
              AND Year=%s
              AND Month=%s
        """, [emp_code, end_date.year, end_date.month])

        row = cursor.fetchone()

    if not row:
        return {}

    used_permission = get_used_permission_hours(emp_code, start_date, end_date)
    used_grace = get_used_monthly_leaves(emp_code, "Grace Time", start_date, end_date)

    grace_time = row[0] if row[0] is not None else MONTHLY_LEAVE_POLICY["Grace Time"]
    permission_hours = row[1] if row[1] is not None else MONTHLY_LEAVE_POLICY["Permission"]

    return {
        "Grace Time": max(grace_time - used_grace, 0),
        "Permission": max(permission_hours - used_permission, 0)
    }



# -----------------------------
# Leave policy
# -----------------------------
LEAVE_POLICY = {
    "Casual Leave": {
        (1, 2, 3): 3,
        (4, 5, 6): 3,
        (7, 8, 9): 3,
        (10, 11, 12): 3,
    },
    "Sick Leave": {
        (1, 2, 3): 1,
        (4, 5, 6): 1,
        (7, 8, 9): 1,
        (10, 11, 12): 1,
    },
    "Optional Leave": {
        (4, 5, 6): 1,
        (10, 11, 12): 1,
    }
}

# -----------------------------
# Utilities
# -----------------------------
def get_cycle_year_month(ref_date: date):
    """Returns the payroll cycle year and month (26th prev -> 25th current)"""
    if ref_date.day >= 26:
        # 26-Dec-2025 belongs to Jan 2026 cycle
        m = ref_date.month + 1
        y = ref_date.year
        if m > 12:
            m = 1
            y += 1
        return y, m
    return ref_date.year, ref_date.month

def get_cycle_quarter(ref_date: date):
    """Returns the payroll cycle quarter (Q1=Jan,Feb,Mar cycle)"""
    _, m = get_cycle_year_month(ref_date)
    if m <= 3: return 1
    if m <= 6: return 2
    if m <= 9: return 3
    return 4

def get_cycle_quarter_range(ref_date: date):
    """Returns (start_date, end_date) for the payroll cycle quarter"""
    year, month = get_cycle_year_month(ref_date)
    
    if month <= 3:
        # Q1: Jan, Feb, Mar cycles
        # Start = Dec 26 of Prev Year
        # End = Mar 25 of Curr Year
        start = date(year - 1, 12, 26)
        end = date(year, 3, 25)
    elif month <= 6:
        # Q2: Apr, May, Jun cycles
        start = date(year, 3, 26)
        end = date(year, 6, 25)
    elif month <= 9:
        # Q3: Jul, Aug, Sep cycles
        start = date(year, 6, 26)
        end = date(year, 9, 25)
    else:
        # Q4: Oct, Nov, Dec cycles
        start = date(year, 9, 26)
        end = date(year, 12, 25)
        
    return start, end
def ensure_quarter_balance(emp_code, today):
    if is_trainee(emp_code):
        return
    year, _ = get_cycle_year_month(today)
    quarter = get_cycle_quarter(today)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 1 FROM EmployeeQuarterLeave
            WHERE EmployeeCode=%s AND Year=%s AND Quarter=%s
        """, [emp_code, year, quarter])

        exists = cursor.fetchone()

        # 🔒 If already created → DO NOTHING
        if exists:
            return

        # ✅ Create balances ONCE per quarter
        _, month = get_cycle_year_month(today)
        balances = {
            "Casual Leave": get_quarter_limit("Casual Leave", month) or 0,
            "Sick Leave": get_quarter_limit("Sick Leave", month) or 0,
            "Optional Leave": get_quarter_limit("Optional Leave", month) or 0,
        }

        cursor.execute("""
            INSERT INTO EmployeeQuarterLeave
            (EmployeeCode, Year, Quarter, CasualLeave, SickLeave, OptionalLeave)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, [
            emp_code, year, quarter,
            balances["Casual Leave"],
            balances["Sick Leave"],
            balances["Optional Leave"]
        ])
def get_current_balances(emp_code, today):
    year, _ = get_cycle_year_month(today)
    quarter = get_cycle_quarter(today)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT CasualLeave, SickLeave, OptionalLeave
            FROM EmployeeQuarterLeave
            WHERE EmployeeCode=%s AND Year=%s AND Quarter=%s
        """, [emp_code, year, quarter])

        row = cursor.fetchone()

    if not row:
        return {}

    balances = {
        "Casual Leave": row[0],
        "Sick Leave": row[1],
        "Optional Leave": row[2],
    }

    for lt in balances.keys():
        used = get_used_leaves_quarter(emp_code, lt, today)
        balances[lt] = max(balances[lt] - used, 0)

    return balances

def get_quarter_limit(leave_type, month):
    rules = LEAVE_POLICY.get(leave_type)
    if not rules:
        return None

    for months, limit in rules.items():
        if month in months:
            return limit
    return None



def calculate_leave_days(from_date, to_date):
    if isinstance(from_date, str):
        from_date = date.fromisoformat(from_date)
    if isinstance(to_date, str):
        to_date = date.fromisoformat(to_date)
    return (to_date - from_date).days + 1

# -----------------------------
# Leave usage
# -----------------------------
def get_used_leaves_quarter(emp_code, leave_type, ref_date=None):
    """
    Returns total APPROVED leave days used in the current quarter
    """
    if not ref_date:
        ref_date = date.today()

    quarter_start, quarter_end = get_cycle_quarter_range(ref_date)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode = %s
              AND LeaveType = %s
              AND Status = 'APPROVED'
              AND (
                    (FromDate BETWEEN %s AND %s)
                 OR (ToDate BETWEEN %s AND %s)
                 OR (FromDate <= %s AND ToDate >= %s)
              )
        """, [
            emp_code, leave_type,
            quarter_start, quarter_end,
            quarter_start, quarter_end,
            quarter_start, quarter_end
        ])
        rows = cursor.fetchall()

    total_days = 0
    for from_dt, to_dt in rows:
        from_dt = from_dt if isinstance(from_dt, date) else from_dt.date()
        to_dt = to_dt if isinstance(to_dt, date) else to_dt.date()

        start = max(from_dt, quarter_start)
        end = min(to_dt, quarter_end)
        total_days += (end - start).days + 1

    return total_days



def get_approved_quarter_leaves(emp_code, ref_date=None):
    """
    Returns approved leave days per type for this quarter
    """
    leave_types = ["Casual Leave", "Sick Leave", "Optional Leave"]
    approved = {}
    for lt in leave_types:
        approved[lt] = get_used_leaves_quarter(emp_code, lt, ref_date)
    return approved


def get_used_leaves_quarter(emp_code, leave_type, ref_date=None):
    ref_date = ref_date or date.today()
    q_start, q_end = get_cycle_quarter_range(ref_date)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode = %s
              AND LeaveType = %s
              AND Status = 'APPROVED'
              AND (
                    (FromDate BETWEEN %s AND %s)
                 OR (ToDate BETWEEN %s AND %s)
                 OR (FromDate <= %s AND ToDate >= %s)
              )
        """, [
            emp_code, leave_type,
            q_start, q_end,
            q_start, q_end,
            q_start, q_end
        ])
        rows = cursor.fetchall()

    total = 0
    for f, t in rows:
        f = f if isinstance(f, date) else f.date()
        t = t if isinstance(t, date) else t.date()
        total += (min(t, q_end) - max(f, q_start)).days + 1

    return total



def get_quarter_leave_counts(emp_code, ref_date=None):
    ref_date = ref_date or date.today()
    q_start, q_end = get_cycle_quarter_range(ref_date)

    leave_types = ["Casual Leave", "Sick Leave", "Optional Leave"]
    counts = {}

    with connection.cursor() as cursor:
        for lt in leave_types:
            cursor.execute("""
                SELECT Status,
                       SUM(DATEDIFF(DAY,
                           CASE WHEN FromDate < %s THEN %s ELSE FromDate END,
                           CASE WHEN ToDate > %s THEN %s ELSE ToDate END
                       ) + 1)
                FROM LeaveRequests
                WHERE EmployeeCode = %s
                  AND LeaveType = %s
                  AND Status IN ('APPROVED','REJECTED')
                  AND (
                        (FromDate BETWEEN %s AND %s)
                     OR (ToDate BETWEEN %s AND %s)
                     OR (FromDate <= %s AND ToDate >= %s)
                  )
                GROUP BY Status
            """, [
                q_start, q_start,
                q_end, q_end,
                emp_code, lt,
                q_start, q_end,
                q_start, q_end,
                q_start, q_end
            ])

            counts[lt] = {"APPROVED": 0, "REJECTED": 0}
            for status, days in cursor.fetchall():
                counts[lt][status] = days or 0

    return counts


def get_monthly_used_leave_types(emp_code, start_date, end_date):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode=%s
              AND FromDate BETWEEN %s AND %s
              AND Status IN ('PENDING','APPROVED')
        """, [emp_code, start_date, end_date])

        return [r[0] for r in cursor.fetchall()]


# -----------------------------
# Delete leave
# -----------------------------
def delete_leave_request(request, leave_id):
    emp_code = request.session.get("emp_code")
    if not emp_code:
        return redirect("emp_login")
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM LeaveRequests
                WHERE LeaveId = %s
                  AND EmployeeCode = %s
                  AND Status = 'PENDING'
            """, [leave_id, emp_code])
            if cursor.rowcount == 0:
                messages.error(request, "Cannot delete this leave request")
            else:
                messages.success(request, "Leave request deleted successfully")
        connection.commit()
    except Exception as e:
        messages.error(request, str(e))
    return redirect("leave_request")



def get_custom_month_range(year, month):
    """Return start_date and end_date for a custom leave month (26th prev → 25th current)"""
    if month == 1:
        start_date = date(year - 1, 12, 26)
    else:
        start_date = date(year, month - 1, 26)
    end_date = date(year, month, 25)
    return start_date, end_date







def hr_attendance(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    logged_in_emp_code = request.session["emp_code"]
    emp_code_filter = request.GET.get("emp_code")

    # ===============================
    # Handle start and end dates
    # ===============================
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    else:
        start_date = datetime.today().date()

    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    else:
        end_date = start_date

    # Strings for SQL
    start_date_sql = start_date.strftime("%Y-%m-%d")
    end_date_sql = end_date.strftime("%Y-%m-%d")

    selected_date = start_date  # for shift / punch calculations

    # ===============================
    # SQL Query: All Employees in Hierarchy
    # ===============================
    hierarchy_sql = """
    WITH TeamHierarchy AS (
        SELECT EmployeeCode, EmployeeName, Team
        FROM dbo.Employees

        UNION ALL

        SELECT e.EmployeeCode, e.EmployeeName, e.Team
        FROM dbo.Employees e
        INNER JOIN TeamHierarchy th ON e.Team = th.EmployeeCode
    )
    SELECT DISTINCT EmployeeCode, EmployeeName, Team
    FROM TeamHierarchy
    WHERE EmployeeName NOT LIKE %s
    """
    with connection.cursor() as cursor:
        cursor.execute(hierarchy_sql, ["del_%"])
        hierarchy_rows = cursor.fetchall()
    
    all_team_members = {row[0]: {"name": row[1], "team_code": row[2]} for row in hierarchy_rows}
    all_team_codes = list(all_team_members.keys())

    if not all_team_codes:
        return render(request, "myapp/hr_attendance.html", {
            "data": [],
            "pair_range": range(0),
            "start_date": start_date,
            "end_date": end_date,
        })

    # ===============================
    # SQL Query: Device Logs
    # ===============================
    sql = f"""
    SELECT 
        UserId,
        CAST(LogDate AS DATE) AS log_day,
        LogDate
    FROM (
        SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
        UNION ALL
        SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
        UNION ALL
        SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
        UNION ALL
        SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
    ) dl 
    WHERE UserId IN ({",".join(["%s"] * len(all_team_codes))})
    """

    params = [
        start_date_sql, end_date_sql,
        start_date_sql, end_date_sql,
        start_date_sql, end_date_sql,
        start_date_sql, end_date_sql,
        *all_team_codes
    ]

    if emp_code_filter:
        sql += " AND UserId = %s"
        params.append(emp_code_filter)

    sql += " ORDER BY log_day, UserId, dl.LogDate"

    # ===============================
    # Fetch logs from DB
    # ===============================
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    # ===============================
    # Fetch Team Name Map
    # ===============================
    team_names_map = {}
    distinct_teams = list({info["team_code"] for info in all_team_members.values() if info["team_code"]})
    if distinct_teams:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(distinct_teams))
            cursor.execute(f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode IN ({placeholders})", distinct_teams)
            team_names_map = dict(cursor.fetchall())

    # ===============================
    # Fetch Leaves for the range
    # ===============================
    leave_map = defaultdict(set)
    if all_team_codes:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(all_team_codes))
            cursor.execute(f"""
                SELECT EmployeeCode, FromDate, ToDate
                FROM LeaveRequests
                WHERE Status = 'APPROVED'
                AND EmployeeCode IN ({placeholders})
                AND (
                    (FromDate BETWEEN %s AND %s)
                    OR (ToDate BETWEEN %s AND %s)
                    OR (FromDate <= %s AND ToDate >= %s)
                )
            """, [*all_team_codes, start_date, end_date, start_date, end_date, start_date, end_date])
            for e_code, f_date, t_date in cursor.fetchall():
                curr = f_date.date() if isinstance(f_date, datetime) else f_date
                last = t_date.date() if isinstance(t_date, datetime) else t_date
                while curr <= last:
                    if start_date <= curr <= end_date:
                        leave_map[e_code].add(curr)
                    curr += timedelta(days=1)

    # ===============================
    # Fetch Holidays for the range
    # ===============================
    company_hols = set()
    emp_hols = defaultdict(set)
    if all_team_codes:
        with connection.cursor() as cursor:
            # Company-wide
            cursor.execute("SELECT Date FROM CompanyHolidayWeekOff WHERE Date BETWEEN %s AND %s", [start_date, end_date])
            company_hols = {r[0] for r in cursor.fetchall()}

            # Employee-specific
            placeholders = ",".join(["%s"] * len(all_team_codes))
            cursor.execute(f"SELECT EmployeeCode, Date FROM EmployeeHolidayWeekOff WHERE EmployeeCode IN ({placeholders}) AND Date BETWEEN %s AND %s", [*all_team_codes, start_date, end_date])
            for ec, dt in cursor.fetchall():
                emp_hols[ec].add(dt)

    # ===============================
    # Group logs and pre-populate all days
    # ===============================
    employees = defaultdict(dict)
    team_emp_set = set(all_team_codes)
    punch_map = {}

    # Pre-populate every day for every employee
    curr_d = start_date
    while curr_d <= end_date:
        for e_code, info in all_team_members.items():
            employees[curr_d][e_code] = {
                "name": info["name"],
                "team": team_names_map.get(info["team_code"], info["team_code"] or ""),
                "logs": []
            }
        curr_d += timedelta(days=1)

    for emp_code, log_day_v, log_time in (rows or []):
        if not log_day_v: continue
        log_day_cast: date = log_day_v.date() if isinstance(log_day_v, datetime) else log_day_v
        if log_day_cast not in employees: continue 
        
        emp_inf_ptr = employees[log_day_cast].get(emp_code)
        if emp_inf_ptr and log_time:
            cast(list, emp_inf_ptr["logs"]).append(log_time)
            if (log_day_cast, emp_code) not in punch_map:
                punch_map[(log_day_cast, emp_code)] = log_time

    # ===============================
    # Fetch employee shift start times
    # ===============================
    shift_start_map = {}
    if team_emp_set:
        emp_list = list(team_emp_set)
        placeholders = ",".join(["%s"] * len(emp_list))
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT esa.EmployeeCode, s.BeginTime
                FROM EmployeeShiftAllocation esa
                JOIN Shifts s ON s.ShiftId = esa.ShiftId
                WHERE %s BETWEEN esa.FromDate AND ISNULL(esa.ToDate, '9999-12-31')
                AND esa.EmployeeCode IN ({placeholders})
            """, [selected_date, *emp_list])
            for emp_code, begin_time in cursor.fetchall():
                if begin_time:
                    shift_start_map[emp_code] = datetime.strptime(begin_time, "%H:%M").time()

    # ===============================
    # Calculate punch status (on time / late) per day
    # ===============================
    GRACE_MINUTES = 1
    # We will compute status on the fly while preparing data or store it in a map
    # A map indexed by (date, emp_code) is best.
    daily_status_map = {}

    for log_day_raw, emp_dict in employees.items():
        log_day = log_day_raw.date() if isinstance(log_day_raw, datetime) else log_day_raw
        for emp_code in emp_dict:
            punch = punch_map.get((log_day, emp_code))
            shift_start_time = shift_start_map.get(emp_code, time(9, 30))
            shift_start_dt = datetime.combine(log_day, shift_start_time)

            # ===============================
            # Determine Daily Status
            # ===============================
            emp_rest_days = company_hols.union(emp_hols[emp_code])
            
            if punch:
                if punch > shift_start_dt + timedelta(minutes=GRACE_MINUTES):
                    daily_status_map[(log_day, emp_code)] = "LATE_IN"
                else:
                    daily_status_map[(log_day, emp_code)] = "ON_TIME"
            elif log_day in leave_map.get(emp_code, set()):
                daily_status_map[(log_day, emp_code)] = "ON_LEAVE"
            elif log_day in emp_rest_days:
                # -------------------------------
                # Apply Sandwich Logic
                # -------------------------------
                prev_wd = log_day - timedelta(days=1)
                while prev_wd >= start_date and prev_wd in emp_rest_days:
                    prev_wd -= timedelta(days=1)
                
                next_wd = log_day + timedelta(days=1)
                while next_wd <= end_date and next_wd in emp_rest_days:
                    next_wd += timedelta(days=1)
                
                # Simplified on-the-fly check for neighbors:
                def is_absent(d, ec):
                    if not isinstance(d, (date, datetime)): return False
                    if d < start_date or d > end_date: return False
                    return (d, ec) not in punch_map and d not in leave_map.get(ec, set()) and d not in emp_rest_days

                if is_absent(prev_wd, emp_code) and is_absent(next_wd, emp_code):
                    daily_status_map[(log_day, emp_code)] = "ABSENT" # Sandwiched
                else:
                    daily_status_map[(log_day, emp_code)] = "HOLIDAY"
            else:
                daily_status_map[(log_day, emp_code)] = "NOT_YET_IN"

    # ===============================
    # Prepare data for frontend
    # ===============================
    data = []
    max_pairs = 0

    for log_day, emp_dict in employees.items():
        for emp_code, info in emp_dict.items():
            logs = sorted(info["logs"])
            pairs = [(logs[i], logs[i+1] if i+1 < len(logs) else None) for i in range(0, len(logs), 2)]
            max_pairs = max(max_pairs, len(pairs))

            working_seconds = sum(
                (logs[i+1] - logs[i]).total_seconds() 
                for i in range(len(logs)-1) if i % 2 == 0
            )
            break_seconds = sum(
                (logs[i+1] - logs[i]).total_seconds() 
                for i in range(len(logs)-1) if i % 2 != 0
            )

            def fmt(sec):
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                return f"{h:02d}:{m:02d}"

            last_out_time = logs[-1] if logs else None

            data.append({
                "date": log_day,
                "emp_code": emp_code,
                "emp_name": info["name"],
                "team": info.get("team", ""),
                "pairs": pairs,
                "working_hours": fmt(working_seconds),
                "break_hours": fmt(break_seconds),
                "last_out": last_out_time,
                "punch_status": daily_status_map.get((log_day, emp_code))
            })

    # Pad pairs for table alignment
    for emp in data:
        while len(emp["pairs"]) < max_pairs:
            emp["pairs"].append((None, None))

    return render(request, "myapp/hr_attendance.html", {
        "data": data,
        "pair_range": range(max_pairs),
        "start_date": start_date,
        "end_date": end_date,
    })

def today_attendance(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    logged_in_emp_code = request.session["emp_code"]
    emp_code_filter = request.GET.get("emp_code")

    # ===============================
    # Handle start and end dates
    # ===============================
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    else:
        start_date = datetime.today().date()

    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    else:
        end_date = start_date

    # Strings for SQL
    start_date_sql = start_date.strftime("%Y-%m-%d")
    end_date_sql = end_date.strftime("%Y-%m-%d")

    selected_date = start_date  # for shift / punch calculations

    # ===============================
    # SQL Query: All Employees in Hierarchy
    # ===============================
    hierarchy_sql = """
    WITH TeamHierarchy AS (
        SELECT EmployeeCode, EmployeeName, Team
        FROM Employees
        WHERE Team = %s

        UNION ALL

        SELECT e.EmployeeCode, e.EmployeeName, e.Team
        FROM Employees e
        INNER JOIN TeamHierarchy th ON e.Team = th.EmployeeCode
    )
    SELECT DISTINCT EmployeeCode, EmployeeName, Team
    FROM TeamHierarchy
    WHERE EmployeeName NOT LIKE %s
    """
    with connection.cursor() as cursor:
        cursor.execute(hierarchy_sql, [logged_in_emp_code, "del_%"])
        hierarchy_rows = cursor.fetchall()
    
    all_team_members = {row[0]: {"name": row[1], "team_code": row[2]} for row in hierarchy_rows}
    all_team_codes = list(all_team_members.keys())

    if not all_team_codes:
        return render(request, "myapp/today_attendance.html", {
            "data": [],
            "pair_range": range(0),
            "start_date": start_date,
            "end_date": end_date,
        })

    # ===============================
    # SQL Query: Device Logs
    # ===============================
    sql = f"""
    SELECT 
        UserId,
        CAST(LogDate AS DATE) AS log_day,
        LogDate
    FROM (
        SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
        UNION ALL
        SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
        UNION ALL
        SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
        UNION ALL
        SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
        WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
    ) dl 
    WHERE UserId IN ({",".join(["%s"] * len(all_team_codes))})
    """

    params = [
        start_date_sql, end_date_sql,
        start_date_sql, end_date_sql,
        start_date_sql, end_date_sql,
        start_date_sql, end_date_sql,
        *all_team_codes
    ]

    if emp_code_filter:
        sql += " AND UserId = %s"
        params.append(emp_code_filter)

    sql += " ORDER BY log_day, UserId, dl.LogDate"

    # ===============================
    # Fetch logs from DB
    # ===============================
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    # ===============================
    # Fetch Team Name Map
    # ===============================
    team_names_map = {}
    distinct_teams = list({info["team_code"] for info in all_team_members.values() if info["team_code"]})
    if distinct_teams:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(distinct_teams))
            cursor.execute(f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode IN ({placeholders})", distinct_teams)
            team_names_map = dict(cursor.fetchall())

    # ===============================
    # Fetch Leaves for the range
    # ===============================
    leave_map = defaultdict(set)
    with connection.cursor() as cursor:
        placeholders = ",".join(["%s"] * len(all_team_codes))
        cursor.execute(f"""
            SELECT EmployeeCode, FromDate, ToDate
            FROM LeaveRequests
            WHERE Status = 'APPROVED'
            AND EmployeeCode IN ({placeholders})
            AND (
                (FromDate BETWEEN %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [*all_team_codes, start_date, end_date, start_date, end_date, start_date, end_date])
        for e_code, f_date, t_date in cursor.fetchall():
            curr = f_date.date() if isinstance(f_date, datetime) else f_date
            last = t_date.date() if isinstance(t_date, datetime) else t_date
            while curr <= last:
                if start_date <= curr <= end_date:
                    leave_map[e_code].add(curr)
                curr += timedelta(days=1)

    # ===============================
    # Fetch Holidays for the range
    # ===============================
    company_hols = set()
    emp_hols = defaultdict(set)
    if all_team_codes:
        with connection.cursor() as cursor:
            # Company-wide
            cursor.execute("SELECT Date FROM CompanyHolidayWeekOff WHERE Date BETWEEN %s AND %s", [start_date, end_date])
            company_hols = {r[0] for r in cursor.fetchall()}

            # Employee-specific
            placeholders = ",".join(["%s"] * len(all_team_codes))
            cursor.execute(f"SELECT EmployeeCode, Date FROM EmployeeHolidayWeekOff WHERE EmployeeCode IN ({placeholders}) AND Date BETWEEN %s AND %s", [*all_team_codes, start_date, end_date])
            for ec, dt in cursor.fetchall():
                emp_hols[ec].add(dt)

    # ===============================
    # Group logs and pre-populate all days
    # ===============================
    employees = defaultdict(dict)
    team_emp_set = set(all_team_codes)
    punch_map = {}

    # Pre-populate every day for every employee
    curr_d = start_date
    while curr_d <= end_date:
        for e_code, info in all_team_members.items():
            employees[curr_d][e_code] = {
                "name": info["name"],
                "team": team_names_map.get(info["team_code"], info["team_code"] or ""),
                "logs": []
            }
        curr_d += timedelta(days=1)

    for emp_code, log_day_v, log_time in (rows or []):
        if not log_day_v: continue
        log_day_cast: date = log_day_v.date() if isinstance(log_day_v, datetime) else log_day_v
        if log_day_cast not in employees: continue 
        
        emp_inf_ptr = employees[log_day_cast].get(emp_code)
        if emp_inf_ptr and log_time:
            cast(list, emp_inf_ptr["logs"]).append(log_time)
            if (log_day_cast, emp_code) not in punch_map:
                punch_map[(log_day_cast, emp_code)] = log_time

    # ===============================
    # Fetch employee shift start times
    # ===============================
    shift_start_map = {}
    if team_emp_set:
        emp_list = list(team_emp_set)
        placeholders = ",".join(["%s"] * len(emp_list))
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT esa.EmployeeCode, s.BeginTime
                FROM EmployeeShiftAllocation esa
                JOIN Shifts s ON s.ShiftId = esa.ShiftId
                WHERE %s BETWEEN esa.FromDate AND ISNULL(esa.ToDate, '9999-12-31')
                AND esa.EmployeeCode IN ({placeholders})
            """, [selected_date, *emp_list])
            for emp_code, begin_time in cursor.fetchall():
                if begin_time:
                    shift_start_map[emp_code] = datetime.strptime(begin_time, "%H:%M").time()

    # ===============================
    # Calculate punch status (on time / late) per day
    # ===============================
    GRACE_MINUTES = 1
    daily_status_map = {}

    for log_day_raw, emp_dict in employees.items():
        log_day = log_day_raw.date() if isinstance(log_day_raw, datetime) else log_day_raw
        for emp_code in emp_dict:
            punch = punch_map.get((log_day, emp_code))
            shift_start_time = shift_start_map.get(emp_code, time(9, 30))
            shift_start_dt = datetime.combine(log_day, shift_start_time)

            emp_rest_days = company_hols.union(emp_hols[emp_code])

            if punch:
                if punch > shift_start_dt + timedelta(minutes=GRACE_MINUTES):
                    daily_status_map[(log_day, emp_code)] = "LATE_IN"
                else:
                    daily_status_map[(log_day, emp_code)] = "ON_TIME"
            elif log_day in leave_map.get(emp_code, set()):
                daily_status_map[(log_day, emp_code)] = "ON_LEAVE"
            elif log_day in emp_rest_days:
                # -------------------------------
                # Apply Sandwich Logic
                # -------------------------------
                prev_wd = log_day - timedelta(days=1)
                while prev_wd >= start_date and prev_wd in emp_rest_days:
                    prev_wd -= timedelta(days=1)
                
                next_wd = log_day + timedelta(days=1)
                while next_wd <= end_date and next_wd in emp_rest_days:
                    next_wd += timedelta(days=1)
                
                def is_absent(d, ec):
                    if not isinstance(d, (date, datetime)): return False
                    if d < start_date or d > end_date: return False
                    return (d, ec) not in punch_map and d not in leave_map.get(ec, set()) and d not in emp_rest_days

                if is_absent(prev_wd, emp_code) and is_absent(next_wd, emp_code):
                    daily_status_map[(log_day, emp_code)] = "ABSENT" # Sandwiched
                else:
                    daily_status_map[(log_day, emp_code)] = "HOLIDAY"
            else:
                daily_status_map[(log_day, emp_code)] = "NOT_YET_IN"

    # ===============================
    # Prepare data for frontend
    # ===============================
    data = []
    max_pairs = 0

    for log_day, emp_dict in employees.items():
        for emp_code, info in emp_dict.items():
            logs = sorted(info["logs"])
            pairs = [(logs[i], logs[i+1] if i+1 < len(logs) else None) for i in range(0, len(logs), 2)]
            max_pairs = max(max_pairs, len(pairs))

            working_seconds = sum(
                (logs[i+1] - logs[i]).total_seconds() 
                for i in range(len(logs)-1) if i % 2 == 0
            )
            break_seconds = sum(
                (logs[i+1] - logs[i]).total_seconds() 
                for i in range(len(logs)-1) if i % 2 != 0
            )

            def fmt(sec):
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                return f"{h:02d}:{m:02d}"

            last_out_time = logs[-1] if logs else None

            data.append({
                "date": log_day,
                "emp_code": emp_code,
                "emp_name": info["name"],
                "team": info.get("team", ""),
                "pairs": pairs,
                "working_hours": fmt(working_seconds),
                "break_hours": fmt(break_seconds),
                "last_out": last_out_time,
                "punch_status": daily_status_map.get((log_day, emp_code))
            })

    # Pad pairs for table alignment
    for emp in data:
        while len(emp["pairs"]) < max_pairs:
            emp["pairs"].append((None, None))

    return render(request, "myapp/today_attendance.html", {
        "data": data,
        "pair_range": range(max_pairs),
        "start_date": start_date,
        "end_date": end_date,
    })
def shift_allocation(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT ShiftId, ShiftFName FROM Shifts")
        shifts = cursor.fetchall()

        # Fetch employees for the dropdown
        cursor.execute("SELECT EmployeeCode, EmployeeName FROM Employees ORDER BY EmployeeName")
        employees = cursor.fetchall()

        cursor.execute("""
            SELECT a.AllocationId, a.EmployeeCode, e.EmployeeName, e.Team,
                   s.ShiftFName, a.FromDate, a.ToDate
            FROM EmployeeShiftAllocation a
            JOIN Employees e ON e.EmployeeCode = a.EmployeeCode
            JOIN Shifts s ON s.ShiftId = a.ShiftId
            ORDER BY a.FromDate DESC
        """)
        rows = cursor.fetchall()

    # Resolve Team names if possible
    team_codes = list({r[3] for r in rows if r[3]})
    team_map = {}
    if team_codes:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(team_codes))
            cursor.execute(f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode IN ({placeholders})", team_codes)
            team_map = dict(cursor.fetchall())

    allocations = []
    for r in rows:
        team_name = team_map.get(r[3], r[3] or "—")
        allocations.append(list(r[:3]) + [team_name] + list(r[4:]))

    if request.method == "POST":
        emp_list = request.POST.getlist("employee")
        shift = request.POST["shift"]
        from_date = request.POST["from_date"]
        to_date = request.POST.get("to_date") or None

        target_employees = []
        if 'all' in emp_list:
             with connection.cursor() as cursor:
                cursor.execute("SELECT EmployeeCode FROM Employees")
                target_employees = [row[0] for row in cursor.fetchall()]
        else:
            target_employees = emp_list

        with connection.cursor() as cursor:
            for emp_code in target_employees:
                cursor.execute("""
                    INSERT INTO EmployeeShiftAllocation
                    (EmployeeCode, ShiftId, FromDate, ToDate)
                    VALUES (%s, %s, %s, %s)
                """, [emp_code, shift, from_date, to_date])

        return redirect("shift_allocation")

    return render(request, "myapp/shift_allocation.html", {
        "shifts": shifts,
        "employees": employees,
        "allocations": allocations
    })
def delete_shift_allocation(request, id):
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM EmployeeShiftAllocation WHERE AllocationId = %s",
            [id]
        )
    return redirect("shift_allocation")
def fmt(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{int(h):02d}:{int(m):02d}"

def sec_to_hours(sec):
    return round(sec / 3600, 2)
def get_leave_month(ref_date):
    year = ref_date.year
    month = ref_date.month

    if ref_date.day >= 26:
        month += 1
        if month > 12:
            month = 1
            year += 1

    return year, month

def dashboard(request):
    if "year" in request.GET and "month" in request.GET:
        year = int(request.GET["year"])
        month = int(request.GET["month"])
    else:
        year, month = get_leave_month(date.today())

    if month > 12:
        return redirect(f"/?month=1&year={year + 1}")
    if month < 1:
        return redirect(f"/?month=12&year={year - 1}")

    start_date = date(year - 1, 12, 26) if month == 1 else date(year, month - 1, 26)
    end_date   = date(year, month, 25)
    PRESENT_STATUSES = {
        "Present full day",
        "Late",
        "Grace Time",
        "Permission",
        "Half Day",
        "Half Day Leave",
        "Work From Home present",
    }
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    emp_code = request.session["emp_code"]
    
    trainee = is_trainee(emp_code)
    
    


    
    # =====================================================
    # 🔹 FETCH EMPLOYEE SHIFT ALLOCATION (DATE-WISE)
    # =====================================================
    employee_shifts = {}   # date -> shift_start_time

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                a.FromDate,
                ISNULL(a.ToDate, '2099-12-31'),
                s.BeginTime
            FROM EmployeeShiftAllocation a
            JOIN Shifts s ON s.ShiftId = a.ShiftId
            WHERE a.EmployeeCode = %s
            AND (
                    a.FromDate BETWEEN %s AND %s
                OR a.ToDate BETWEEN %s AND %s
                OR (a.FromDate <= %s AND a.ToDate >= %s)
            )
        """, [
            emp_code,
            start_date, end_date,
            start_date, end_date,
            start_date, end_date
        ])

        for fs, fe, begin_time in cursor.fetchall():
            fs = fs if isinstance(fs, date) else fs.date()
            fe = fe if isinstance(fe, date) else fe.date()

            d = fs
            while d <= fe:
                employee_shifts[d] = begin_time
                d += timedelta(days=1)

    
    # =====================================================
    # 1️⃣ FETCH HOLIDAYS & EMPLOYEE OVERRIDES
    # =====================================================
    holidays = {}  # date -> DayType (Holiday / WeekOff)

    with connection.cursor() as cursor:
        # 1a. Company holidays
        cursor.execute("""
            SELECT Date, DayType
            FROM CompanyHolidayWeekOff
            WHERE Date BETWEEN %s AND %s
        """, [start_date, end_date])
        for dt, day_type in cursor.fetchall():
            holidays[dt] = day_type

        # 1b. Employee-specific overrides
        cursor.execute("""
            SELECT Date, DayType
            FROM EmployeeHolidayWeekOff
            WHERE EmployeeCode = %s
            AND Date BETWEEN %s AND %s
        """, [emp_code, start_date, end_date])
        for dt, day_type in cursor.fetchall():
            holidays[dt] = day_type  # override company

    # =====================================================
    # 2️⃣ FETCH APPROVED LEAVES
    # =====================================================
    leaves = {}

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND (
                    (FromDate BETWEEN   %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [emp_code, start_date, end_date, start_date, end_date, start_date, end_date])
        
        for fs, fe, lt in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe
            d = fs
            while d <= fe:
                leaves[d] = lt
                d += timedelta(days=1)
    # =====================================================
    # 2️⃣ FETCH DEVICE LOGS
    # =====================================================
    punch_sql = """
        SELECT CAST(LogDate AS DATE) AS LogDay, LogDate
        FROM (
            SELECT * FROM dbo.DeviceLogs_3_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_2_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_1_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_12_2025
        ) dl
        WHERE UserId = %s
          AND CAST(LogDate AS DATE) BETWEEN %s AND %s
        ORDER BY LogDay, LogDate
    """

    with connection.cursor() as cursor:
        cursor.execute(punch_sql, [emp_code, start_date, end_date])
        punch_rows = cursor.fetchall()

    daily_logs = defaultdict(list)
    device_days = set()
    


    for day, log_time in punch_rows:
        daily_logs[day].append(log_time)
        device_days.add(day)
    
    # =====================================================
    # 3️⃣ FETCH MANUAL PUNCHES
    # =====================================================
    manual_sql = """
        SELECT PunchDate, PunchTime
        FROM ManualPunches
        WHERE EmployeeCode = %s
          AND PunchDate BETWEEN %s AND %s
        ORDER BY PunchDate, PunchTime
    """

    with connection.cursor() as cursor:
        cursor.execute(manual_sql, [emp_code, start_date, end_date])
        manual_rows = cursor.fetchall()
    manual_logs = defaultdict(list)
    for pdate, ptime in manual_rows:
        if isinstance(pdate, datetime):
            pdate = pdate.date()

        if isinstance(ptime, time):
            ptime = datetime.combine(pdate, ptime)

        manual_logs[pdate].append(ptime)

    manual_days = set(manual_logs.keys())


    # =====================================================
    # 4️⃣ MERGE MANUAL → DAILY LOGS (DEVICE PRIORITY)
    # =====================================================
    for day, logs in manual_logs.items():
        if day not in daily_logs or not daily_logs[day]:
            daily_logs[day] = logs
    manual_days = set(manual_logs.keys()) 
    
    # =====================================================
    # 2a️⃣ FETCH APPROVED GRACE TIME / PERMISSION
    # =====================================================
    daily_permissions = {}  # key=date, value=leave_type

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND LeaveType IN ('Grace Time', 'Permission', 'Half Day', 'Comp-Off', 'Work From Home')
            AND (
                    (FromDate BETWEEN %s AND %s)
                OR  (ToDate BETWEEN %s AND %s)
                OR  (FromDate <= %s AND ToDate >= %s)
            )
        """, [emp_code, start_date, end_date, start_date, end_date, start_date, end_date])

        for fs, fe, lt in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe
            d = fs
            while d <= fe:
                daily_permissions[d] = lt
                d += timedelta(days=1)

    # =====================================================
    # 5️⃣ BUILD ATTENDANCE (WITH SOURCE + LATE FLAG)
    # =====================================================
    attendance = {}
    daily_totals = {}

    for day, logs in daily_logs.items():
        if len(logs) < 1:  # ✅ allow at least 1 punch
            continue

        logs.sort()
        punch_in = logs[0]
        punch_out = logs[-1] if len(logs) > 1 else None

        working_sec = 0
        break_sec = 0

        # calculate working/break only if there are at least 2 punches
        if len(logs) > 1:
            for i in range(len(logs) - 1):
                diff = (logs[i + 1] - logs[i]).total_seconds()
                if i % 2 == 0:
                    working_sec += diff
                else:
                    break_sec += diff

        # Check if late
        # Check if late or half day
        SHIFT_HOURS_REQUIRED = 9 * 3600 
        shift_start = employee_shifts.get(day)
        if not shift_start:
            shift_start = time(9, 30)

        if isinstance(shift_start, str):
            shift_start = datetime.strptime(shift_start, "%H:%M").time()
        
        shift_start_dt = datetime.combine(day, shift_start)

        present_limit_dt = shift_start_dt + timedelta(minutes=0)
        late_limit_dt = shift_start_dt + timedelta(minutes=1)

        if punch_in <= present_limit_dt:
            status = "Present full day"
            late_flag = False
            late_duration_sec = 0

        elif punch_in <= late_limit_dt:
            status = "Late"
            late_flag = True
            late_duration_sec = (punch_in - present_limit_dt).total_seconds()

        else:
            status = "Half Day"
            late_flag = True
            late_duration_sec = (punch_in - late_limit_dt).total_seconds()
        shift_start_dt = datetime.combine(day, shift_start)
        shift_end_dt = shift_start_dt + timedelta(seconds=SHIFT_HOURS_REQUIRED)

        # Determine Early Going (only for present full day / late / half day)
        early_going_flag = False
        if punch_out is not None and punch_out < shift_end_dt and status in ["Present full day", "Late", "Half Day"]:
            early_going_flag = True
        attendance[day] = {
            "punch_in": punch_in,
            "punch_out": punch_out,
            "status": status,
            "source": "device" if day in device_days else "manual",
            "late": late_flag,
            "late_duration": fmt(late_duration_sec),
            "working": fmt(working_sec),
            "break": fmt(break_sec),
            "early_going": early_going_flag,
        }


        daily_totals[day] = (working_sec, break_sec)


    # =====================================================
    # 6️⃣ DAILY WORKING & BREAK SUMMARY
    # =====================================================
    daily_summary = {}

    for day, logs in daily_logs.items():
        working = 0
        breaking = 0

        for i in range(len(logs) - 1):
            diff = (logs[i + 1] - logs[i]).total_seconds()
            if i % 2 == 0:
                working += diff
            else:
                breaking += diff

        daily_summary[day] = {
            "working": fmt(working),
            "break": fmt(breaking),
        }
    # =====================================================
    # 5a️⃣ MERGE APPROVED GRACE TIME / PERMISSION INTO ATTENDANCE
    # =====================================================
    # =====================================================
    # 5a️⃣ APPLY APPROVED GRACE TIME / PERMISSION (OVERRIDE STATUS)
    # =====================================================
    for day, entry in attendance.items():
        if day not in daily_permissions:
            continue

        leave_type = daily_permissions[day]
        entry["leave_type"] = leave_type
        entry["APPROVED"] = True

        # Grace Time overrides LATE
        if leave_type == "Grace Time" and entry["status"] == "Late":
            entry["status"] = "Grace Time"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        # Permission overrides HALF DAY
        elif leave_type == "Permission":
            if entry["status"] == "Half Day":
                entry["status"] = "Permission"
                entry["late"] = False
                entry["late_duration"] = "00:00"
            entry["early_going"] = False

        elif leave_type == "Half Day":
            entry["status"] = "Half Day Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        elif leave_type == "Work From Home":
            entry["status"] = "Work From Home present"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        elif leave_type == "Comp-Off":
            entry["status"] = "Comp-Off Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"

    # =====================================================
    # 3️⃣ BUILD CALENDAR DAYS WITH HOLIDAY/LEAVE LOGIC
    # =====================================================
    calendar_days = []
    total_days = (end_date - start_date).days + 1
    current_date = start_date

    def is_absent(d):
        if not isinstance(d, (date, datetime)): return False
        if d < start_date or d > end_date: return False
        return d not in attendance and d not in leaves and d not in holidays and d.weekday() != 6

    while current_date <= end_date:
        weekday = current_date.weekday()
        day_info = {"date": current_date, "break": "00:00"}
        shift_start = employee_shifts.get(current_date)
        if shift_start:
            day_info["shift_start"] = shift_start

        
        if current_date in attendance:
            day_info.update(attendance[current_date])
            if (
                day_info.get("leave_type") in ["Grace Time", "Permission"]
                and day_info.get("APPROVED") is True
            ):
                day_info["late"] = False
        elif current_date in leaves:
            day_info.update({
                "status": "Leave",
                "leave_type": leaves[current_date],
            })
        elif current_date in holidays or weekday == 6:
            # -------------------------------
            # Apply Sandwich Logic
            # -------------------------------
            emp_rest_days = set(holidays.keys())
            # Add all Sundays in the range to rest days
            d_sun = start_date
            while d_sun <= end_date:
                if d_sun.weekday() == 6:
                    emp_rest_days.add(d_sun)
                d_sun += timedelta(days=1)

            prev_wd = current_date - timedelta(days=1)
            while prev_wd >= start_date and prev_wd in emp_rest_days:
                prev_wd -= timedelta(days=1)
            
            next_wd = current_date + timedelta(days=1)
            while next_wd <= end_date and next_wd in emp_rest_days:
                next_wd += timedelta(days=1)
            
            if is_absent(prev_wd) and is_absent(next_wd):
                day_info.update({"status": "Absent", "break": "00:00"})
            else:
                day_info.update({
                    "status": holidays.get(current_date, "Weekly Off"),
                    "break": "00:00"
                })
        else:
            day_info.update({"status": "Absent", "break": "00:00"})

        calendar_days.append(day_info)
        current_date += timedelta(days=1)
        
    # =====================================================
    # 8️⃣ CALENDAR GRID
    # =====================================================
    first_weekday = start_date.weekday()
    calendar_grid = [None] * first_weekday + calendar_days

    while len(calendar_grid) % 7 != 0:
        calendar_grid.append(None)


    total_working_seconds = sum(v[0] for v in daily_totals.values())
    total_break_seconds = sum(v[1] for v in daily_totals.values())

    total_working = fmt(total_working_seconds)
    total_break = fmt(total_break_seconds)

    # =====================================================
    # 9️⃣ MONTHLY TABLE DATA
    # =====================================================
    table_data = []
    max_pairs = 0
    total_working_seconds = 0
    total_break_seconds = 0

    for day, logs in daily_logs.items():
        pairs = []
        working = 0
        breaking = 0

        for i in range(0, len(logs), 2):
            in_time = logs[i]
            out_time = logs[i + 1] if i + 1 < len(logs) else None
            pairs.append((in_time, out_time))

        for i in range(len(logs) - 1):
            diff = (logs[i + 1] - logs[i]).total_seconds()
            if i % 2 == 0:
                working += diff
            else:
                breaking += diff

        max_pairs = max(max_pairs, len(pairs))
        total_working_seconds += working
        total_break_seconds += breaking

        table_data.append({
            "date": day,
            "pairs": pairs,
            "working": fmt(working),
            "break": fmt(breaking),
        })

    total_columns = (max_pairs * 2) + 1
    # =====================================================
    # 🔹 YEARLY LEAVE COUNT (CYCLE-AWARE: Dec 26 - Dec 25)
    # =====================================================
    YEARLY_LEAVES_ALLOWED = 18
    LEAVE_TYPES = ("Casual Leave", "Sick Leave", "Optional Leave")
    yearly_leave_days = set()

    # Cycle-aware year start (Dec 26 of prev year)
    cycle_year_start = date(year - 1, 12, 26)
    cycle_year_end = date(year, 12, 25)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND LeaveType IN (%s, %s, %s)
            AND (
                    (FromDate BETWEEN %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [
            emp_code,
            *LEAVE_TYPES,
            cycle_year_start, cycle_year_end,
            cycle_year_start, cycle_year_end,
            cycle_year_start, cycle_year_end
        ])

        for fs, fe in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe

            # clip to selected cycle year
            start = max(fs, cycle_year_start)
            end = min(fe, cycle_year_end)

            d = start
            while d <= end:
                yearly_leave_days.add(d)
                d += timedelta(days=1)

    # ✅ FINAL REQUIRED VALUES
    leaves_taken = len(yearly_leave_days)
    leaves_remaining = max(0, YEARLY_LEAVES_ALLOWED - leaves_taken)
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                lr.LeaveId,
                lr.EmployeeCode,
                e.EmployeeName,
                lr.FromDate,
                lr.ToDate,
                lr.LeaveType,
                lr.Status,
                lr.Remarks
            FROM LeaveRequests lr
            JOIN Employees e ON e.EmployeeCode = lr.EmployeeCode
            WHERE lr.EmployeeCode = %s
        """, [emp_code])

        rows = cursor.fetchall()


    leaves = [{
        "leave_id": r[0],
        "employee": f"{r[1]} - {r[2]}",
        "from": r[3],
        "to": r[4],
        "type": r[5],
        "status": r[6],
        "remarks": r[7],
    } for r in rows]

    

    chart_labels = []
    monthly_break_hours = []
    monthly_working_hours = []
    monthly_first_punch = []

    total_break_sec = 0
    total_working_sec = 0
    first_punch_minutes_sum = 0
    present_days_count = 0

    current = start_date
    while current <= end_date:

        chart_labels.append(current.strftime("%d-%b"))

        # ✅ ONLY PRESENT DAYS
        if current in attendance and attendance[current]["status"] in PRESENT_STATUSES:

            # --- working & break ---
            working_sec, break_sec = daily_totals.get(current, (0, 0))

            monthly_working_hours.append(round(working_sec / 3600, 2))
            monthly_break_hours.append(round(break_sec / 3600, 2))

            total_working_sec += working_sec
            total_break_sec += break_sec

            # --- first punch ---
            punch_in = attendance[current]["punch_in"]
            minutes = punch_in.hour * 60 + punch_in.minute
            monthly_first_punch.append(minutes)
            

            first_punch_minutes_sum += minutes
            present_days_count += 1

        else:
            # ❌ Non-present day → no bar value
            monthly_working_hours.append(None)
            monthly_break_hours.append(None)
            monthly_first_punch.append(None)

        current += timedelta(days=1)

    avg_working_hours = (
        round((total_working_sec / 3600) / present_days_count, 2)
        if present_days_count > 0 else 0
    )

    avg_break_hours = (
        round((total_break_sec / 3600) / present_days_count, 2)
        if present_days_count > 0 else 0
    )

    avg_first_punch = (
        round(first_punch_minutes_sum / present_days_count, 2)
        if present_days_count > 0 else 0
    )

    def minutes_to_hhmm(mins):
        h = int(mins) // 60
        m = int(mins) % 60
        return f"{h:02d}:{m:02d}"

    avg_first_punch_time = (
        minutes_to_hhmm(avg_first_punch)
        if present_days_count > 0 else "--:--"
    )
    
    today = date.today()
    ensure_quarter_balance(emp_code, today)
    ensure_monthly_balance(emp_code, today)

    quarter_balances = {} if trainee else get_current_balances(emp_code, today)
    
    # Monthly balances adjusted for custom month range
    used_permission = get_used_permission_hours(emp_code, start_date, end_date)
    used_grace = get_used_monthly_leaves(emp_code, "Grace Time", start_date, end_date)

    monthly_balances = {
        "Grace Time": max(MONTHLY_LEAVE_POLICY["Grace Time"] - used_grace, 0),
        "Permission": max(MONTHLY_LEAVE_POLICY["Permission"] - used_permission, 0)
    }
    

    balances = {**quarter_balances, **monthly_balances}
    total_quarter_left = sum(v for v in quarter_balances.values() if isinstance(v, (int, float)))

    


    # =====================================================
    # 🔟 TOTALS
    # =====================================================
    total_present = sum(1 for d in calendar_days if d["status"] == "Present full day" or d["status"] == "Late" or d["status"] == "Half Day" or d["status"] == "Permission" or d["status"] == "Grace Time" or d["status"] == "Half Day Leave" or d["status"] == "Work From Home present")
    total_leave = sum(1 for d in calendar_days if d["status"] == "Leave" or d["status"] == "Comp-Off Leave" or d["status"] == "Comp-Off")
    total_holiday = sum(1 for d in calendar_days if d["status"] == "Holiday")
    total_weekly_off = sum(1 for d in calendar_days if d["status"] == "WeekOff" or d["status"] == "Weekly Off")
    total_absent = sum(1 for d in calendar_days if d["status"] == "Absent")

    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # --- Announcements & Birthdays ---
    active_announcements = CompanyAnnouncement.objects.filter(IsActive=True).order_by('-CreatedAt')[:5]
    
    # Birthdays today or in the next 7 days
    upcoming_birthdays = []
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EmployeeName, EmployeeCode, DOB, EmployeePhoto
            FROM Employees
            WHERE 
                (MONTH(DOB) = MONTH(GETDATE()) AND DAY(DOB) >= DAY(GETDATE()) AND DAY(DOB) <= DAY(DATEADD(day, 7, GETDATE())))
                OR (MONTH(DOB) = MONTH(DATEADD(day, 7, GETDATE())) AND DAY(DOB) <= DAY(DATEADD(day, 7, GETDATE())) AND MONTH(DOB) <> MONTH(GETDATE()))
        """)
        birthday_rows = cursor.fetchall()

    for bname, bcode, bdob, bphoto in birthday_rows:
        photo_b64 = None
        if bphoto:
            try:
                photo_bytes = bphoto
                if isinstance(photo_bytes, memoryview):
                    photo_bytes = photo_bytes.tobytes()
                photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
            except Exception:
                pass
        
        upcoming_birthdays.append({
            "name": bname,
            "code": bcode,
            "dob": bdob,
            "photo": photo_b64,
            "is_today": bdob.day == today.day and bdob.month == today.month
        })

    return render(request, "myapp/dashboard.html", {
        "announcements": active_announcements,
        "birthdays": upcoming_birthdays,
        "calendar_days": calendar_days,
        "calendar_grid": calendar_grid,
        "weekdays": weekdays,
        "total_present": total_present,
        "total_absent": total_absent,
        "total_leave": total_leave,
        "total_weekly_off": total_weekly_off,
        "total_holiday": total_holiday,
        "table_data": table_data,
        "pair_range": range(max_pairs),
        "total_working": fmt(total_working_seconds),
        "total_break": fmt(total_break_seconds),
        "total_columns": total_columns,
        "year": year,
        "month": month,
        "emp_name": request.session["emp_name"],
        "user_allocate_position": request.session.get("allocate_position"),
        "balances": balances,
        "total_quarter_left": total_quarter_left,

        "leaves_taken": leaves_taken,
        "leaves_remaining": leaves_remaining,
        "leaves": leaves,

        "chart_labels": chart_labels,
        "monthly_working_hours": monthly_working_hours,
        "monthly_break_hours": monthly_break_hours,
        "monthly_first_punch": monthly_first_punch,
        "avg_first_punch_time":avg_first_punch_time,
        "avg_working_hours": avg_working_hours,
        "avg_break_hours": avg_break_hours,
        "avg_first_punch": avg_first_punch,
        "present_days_count": present_days_count,

    })



def my_attendance(request):

    PRESENT_STATUSES = {
        "Present full day",
        "Late",
        "Grace Time",
        "Permission",
        "Half Day",
        "Half Day Leave",
        "Work From Home present",
    }
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    emp_code = request.session["emp_code"]

    if "year" in request.GET and "month" in request.GET:
        year = int(request.GET["year"])
        month = int(request.GET["month"])
    else:
        year, month = get_leave_month(date.today())

    if month < 1:
        return redirect(reverse("my_attendance") + f"?month=12&year={year - 1}")

    if month > 12:
        return redirect(reverse("my_attendance") + f"?month=1&year={year + 1}")

    start_date = date(year - 1, 12, 26) if month == 1 else date(year, month - 1, 26)
    end_date = date(year, month, 25)

    
    # =====================================================
    # 🔹 FETCH EMPLOYEE SHIFT ALLOCATION (DATE-WISE)
    # =====================================================
    employee_shifts = {}   # date -> shift_start_time

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                a.FromDate,
                ISNULL(a.ToDate, '2099-12-31'),
                s.BeginTime
            FROM EmployeeShiftAllocation a
            JOIN Shifts s ON s.ShiftId = a.ShiftId
            WHERE a.EmployeeCode = %s
            AND (
                    a.FromDate BETWEEN %s AND %s
                OR a.ToDate BETWEEN %s AND %s
                OR (a.FromDate <= %s AND a.ToDate >= %s)
            )
        """, [
            emp_code,
            start_date, end_date,
            start_date, end_date,
            start_date, end_date
        ])

        for fs, fe, begin_time in cursor.fetchall():
            fs = fs if isinstance(fs, date) else fs.date()
            fe = fe if isinstance(fe, date) else fe.date()

            d = fs
            while d <= fe:
                employee_shifts[d] = begin_time
                d += timedelta(days=1)

    
    # =====================================================
    # 1️⃣ FETCH HOLIDAYS & EMPLOYEE OVERRIDES
    # =====================================================
    holidays = {}  # date -> DayType (Holiday / WeekOff)

    with connection.cursor() as cursor:
        # 1a. Company holidays
        cursor.execute("""
            SELECT Date, DayType
            FROM CompanyHolidayWeekOff
            WHERE Date BETWEEN %s AND %s
        """, [start_date, end_date])
        for dt, day_type in cursor.fetchall():
            holidays[dt] = day_type

        # 1b. Employee-specific overrides
        cursor.execute("""
            SELECT Date, DayType
            FROM EmployeeHolidayWeekOff
            WHERE EmployeeCode = %s
            AND Date BETWEEN %s AND %s
        """, [emp_code, start_date, end_date])
        for dt, day_type in cursor.fetchall():
            holidays[dt] = day_type  # override company

    # =====================================================
    # 2️⃣ FETCH APPROVED LEAVES
    # =====================================================
    leaves = {}

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND (
                    (FromDate BETWEEN   %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [emp_code, start_date, end_date, start_date, end_date, start_date, end_date])
        
        for fs, fe, lt in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe
            d = fs
            while d <= fe:
                leaves[d] = lt
                d += timedelta(days=1)
    # =====================================================
    # 2️⃣ FETCH DEVICE LOGS
    # =====================================================
    punch_sql = """
        SELECT CAST(LogDate AS DATE) AS LogDay, LogDate
        FROM (
            SELECT * FROM dbo.DeviceLogs_3_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_2_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_1_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_12_2025
        ) dl
        WHERE UserId = %s
          AND CAST(LogDate AS DATE) BETWEEN %s AND %s
        ORDER BY LogDay, LogDate
    """

    with connection.cursor() as cursor:
        cursor.execute(punch_sql, [emp_code, start_date, end_date])
        punch_rows = cursor.fetchall()

    daily_logs = defaultdict(list)
    device_days = set()
    


    for day, log_time in punch_rows:
        daily_logs[day].append(log_time)
        device_days.add(day)
    
    # =====================================================
    # 3️⃣ FETCH MANUAL PUNCHES
    # =====================================================
    manual_sql = """
        SELECT PunchDate, PunchTime
        FROM ManualPunches
        WHERE EmployeeCode = %s
          AND PunchDate BETWEEN %s AND %s
          AND ApprovalStatus = 'APPROVED'
        ORDER BY PunchDate, PunchTime
    """

    with connection.cursor() as cursor:
        cursor.execute(manual_sql, [emp_code, start_date, end_date])
        manual_rows = cursor.fetchall()
    manual_logs = defaultdict(list)
    for pdate, ptime in manual_rows:
        if isinstance(pdate, datetime):
            pdate = pdate.date()

        if isinstance(ptime, time):
            ptime = datetime.combine(pdate, ptime)

        manual_logs[pdate].append(ptime)

    manual_days = set(manual_logs.keys())


    # =====================================================
    # 4️⃣ MERGE MANUAL → DAILY LOGS (DEVICE PRIORITY)
    # =====================================================
    for day, logs in manual_logs.items():
        if day not in daily_logs or not daily_logs[day]:
            daily_logs[day] = logs
    manual_days = set(manual_logs.keys()) 
    
    # =====================================================
    # 2a️⃣ FETCH APPROVED GRACE TIME / PERMISSION
    # =====================================================
    daily_permissions = {}  # key=date, value=leave_type

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND LeaveType IN ('Grace Time', 'Permission', 'Half Day', 'Comp-Off', 'Work From Home', 'Informed Leave')
            AND (
                    (FromDate BETWEEN %s AND %s)
                OR  (ToDate BETWEEN %s AND %s)
                OR  (FromDate <= %s AND ToDate >= %s)
            )
        """, [emp_code, start_date, end_date, start_date, end_date, start_date, end_date])

        for fs, fe, lt in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe
            d = fs
            while d <= fe:
                daily_permissions[d] = lt
                d += timedelta(days=1)

    # =====================================================
    # 5️⃣ BUILD ATTENDANCE (WITH SOURCE + LATE FLAG)
    # =====================================================
    attendance = {}
    daily_totals = {}

    for day, logs in daily_logs.items():
        if len(logs) < 1:  # ✅ allow at least 1 punch
            continue

        logs.sort()
        punch_in = logs[0]
        punch_out = logs[-1] if len(logs) > 1 else None

        working_sec = 0
        break_sec = 0

        # calculate working/break only if there are at least 2 punches
        if len(logs) > 1:
            for i in range(len(logs) - 1):
                diff = (logs[i + 1] - logs[i]).total_seconds()
                if i % 2 == 0:
                    working_sec += diff
                else:
                    break_sec += diff

        # Check if late
        # Check if late or half day
        SHIFT_HOURS_REQUIRED = 9 * 3600 
        shift_start = employee_shifts.get(day)
        if not shift_start:
            shift_start = time(9, 30)

        if isinstance(shift_start, str):
            shift_start = datetime.strptime(shift_start, "%H:%M").time()
        
        shift_start_dt = datetime.combine(day, shift_start)

        present_limit_dt = shift_start_dt + timedelta(minutes=1)
        late_limit_dt = shift_start_dt + timedelta(minutes=1)

        if punch_in <= present_limit_dt:
            status = "Present full day"
            late_flag = False
            late_duration_sec = 0

        elif punch_in <= late_limit_dt:
            status = "Late"
            late_flag = True
            late_duration_sec = (punch_in - present_limit_dt).total_seconds()

        else:
            status = "Half Day"
            late_flag = True
            late_duration_sec = (punch_in - late_limit_dt).total_seconds()
        shift_start_dt = datetime.combine(day, shift_start)
        shift_end_dt = shift_start_dt + timedelta(seconds=SHIFT_HOURS_REQUIRED)

        # Determine Early Going (only for present full day / late / half day)
        early_going_flag = False
        if punch_out is not None and punch_out < shift_end_dt and status in ["Present full day", "Late", "Half Day"]:
            early_going_flag = True
        attendance[day] = {
            "punch_in": punch_in,
            "punch_out": punch_out,
            "status": status,
            "source": "device" if day in device_days else "manual",
            "late": late_flag,
            "late_duration": fmt(late_duration_sec),
            "working": fmt(working_sec),
            "break": fmt(break_sec),
            "early_going": early_going_flag,
        }


        daily_totals[day] = (working_sec, break_sec)


    # =====================================================
    # 6️⃣ DAILY WORKING & BREAK SUMMARY
    # =====================================================
    daily_summary = {}

    for day, logs in daily_logs.items():
        working = 0
        breaking = 0

        for i in range(len(logs) - 1):
            diff = (logs[i + 1] - logs[i]).total_seconds()
            if i % 2 == 0:
                working += diff
            else:
                breaking += diff

        daily_summary[day] = {
            "working": fmt(working),
            "break": fmt(breaking),
        }
    # =====================================================
    # 5a️⃣ MERGE APPROVED GRACE TIME / PERMISSION INTO ATTENDANCE
    # =====================================================
    # =====================================================
    # 5a️⃣ APPLY APPROVED GRACE TIME / PERMISSION (OVERRIDE STATUS)
    # =====================================================
    for day, entry in attendance.items():
        if day not in daily_permissions:
            continue

        leave_type = daily_permissions[day]
        entry["leave_type"] = leave_type
        entry["APPROVED"] = True

        # Grace Time overrides LATE
        if leave_type == "Grace Time" and entry["status"] == "Late":
            entry["status"] = "Grace Time"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        # Permission overrides HALF DAY
        elif leave_type == "Permission":
            if entry["status"] == "Half Day":
                entry["status"] = "Permission"
                entry["late"] = False
                entry["late_duration"] = "00:00"
            entry["early_going"] = False

        elif leave_type == "Half Day":
            entry["status"] = "Half Day Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        elif leave_type == "Work From Home":
            entry["status"] = "Work From Home present"
            entry["late"] = False
            entry["late_duration"] = "00:00"     

        elif leave_type == "Comp-Off":
            entry["status"] = "Comp-Off Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"
        elif leave_type == "Informed Leave":
            entry["status"] = "Informed Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"
    # =====================================================
    # 3️⃣ BUILD CALENDAR DAYS WITH HOLIDAY/LEAVE LOGIC
    # =====================================================
    calendar_days = []
    total_days = (end_date - start_date).days + 1
    current_date = start_date

    def is_absent(d):
        if d < start_date or d > end_date: return False
        return d not in attendance and d not in leaves and d not in holidays

    while current_date <= end_date:
        weekday = current_date.weekday()
        day_info = {"date": current_date, "break": "00:00"}
        shift_start = employee_shifts.get(current_date)
        if shift_start:
            day_info["shift_start"] = shift_start

        if current_date in attendance:
            day_info.update(attendance[current_date])
            if (
                day_info.get("leave_type") in ["Grace Time", "Permission"]
                and day_info.get("APPROVED") is True
            ):
                day_info["late"] = False
        elif current_date in leaves:
            day_info.update({
                "status": "Leave",
                "leave_type": leaves[current_date],
            })
        elif current_date in holidays:
            # -------------------------------
            # Apply Sandwich Logic
            # -------------------------------
            prev_wd = current_date - timedelta(days=1)
            while prev_wd >= start_date and prev_wd in holidays:
                prev_wd -= timedelta(days=1)
            
            next_wd = current_date + timedelta(days=1)
            while next_wd <= end_date and next_wd in holidays:
                next_wd += timedelta(days=1)
            
            if is_absent(prev_wd) and is_absent(next_wd):
                day_info.update({"status": "Absent", "break": "00:00"})
            else:
                day_info.update({
                    "status": holidays[current_date],  # Holiday / WeekOff
                    "break": "00:00"
                })
        else:
            day_info.update({"status": "Absent", "break": "00:00"})

        calendar_days.append(day_info)
        current_date += timedelta(days=1)
        
    # =====================================================
    # 8️⃣ CALENDAR GRID
    # =====================================================
    first_weekday = start_date.weekday()
    calendar_grid = [None] * first_weekday + calendar_days

    while len(calendar_grid) % 7 != 0:
        calendar_grid.append(None)


    total_working_seconds = sum(v[0] for v in daily_totals.values())
    total_break_seconds = sum(v[1] for v in daily_totals.values())

    total_working = fmt(total_working_seconds)
    total_break = fmt(total_break_seconds)

    # =====================================================
    # 9️⃣ MONTHLY TABLE DATA
    # =====================================================
    table_data = []
    max_pairs = 0
    total_working_seconds = 0
    total_break_seconds = 0

    for day, logs in daily_logs.items():
        pairs = []
        working = 0
        breaking = 0

        for i in range(0, len(logs), 2):
            in_time = logs[i]
            out_time = logs[i + 1] if i + 1 < len(logs) else None
            pairs.append((in_time, out_time))

        for i in range(len(logs) - 1):
            diff = (logs[i + 1] - logs[i]).total_seconds()
            if i % 2 == 0:
                working += diff
            else:
                breaking += diff

        max_pairs = max(max_pairs, len(pairs))
        total_working_seconds += working
        total_break_seconds += breaking

        table_data.append({
            "date": day,
            "pairs": pairs,
            "working": fmt(working),
            "break": fmt(breaking),
        })

    total_columns = (max_pairs * 2) + 1
    # =====================================================
    # 🔹 YEARLY LEAVE COUNT (CYCLE-AWARE: Dec 26 - Dec 25)
    # =====================================================
    YEARLY_LEAVES_ALLOWED = 18
    LEAVE_TYPES = ("Casual Leave", "Sick Leave", "Optional Leave")
    yearly_leave_days = set()

    # Cycle-aware year start (Dec 26 of prev year)
    cycle_year_start = date(year - 1, 12, 26)
    cycle_year_end = date(year, 12, 25)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND LeaveType IN (%s, %s, %s)
            AND (
                    (FromDate BETWEEN %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [
            emp_code,
            *LEAVE_TYPES,
            cycle_year_start, cycle_year_end,
            cycle_year_start, cycle_year_end,
            cycle_year_start, cycle_year_end
        ])

        for fs, fe in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe

            # clip to selected cycle year
            start = max(fs, cycle_year_start)
            end = min(fe, cycle_year_end)

            d = start
            while d <= end:
                yearly_leave_days.add(d)
                d += timedelta(days=1)

    # ✅ FINAL REQUIRED VALUES
    leaves_taken = len(yearly_leave_days)
    leaves_remaining = max(0, YEARLY_LEAVES_ALLOWED - leaves_taken)
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                lr.LeaveId,
                lr.EmployeeCode,
                e.EmployeeName,
                lr.FromDate,
                lr.ToDate,
                lr.LeaveType,
                lr.Status,
                lr.Remarks
            FROM LeaveRequests lr
            JOIN Employees e ON e.EmployeeCode = lr.EmployeeCode
            WHERE lr.EmployeeCode = %s
        """, [emp_code])

        rows = cursor.fetchall()


    leaves = [{
        "leave_id": r[0],
        "employee": f"{r[1]} - {r[2]}",
        "from": r[3],
        "to": r[4],
        "type": r[5],
        "status": r[6],
        "remarks": r[7],
    } for r in rows]

    

    chart_labels = []
    monthly_break_hours = []
    monthly_working_hours = []
    monthly_first_punch = []

    total_break_sec = 0
    total_working_sec = 0
    first_punch_minutes_sum = 0
    present_days_count = 0

    current = start_date
    while current <= end_date:

        chart_labels.append(current.strftime("%d-%b"))

        # ✅ ONLY PRESENT DAYS
        if current in attendance and attendance[current]["status"] in PRESENT_STATUSES:

            # --- working & break ---
            working_sec, break_sec = daily_totals.get(current, (0, 0))

            monthly_working_hours.append(round(working_sec / 3600, 2))
            monthly_break_hours.append(round(break_sec / 3600, 2))

            total_working_sec += working_sec
            total_break_sec += break_sec

            # --- first punch ---
            punch_in = attendance[current]["punch_in"]
            minutes = punch_in.hour * 60 + punch_in.minute
            monthly_first_punch.append(minutes)
            

            first_punch_minutes_sum += minutes
            present_days_count += 1

        else:
            # ❌ Non-present day → no bar value
            monthly_working_hours.append(None)
            monthly_break_hours.append(None)
            monthly_first_punch.append(None)

        current += timedelta(days=1)

    avg_working_hours = (
        round((total_working_sec / 3600) / present_days_count, 2)
        if present_days_count > 0 else 0
    )

    avg_break_hours = (
        round((total_break_sec / 3600) / present_days_count, 2)
        if present_days_count > 0 else 0
    )

    avg_first_punch = (
        round(first_punch_minutes_sum / present_days_count, 2)
        if present_days_count > 0 else 0
    )

    def minutes_to_hhmm(mins):
        h = int(mins) // 60
        m = int(mins) % 60
        return f"{h:02d}:{m:02d}"

    avg_first_punch_time = (
        minutes_to_hhmm(avg_first_punch)
        if present_days_count > 0 else "--:--"
    )
    

    


    # =====================================================
    # 🔟 TOTALS
    # =====================================================
    total_present = sum(1 for d in calendar_days if d["status"] == "Present full day" or d["status"] == "Late" or d["status"] == "Half Day" or d["status"] == "Permission" or d["status"] == "Grace Time" or d["status"] == "Half Day Leave" or d["status"] == "Work From Home present")
    total_leave = sum(1 for d in calendar_days if d["status"] == "Leave" or d["status"] == "Comp-Off Leave" or d["status"] == "Comp-Off")
    total_holiday = sum(1 for d in calendar_days if d["status"] == "Holiday")
    total_weekly_off = sum(1 for d in calendar_days if d["status"] == "WeekOff" or d["status"] == "Weekly Off")
    total_absent = sum(1 for d in calendar_days if d["status"] == "Absent")

    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    return render(request, "myapp/myattendance.html", {
        "calendar_days": calendar_days,
        "calendar_grid": calendar_grid,
        "weekdays": weekdays,
        "total_present": total_present,
        "total_absent": total_absent,
        "total_leave": total_leave,
        "total_weekly_off": total_weekly_off,
        "total_holiday": total_holiday,
        "table_data": table_data,
        "pair_range": range(max_pairs),
        "total_working": fmt(total_working_seconds),
        "total_break": fmt(total_break_seconds),
        "total_columns": total_columns,
        "year": year,
        "month": month,
        "emp_name": request.session["emp_name"],


        "leaves_taken": leaves_taken,
        "leaves_remaining": leaves_remaining,
        "yearly_leave_limit": YEARLY_LEAVES_ALLOWED,


        "total_working": total_working,
        "total_break": total_break,
        "leaves": leaves,




        "chart_labels": chart_labels,
        "monthly_working_hours": monthly_working_hours,
        "monthly_break_hours": monthly_break_hours,
        "monthly_first_punch": monthly_first_punch,
        "avg_first_punch_time":avg_first_punch_time,
        "avg_working_hours": avg_working_hours,
        "avg_break_hours": avg_break_hours,
        "avg_first_punch": avg_first_punch,
        "present_days_count": present_days_count,

    })



SPOC_TEAM_MAP = {
    "MLV551": "Team Selvakumar",
    "MLV626": "Team Deepak",
    "MLV668": "Team Kathir",
    "MLV758": "Team Nandhini",
    "MLV787": "Team Pavithra",
    "MLV825": "Team Jegadeesan",
    "MLV829": "Team Nivedha",
    "MLV1186": "Team Naren",
    "MLV1196": "MLV/CHN/OPS",
    "MLV1150": "Team Suji",
    "MLV655": "Team Mythili",
    "MLV754": "Team Pravin",
}
@csrf_exempt
def leave_approve_reject(request, leave_id):
    if request.method != "POST":
        # If hit via GET, redirect to the dashboard where the manager can take action
        return redirect('reporting_team')

    manager = request.session.get("emp_code")
    if not manager:
        return JsonResponse({"success": False, "message": "Manager not logged in"})

    # Try JSON first
    try:
        data = json.loads(request.body)
        status = data.get("status", "").upper()
        remarks = data.get("remarks")
    except (json.JSONDecodeError, TypeError):
        # Fallback to form data
        status = request.POST.get("status", "").upper()
        remarks = request.POST.get("remarks")

    if status not in ("APPROVED", "REJECTED") or not remarks:
        return JsonResponse({"success": False, "message": "Missing or invalid data"})

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE LeaveRequests
                SET Status=%s, Remarks=%s, ApprovedBy=%s
                WHERE LeaveId=%s
            """, [status, remarks, manager, leave_id])
            
            # Notify Employee
            cursor.execute("SELECT EmployeeCode FROM LeaveRequests WHERE LeaveId = %s", [leave_id])
            row = cursor.fetchone()
            if row:
                emp_code_notify = row[0]
                cursor.execute("""
                    INSERT INTO Notifications
                    (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
                    VALUES (%s, %s, %s, %s, 'LEAVE_RESPONSE', %s)
                """, [
                    manager,
                    emp_code_notify,
                    f"Leave {status.capitalize()}",
                    f"Your leave request has been {status.lower()}",
                    leave_id
                ])
                
        connection.commit()
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({
        "success": True,
        "status": status,
        "message": f"Leave {status.lower()} successfully"
    })
@csrf_exempt

def export_leaves_excel(request):
    import io
    import pandas as pd
    from django.http import HttpResponse
    from datetime import datetime

    emp_code = request.session.get("emp_code")
    if not emp_code:
        return redirect("emp_login")

    # Re-use the hierarchy logic
    hierarchy_sql = """
        WITH TeamHierarchy AS (
            SELECT EmployeeCode, EmployeeName, Team
            FROM Employees
            WHERE Team = %s

            UNION ALL

            SELECT e.EmployeeCode, e.EmployeeName, e.Team
            FROM Employees e
            JOIN TeamHierarchy th ON e.Team = th.EmployeeCode
        )
        SELECT EmployeeCode FROM TeamHierarchy
        OPTION (MAXRECURSION 100)
    """
    with connection.cursor() as cursor:
        cursor.execute(hierarchy_sql, [emp_code])
        team_emp_list = [r[0] for r in cursor.fetchall()]

    if not team_emp_list:
        team_emp_list = ["__NONE__"]

    placeholders = ",".join(["%s"] * len(team_emp_list))

    leave_sql = f"""
        SELECT 
            lr.EmployeeCode, 
            e.EmployeeName, 
            lr.FromDate, 
            lr.ToDate, 
            lr.LeaveType, 
            lr.Reason, 
            lr.PermissionHours, 
            lr.Remarks, 
            lr.Status,
            tl.EmployeeName AS TL_Name,
            e.Team AS TL_EmployeeCode
        FROM LeaveRequests lr
        JOIN Employees e ON e.EmployeeCode = lr.EmployeeCode
        LEFT JOIN Employees tl ON tl.EmployeeCode = e.Team
        WHERE lr.EmployeeCode IN ({placeholders})
        ORDER BY lr.FromDate DESC
    """

    with connection.cursor() as cursor:
        cursor.execute(leave_sql, team_emp_list)
        rows = cursor.fetchall()

    df = pd.DataFrame(rows, columns=[
        "Employee Code", "Employee Name", "From Date", "To Date", 
        "Leave Type", "Leave Reason", "Permission Hrs", "Remarks", "Status",
        "TL Name", "TL Employee Id"
    ])

    # Format dates
    for col in ["From Date", "To Date"]:
        df[col] = pd.to_datetime(df[col]).dt.strftime('%d-%b-%Y')

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for status_code in ["PENDING", "APPROVED", "REJECTED"]:
            subset = df[df["Status"] == status_code].copy()
            # Drop status column from the sheet since it's implied by the sheet name
            subset = subset.drop(columns=["Status"])
            sheet_title = status_code.capitalize()
            subset.to_excel(writer, index=False, sheet_name=sheet_title)
            
            # Optional: Basic column width adjustment
            if not subset.empty:
                worksheet = writer.sheets[sheet_title]
                for i, col in enumerate(subset.columns):
                    column_len = max(subset[col].astype(str).str.len().max(), len(col)) + 2
                    worksheet.set_column(i, i, min(column_len, 50))

    output.seek(0)
    response = HttpResponse(
        output.read(), 
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename=Leave_Notifications_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return response

def reporting_team_dashboard(request):
    emp_code = request.session.get("emp_code")
    if not emp_code:
        return redirect("emp_login")

    status = request.GET.get("status", "PENDING")

    selected_date_str = request.GET.get("date")
    selected_date = (
        datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        if selected_date_str
        else date.today()
    )
    selected_date_str = selected_date.strftime("%Y-%m-%d")

    # =========================
    # LEAVE CYCLE (26 → 25)
    # =========================
    if selected_date.day >= 26:
        cycle_start = date(selected_date.year, selected_date.month, 26)
        cycle_end = date(
            selected_date.year + (selected_date.month == 12),
            1 if selected_date.month == 12 else selected_date.month + 1,
            25,
        )
    else:
        cycle_start = date(
            selected_date.year - (selected_date.month == 1),
            12 if selected_date.month == 1 else selected_date.month - 1,
            26,
        )
        cycle_end = date(selected_date.year, selected_date.month, 25)

    # =========================
    # TEAM HIERARCHY + ATTENDANCE
    # =========================
    attendance_sql = """
        WITH TeamHierarchy AS (
            SELECT EmployeeCode, EmployeeName, Team
            FROM Employees
            WHERE Team = %s

            UNION ALL

            SELECT e.EmployeeCode, e.EmployeeName, e.Team
            FROM Employees e
            JOIN TeamHierarchy th ON e.Team = th.EmployeeCode
        )
        SELECT
            th.EmployeeCode,
            th.EmployeeName,
            th.Team,
            CASE WHEN dl.UserId IS NULL THEN 'ABSENT' ELSE 'PRESENT' END
        FROM TeamHierarchy th
        LEFT JOIN (
            SELECT DISTINCT UserId
            FROM (
                SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
            ) x
            WHERE CAST(LogDate AS DATE) = %s
        ) dl ON dl.UserId = th.EmployeeCode
        WHERE th.EmployeeName NOT LIKE %s
        OPTION (MAXRECURSION 100)
    """

    with connection.cursor() as cursor:
        cursor.execute(attendance_sql, [emp_code, selected_date, "del_%"])
        rows = cursor.fetchall()
    
    punch_status_map = {}
    team_data = defaultdict(list)

    for emp_code_, name, team, status_ in rows:
        team_data[team or emp_code].append({
            "emp_code": emp_code_,
            "emp_name": name,
            "status": status_, 
        })

    team_emp_set = {r[0] for r in rows}
    team_emp_list = list(team_emp_set) or ["__NONE__"]
    emp_placeholders = ",".join(["%s"] * len(team_emp_list))

    # =========================
    # FETCH ALL HOLIDAYS FOR CYCLE (for sandwich check)
    # =========================
    company_hols = {}
    emp_hols = defaultdict(dict)
    with connection.cursor() as cursor:
        cursor.execute("SELECT Date, DayType FROM CompanyHolidayWeekOff WHERE Date BETWEEN %s AND %s", [cycle_start, cycle_end])
        company_hols = {r[0]: r[1] for r in cursor.fetchall()}

        cursor.execute(f"SELECT EmployeeCode, Date, DayType FROM EmployeeHolidayWeekOff WHERE EmployeeCode IN ({emp_placeholders}) AND Date BETWEEN %s AND %s", [*team_emp_list, cycle_start, cycle_end])
        for ec, dt, dtype in cursor.fetchall():
            emp_hols[ec][dt] = dtype

    # =========================
    # FETCH ALL LEAVES FOR CYCLE (for sandwich check)
    # =========================
    cycle_leaves = defaultdict(dict)  # ec -> date -> type
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT EmployeeCode, FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE Status='APPROVED'
            AND EmployeeCode IN ({emp_placeholders})
            AND (
                (FromDate BETWEEN %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [*team_emp_list, cycle_start, cycle_end, cycle_start, cycle_end, cycle_start, cycle_end])
        for ec, f, t, lt in cursor.fetchall():
            curr = f.date() if isinstance(f, datetime) else f
            last = t.date() if isinstance(t, datetime) else t
            while curr <= last:
                if cycle_start <= curr <= cycle_end:
                    cycle_leaves[ec][curr] = lt
                curr += timedelta(days=1)

    # =========================
    # FETCH ALL LOG DAYS FOR CYCLE
    # =========================
    cycle_punches = defaultdict(set)
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT UserId, CAST(LogDate AS DATE)
            FROM (
                SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
            ) x
            WHERE CAST(LogDate AS DATE) BETWEEN %s AND %s
            AND UserId IN ({emp_placeholders})
        """, [cycle_start, cycle_end, *team_emp_list])
        for ec, dt in cursor.fetchall():
            cycle_punches[ec].add(dt)

    # =========================
    # TEAM NAMES
    # =========================
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode IN ({emp_placeholders})",
            team_emp_list
        )
        team_name_map = dict(cursor.fetchall())

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                lr.LeaveId,
                lr.EmployeeCode,
                e.EmployeeName,
                lr.FromDate,
                lr.ToDate,
                lr.LeaveType,
                lr.Remarks,
                lr.Status,
                lr.PermissionHours,
                lr.Reason
            FROM LeaveRequests lr
            JOIN Employees e ON e.EmployeeCode = lr.EmployeeCode
            WHERE lr.EmployeeCode IN ({emp_placeholders})
            AND lr.Status = %s
            ORDER BY lr.FromDate DESC
        """, [*team_emp_list, status])

        leave_rows = cursor.fetchall()
    leaves = []

    for r in leave_rows:
        leaves.append({
            "leave_id": r[0],
            "employee": f"{r[2]} ({r[1]})",
            "from": r[3],
            "to": r[4],
            "type": r[5],
            "remarks": r[6],
            "status": r[7],
            "permission":r[8],
            "reason":r[9]
        })

    # =========================
    # APPROVED LEAVES (DAY)
    # =========================
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT DISTINCT EmployeeCode
            FROM LeaveRequests
            WHERE Status='APPROVED'
            AND LeaveType IN (
                'Informed Leave',
                'Casual Leave','Sick Leave','Optional Leave'
              )
            AND %s BETWEEN FromDate AND ToDate
            AND EmployeeCode IN ({emp_placeholders})
        """, [selected_date, *team_emp_list])
        leave_emp_set = {r[0] for r in cursor.fetchall()}

    # =========================
    # OVERALL STATS
    # =========================
    total_count = len(team_emp_set)
    total_present = sum(r[3] == "PRESENT" for r in rows)
    total_on_leave = len(leave_emp_set)
    total_absent = total_count - total_present - total_on_leave
    shrinkage = round((total_absent / total_count) * 100, 2) if total_count else 0

    DEFAULT_SHIFT_END = time(18, 30)
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                esa.EmployeeCode,
                s.BeginTime,
                s.EndTime
            FROM EmployeeShiftAllocation esa
            JOIN Shifts s ON s.ShiftId = esa.ShiftId
            WHERE %s BETWEEN esa.FromDate AND ISNULL(esa.ToDate, '9999-12-31')
            AND esa.EmployeeCode IN ({emp_placeholders})
        """, [selected_date, *team_emp_list])

        shift_map_raw = cursor.fetchall()

    shift_start_map = {}
    shift_end_map = {}

    for emp_code_, begin_time, end_time in shift_map_raw:
        if begin_time:
            if isinstance(begin_time, time):
                shift_start_map[emp_code_] = begin_time
            else:
                shift_start_map[emp_code_] = datetime.strptime(begin_time, "%H:%M").time()
        if end_time:
            if isinstance(end_time, time):
                shift_end_map[emp_code_] = end_time
            else:
                shift_end_map[emp_code_] = datetime.strptime(end_time, "%H:%M").time()




    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT UserId, MIN(LogDate)
            FROM (
                SELECT * FROM dbo.DeviceLogs_12_2025
                UNION ALL
                SELECT * FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT * FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT * FROM dbo.DeviceLogs_3_2026
            ) x
            WHERE CAST(LogDate AS DATE) = %s
            AND UserId IN ({emp_placeholders})
            GROUP BY UserId
        """, [selected_date, *team_emp_list])

        first_punch_map = dict(cursor.fetchall())

    # =========================
    # FIRST PUNCH
    # =========================
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT UserId, MAX(LogDate)
            FROM (
                SELECT * FROM dbo.DeviceLogs_12_2025
                UNION ALL
                SELECT * FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT * FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT * FROM dbo.DeviceLogs_3_2026
            ) x
            WHERE CAST(LogDate AS DATE) = %s
            AND UserId IN ({emp_placeholders})
            GROUP BY UserId
        """, [selected_date, *team_emp_list])

        last_punch_map = dict(cursor.fetchall())
    login_status_map = {}
    logout_status_map = {}
    # =========================
    # LATE CALCULATION
    # =========================
    EARLY_GRACE_MINUTES = 1
    LATE_GRACE_MINUTES = 1

    early_going = 0
    on_time = late_in = not_yet_in = 0

    for emp in team_emp_set:
        if emp in leave_emp_set:
            login_status_map[emp] = "ON_LEAVE"
            continue

        punch_in = first_punch_map.get(emp)
        punch_out = last_punch_map.get(emp)

        def is_absent(d, ec):
            if d < cycle_start or d > cycle_end: return False
            return d not in cycle_punches[ec] and d not in cycle_leaves[ec] and d not in company_hols and d not in emp_hols[ec]

        if not punch_in:
            if emp in leave_emp_set:
                 login_status_map[emp] = "ON_LEAVE"
            else:
                 # Check if Holiday and Sandwich
                 emp_rest_days = set(company_hols.keys()).union(set(emp_hols[emp].keys()))
                 if selected_date in emp_rest_days:
                     prev_wd = selected_date - timedelta(days=1)
                     while prev_wd >= cycle_start and prev_wd in emp_rest_days:
                         prev_wd -= timedelta(days=1)
                     
                     next_wd = selected_date + timedelta(days=1)
                     while next_wd <= cycle_end and next_wd in emp_rest_days:
                         next_wd += timedelta(days=1)
                     
                     if is_absent(prev_wd, emp) and is_absent(next_wd, emp):
                         login_status_map[emp] = "ABSENT" # Sandwiched
                         not_yet_in += 1 # Count as absent/not yet in
                     else:
                         login_status_map[emp] = company_hols.get(selected_date) or emp_hols[emp].get(selected_date) or "HOLIDAY"
                 else:
                     not_yet_in += 1
                     login_status_map[emp] = "NOT_YET_IN"
            continue

        shift_start_time = shift_start_map.get(emp, time(9, 0))
        shift_end_time = shift_end_map.get(emp, DEFAULT_SHIFT_END)

        shift_start_dt = datetime.combine(selected_date, shift_start_time)
        shift_end_dt = datetime.combine(selected_date, shift_end_time)

        # -------- LOGIN STATUS --------
        if punch_in > shift_start_dt + timedelta(minutes=LATE_GRACE_MINUTES):
            late_in += 1
            login_status_map[emp] = "LATE_IN"
        else:
            on_time += 1
            login_status_map[emp] = "ON_TIME"

        # -------- LOGOUT STATUS --------
        is_permission = cycle_leaves.get(emp, {}).get(selected_date) == "Permission"
        if punch_out and punch_out < shift_end_dt - timedelta(minutes=EARLY_GRACE_MINUTES) and not is_permission:
            early_going += 1
            logout_status_map[emp] = "EARLY_GOING"
        elif is_permission:
            logout_status_map[emp] = "PERMISSION"
        else:
            logout_status_map[emp] = "HOURS_MET"


    on_leave = len(leave_emp_set)
    going = early_going
    final_teams = []
    for team_code, members in team_data.items():
        present = 0

        for m in members:
            m["punch_status"] = login_status_map.get(m["emp_code"])
            m["logout_status"] = logout_status_map.get(m["emp_code"])


            if m["status"] == "PRESENT":
                present += 1
        final_teams.append({
            "early_going": early_going,
            "team_code": team_code,
            "team_name": team_name_map.get(team_code, team_code),
            "members": members,
            "total": len(members),
            "present": present,
            "absent": len(members) - present,
        })

    return render(request, "myapp/reporting_team.html", {
        "going":going,
        "teams": final_teams,
        "total_count": total_count,
        "total_present": total_present,
        "total_absent": total_absent,
        "shrinkage": shrinkage,
        "selected_date": selected_date_str,
        "cycle_start": cycle_start,
        "cycle_end": cycle_end,
        "current_status": status,
        "on_time": on_time,
        "late_in": late_in,
        "not_yet_in": not_yet_in,
        "on_leave": on_leave,
        "who_total": on_time + late_in + not_yet_in + on_leave,
        "leaves": leaves,
    })
def spoc_employee_dashboard(request, target_emp_code):
    # Logged-in SPOC check
    spoc_code = request.session.get("emp_code")
    if not spoc_code:
        return redirect("emp_login")

    # ✅ No team filtering — any SPOC can view any employee
    return employee_dashboard_core(
        request=request,
        emp_code=target_emp_code,
        read_only=True
    )
def safe_month_redirect(month, year, emp_code, read_only):
    if read_only:
        return redirect(
            reverse("spoc_employee_dashboard", args=[emp_code]) +
            f"?month={month}&year={year}"
        )
    return redirect(f"?month={month}&year={year}")
def employee_dashboard_core(request, emp_code, read_only=False):
    # (Use YOUR EXISTING dashboard SQL here)
    # Replace session emp_code with passed emp_code

    if not request.session.get("emp_code"):
        return redirect("emp_login")

    if "year" in request.GET and "month" in request.GET:
        year = int(request.GET["year"])
        month = int(request.GET["month"])
    else:
        year, month = get_leave_month(date.today())

    if month > 12:
        return safe_month_redirect(1, year + 1, emp_code, read_only)

    if month < 1:
        return safe_month_redirect(12, year - 1, emp_code, read_only)


    start_date = date(year - 1, 12, 26) if month == 1 else date(year, month - 1, 26)
    end_date = date(year, month, 25)
    # =====================================================
    # 🔹 FETCH EMPLOYEE SHIFT ALLOCATION (DATE-WISE)
    # =====================================================
    employee_shifts = {}   # date -> shift_start_time

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                a.FromDate,
                ISNULL(a.ToDate, '2099-12-31'),
                s.BeginTime
            FROM EmployeeShiftAllocation a
            JOIN Shifts s ON s.ShiftId = a.ShiftId
            WHERE a.EmployeeCode = %s
            AND (
                    a.FromDate BETWEEN %s AND %s
                OR a.ToDate BETWEEN %s AND %s
                OR (a.FromDate <= %s AND a.ToDate >= %s)
            )
        """, [
            emp_code,
            start_date, end_date,
            start_date, end_date,
            start_date, end_date
        ])

        for fs, fe, begin_time in cursor.fetchall():
            fs = fs if isinstance(fs, date) else fs.date()
            fe = fe if isinstance(fe, date) else fe.date()

            d = fs
            while d <= fe:
                employee_shifts[d] = begin_time
                d += timedelta(days=1)

    
    # =====================================================
    # 1️⃣ FETCH HOLIDAYS & EMPLOYEE OVERRIDES
    # =====================================================
    holidays = {}  # date -> DayType (Holiday / WeekOff)

    with connection.cursor() as cursor:
        # 1a. Company holidays
        cursor.execute("""
            SELECT Date, DayType
            FROM CompanyHolidayWeekOff
            WHERE Date BETWEEN %s AND %s
        """, [start_date, end_date])
        for dt, day_type in cursor.fetchall():
            holidays[dt] = day_type

        # 1b. Employee-specific overrides
        cursor.execute("""
            SELECT Date, DayType
            FROM EmployeeHolidayWeekOff
            WHERE EmployeeCode = %s
            AND Date BETWEEN %s AND %s
        """, [emp_code, start_date, end_date])
        for dt, day_type in cursor.fetchall():
            holidays[dt] = day_type  # override company

    # =====================================================
    # 2️⃣ FETCH APPROVED LEAVES
    # =====================================================
    leaves = {}

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND (
                    (FromDate BETWEEN   %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [emp_code, start_date, end_date, start_date, end_date, start_date, end_date])
        
        for fs, fe, lt in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe
            d = fs
            while d <= fe:
                leaves[d] = lt
                d += timedelta(days=1)
    # =====================================================
    # 2️⃣ FETCH DEVICE LOGS
    # =====================================================
    punch_sql = """
        SELECT CAST(LogDate AS DATE) AS LogDay, LogDate
        FROM (
            SELECT * FROM dbo.DeviceLogs_3_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_2_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_1_2026
            UNION ALL
            SELECT * FROM dbo.DeviceLogs_12_2025
        ) dl
        WHERE UserId = %s
          AND CAST(LogDate AS DATE) BETWEEN %s AND %s
        ORDER BY LogDay, LogDate
    """

    with connection.cursor() as cursor:
        cursor.execute(punch_sql, [emp_code, start_date, end_date])
        punch_rows = cursor.fetchall()

    daily_logs = defaultdict(list)
    device_days = set()
    


    for day, log_time in punch_rows:
        daily_logs[day].append(log_time)
        device_days.add(day)
    
    # =====================================================
    # 3️⃣ FETCH MANUAL PUNCHES
    # =====================================================
    manual_sql = """
        SELECT PunchDate, PunchTime
        FROM ManualPunches
        WHERE EmployeeCode = %s
          AND PunchDate BETWEEN %s AND %s
        ORDER BY PunchDate, PunchTime
    """

    with connection.cursor() as cursor:
        cursor.execute(manual_sql, [emp_code, start_date, end_date])
        manual_rows = cursor.fetchall()
    manual_logs = defaultdict(list)
    for pdate, ptime in manual_rows:
        if isinstance(pdate, datetime):
            pdate = pdate.date()

        if isinstance(ptime, time):
            ptime = datetime.combine(pdate, ptime)

        manual_logs[pdate].append(ptime)

    manual_days = set(manual_logs.keys())


    # =====================================================
    # 4️⃣ MERGE MANUAL → DAILY LOGS (DEVICE PRIORITY)
    # =====================================================
    for day, logs in manual_logs.items():
        if day not in daily_logs or not daily_logs[day]:
            daily_logs[day] = logs
    manual_days = set(manual_logs.keys()) 
    
    # =====================================================
    # 2a️⃣ FETCH APPROVED GRACE TIME / PERMISSION
    # =====================================================
    daily_permissions = {}  # key=date, value=leave_type

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate, LeaveType
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND LeaveType IN ('Grace Time', 'Permission', 'Half Day', 'Comp-Off', 'Work From Home')
            AND (
                    (FromDate BETWEEN %s AND %s)
                OR  (ToDate BETWEEN %s AND %s)
                OR  (FromDate <= %s AND ToDate >= %s)
            )
        """, [emp_code, start_date, end_date, start_date, end_date, start_date, end_date])

        for fs, fe, lt in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe
            d = fs
            while d <= fe:
                daily_permissions[d] = lt
                d += timedelta(days=1)

    # =====================================================
    # 5️⃣ BUILD ATTENDANCE (WITH SOURCE + LATE FLAG)
    # =====================================================
    attendance = {}
    daily_totals = {}

    for day, logs in daily_logs.items():
        if len(logs) < 1:  # ✅ allow at least 1 punch
            continue

        logs.sort()
        punch_in = logs[0]
        punch_out = logs[-1] if len(logs) > 1 else None

        working_sec = 0
        break_sec = 0

        # calculate working/break only if there are at least 2 punches
        if len(logs) > 1:
            for i in range(len(logs) - 1):
                diff = (logs[i + 1] - logs[i]).total_seconds()
                if i % 2 == 0:
                    working_sec += diff
                else:
                    break_sec += diff

        # Check if late
        # Check if late or half day
        SHIFT_HOURS_REQUIRED = 9 * 3600 
        shift_start = employee_shifts.get(day)
        if not shift_start:
            shift_start = time(9, 0)

        if isinstance(shift_start, str):
            shift_start = datetime.strptime(shift_start, "%H:%M").time()
        
        shift_start_dt = datetime.combine(day, shift_start)

        present_limit_dt = shift_start_dt + timedelta(minutes=0)
        late_limit_dt = shift_start_dt + timedelta(minutes=1)

        if punch_in <= present_limit_dt:
            status = "Present full day"
            late_flag = False
            late_duration_sec = 0

        elif punch_in <= late_limit_dt:
            status = "Late"
            late_flag = True
            late_duration_sec = (punch_in - present_limit_dt).total_seconds()

        else:
            status = "Half Day"
            late_flag = True
            late_duration_sec = (punch_in - late_limit_dt).total_seconds()
        shift_start_dt = datetime.combine(day, shift_start)
        shift_end_dt = shift_start_dt + timedelta(seconds=SHIFT_HOURS_REQUIRED)

        # Determine Early Going (only for present full day / late / half day)
        early_going_flag = False
        if punch_out is not None and punch_out < shift_end_dt and status in ["Present full day", "Late", "Half Day"]:
            early_going_flag = True
        attendance[day] = {
            "punch_in": punch_in,
            "punch_out": punch_out,
            "status": status,
            "source": "device" if day in device_days else "manual",
            "late": late_flag,
            "late_duration": fmt(late_duration_sec),
            "working": fmt(working_sec),
            "break": fmt(break_sec),
            "early_going": early_going_flag,
        }


        daily_totals[day] = (working_sec, break_sec)


    # =====================================================
    # 6️⃣ DAILY WORKING & BREAK SUMMARY
    # =====================================================
    daily_summary = {}

    for day, logs in daily_logs.items():
        working = 0
        breaking = 0

        for i in range(len(logs) - 1):
            diff = (logs[i + 1] - logs[i]).total_seconds()
            if i % 2 == 0:
                working += diff
            else:
                breaking += diff

        daily_summary[day] = {
            "working": fmt(working),
            "break": fmt(breaking),
        }
    # =====================================================
    # 5a️⃣ MERGE APPROVED GRACE TIME / PERMISSION INTO ATTENDANCE
    # =====================================================
    # =====================================================
    # 5a️⃣ APPLY APPROVED GRACE TIME / PERMISSION (OVERRIDE STATUS)
    # =====================================================
    for day, entry in attendance.items():
        if day not in daily_permissions:
            continue

        leave_type = daily_permissions[day]
        entry["leave_type"] = leave_type
        entry["APPROVED"] = True

        # Grace Time overrides LATE
        if leave_type == "Grace Time" and entry["status"] == "Late":
            entry["status"] = "Grace Time"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        # Permission overrides HALF DAY
        elif leave_type == "Permission":
            if entry["status"] == "Half Day":
                entry["status"] = "Permission"
                entry["late"] = False
                entry["late_duration"] = "00:00"
            entry["early_going"] = False

        elif leave_type == "Half Day":
            entry["status"] = "Half Day Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        elif leave_type == "Work From Home":
            entry["status"] = "Work From Home present"
            entry["late"] = False
            entry["late_duration"] = "00:00"

        elif leave_type == "Comp-Off":
            entry["status"] = "Comp-Off Leave"
            entry["late"] = False
            entry["late_duration"] = "00:00"

    # =====================================================
    # 3️⃣ BUILD CALENDAR DAYS WITH HOLIDAY/LEAVE LOGIC
    # =====================================================
    # =====================================================
    # REST DAYS & ABSENCE HELPER (Sandwich Logic)
    # =====================================================
    emp_rest_days = set(holidays.keys())
    d_sun = start_date
    while d_sun <= end_date:
        if d_sun.weekday() == 6:
            emp_rest_days.add(d_sun)
        d_sun += timedelta(days=1)

    def is_absent(d):
        if d < start_date or d > end_date:
            return False
        # Absent if: No punch AND No approved leave AND Not a rest day
        return d not in attendance and d not in leaves and d not in emp_rest_days

    calendar_days = []
    total_days = (end_date - start_date).days + 1
    current_date = start_date

    while current_date <= end_date:
        weekday = current_date.weekday()
        day_info = {"date": current_date, "break": "00:00"}
        shift_start = employee_shifts.get(current_date)
        if shift_start:
            day_info["shift_start"] = shift_start

        
        if current_date in attendance:
            day_info.update(attendance[current_date])
            if (
                day_info.get("leave_type") in ["Grace Time", "Permission"]
                and day_info.get("APPROVED") is True
            ):
                day_info["late"] = False
        elif current_date in leaves:
            day_info.update({
                "status": "Leave",
                "leave_type": leaves[current_date],
            })
        elif current_date in holidays or weekday == 6:
            # -------------------------------
            # Apply Sandwich Logic
            # -------------------------------
            prev_wd = current_date - timedelta(days=1)
            while prev_wd >= start_date and prev_wd in emp_rest_days:
                prev_wd -= timedelta(days=1)
            
            next_wd = current_date + timedelta(days=1)
            while next_wd <= end_date and next_wd in emp_rest_days:
                next_wd += timedelta(days=1)
            
            if is_absent(prev_wd) and is_absent(next_wd):
                day_info.update({"status": "Absent", "break": "00:00"})
            else:
                day_info.update({
                    "status": holidays.get(current_date, "Weekly Off"),
                    "break": "00:00"
                })
        else:
            day_info.update({"status": "Absent", "break": "00:00"})

        calendar_days.append(day_info)
        current_date += timedelta(days=1)
        
    # =====================================================
    # 8️⃣ CALENDAR GRID
    # =====================================================
    first_weekday = start_date.weekday()
    calendar_grid = [None] * first_weekday + calendar_days

    while len(calendar_grid) % 7 != 0:
        calendar_grid.append(None)


    total_working_seconds = sum(v[0] for v in daily_totals.values())
    total_break_seconds = sum(v[1] for v in daily_totals.values())

    total_working = fmt(total_working_seconds)
    total_break = fmt(total_break_seconds)

    # =====================================================
    # 9️⃣ MONTHLY TABLE DATA
    # =====================================================
    table_data = []
    max_pairs = 0
    total_working_seconds = 0
    total_break_seconds = 0

    for day, logs in daily_logs.items():
        pairs = []
        working = 0
        breaking = 0

        for i in range(0, len(logs), 2):
            in_time = logs[i]
            out_time = logs[i + 1] if i + 1 < len(logs) else None
            pairs.append((in_time, out_time))

        for i in range(len(logs) - 1):
            diff = (logs[i + 1] - logs[i]).total_seconds()
            if i % 2 == 0:
                working += diff
            else:
                breaking += diff

        max_pairs = max(max_pairs, len(pairs))
        total_working_seconds += working
        total_break_seconds += breaking

        table_data.append({
            "date": day,
            "pairs": pairs,
            "working": fmt(working),
            "break": fmt(breaking),
        })

    total_columns = (max_pairs * 2) + 1
    # =====================================================
    # 🔹 YEARLY LEAVE COUNT (CYCLE-AWARE: Dec 26 - Dec 25)
    # =====================================================
    YEARLY_LEAVES_ALLOWED = 18
    LEAVE_TYPES = ("Casual Leave", "Sick Leave", "Optional Leave")
    yearly_leave_days = set()

    # Cycle-aware year start (Dec 26 of prev year)
    cycle_year_start = date(year - 1, 12, 26)
    cycle_year_end = date(year, 12, 25)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode = %s
            AND Status = 'APPROVED'
            AND LeaveType IN (%s, %s, %s)
            AND (
                    (FromDate BETWEEN %s AND %s)
                OR (ToDate BETWEEN %s AND %s)
                OR (FromDate <= %s AND ToDate >= %s)
            )
        """, [
            emp_code,
            *LEAVE_TYPES,
            cycle_year_start, cycle_year_end,
            cycle_year_start, cycle_year_end,
            cycle_year_start, cycle_year_end
        ])

        for fs, fe in cursor.fetchall():
            fs = fs.date() if isinstance(fs, datetime) else fs
            fe = fe.date() if isinstance(fe, datetime) else fe

            # clip to selected cycle year
            start = max(fs, cycle_year_start)
            end = min(fe, cycle_year_end)

            d = start
            while d <= end:
                yearly_leave_days.add(d)
                d += timedelta(days=1)

    # ✅ FINAL REQUIRED VALUES
    leaves_taken = len(yearly_leave_days)
    leaves_remaining = max(0, YEARLY_LEAVES_ALLOWED - leaves_taken)
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                lr.LeaveId,
                lr.EmployeeCode,
                e.EmployeeName,
                lr.FromDate,
                lr.ToDate,
                lr.LeaveType,
                lr.Status,
                lr.Remarks
            FROM LeaveRequests lr
            JOIN Employees e ON e.EmployeeCode = lr.EmployeeCode
            WHERE lr.EmployeeCode = %s
        """, [emp_code])

        rows = cursor.fetchall()


    leaves = [{
        "leave_id": r[0],
        "employee": f"{r[1]} - {r[2]}",
        "from": r[3],
        "to": r[4],
        "type": r[5],
        "status": r[6],
        "remarks": r[7],
    } for r in rows]
    # =====================================================
    # 🔟 TOTALS
    # =====================================================
    total_present = sum(1 for d in calendar_days if d["status"] == "Present full day" or d["status"] == "Late" or d["status"] == "Half Day" or d["status"] == "Permission" or d["status"] == "Grace Time" or d["status"] == "Half Day Leave" or d["status"] == "Work From Home present")
    total_leave = sum(1 for d in calendar_days if d["status"] == "Leave" or d["status"] == "Comp-Off Leave" or d["status"] == "Comp-Off")
    total_holiday = sum(1 for d in calendar_days if d["status"] == "Holiday")
    total_weekly_off = sum(1 for d in calendar_days if d["status"] == "WeekOff" or d["status"] == "Weekly Off")
    total_absent = sum(1 for d in calendar_days if d["status"] == "Absent")

    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EmployeeName
            FROM Employees
            WHERE EmployeeCode = %s
        """, [emp_code])
        row = cursor.fetchone()

    employee_name = row[0] if row else "Unknown Employee"
    month_nav_url = (
        reverse("spoc_employee_dashboard", args=[emp_code])
        if read_only
        else reverse("dashboard")
    )
    return render(request, "myapp/myattendance.html", {
        "calendar_days": calendar_days,
        "calendar_grid": calendar_grid,
        "weekdays": weekdays,
        "total_present": total_present,
        "total_absent": total_absent,
        "total_leave": total_leave,
        "total_weekly_off": total_weekly_off,
        "total_holiday": total_holiday,
        "table_data": table_data,
        "pair_range": range(max_pairs),
        "total_working": fmt(total_working_seconds),
        "total_break": fmt(total_break_seconds),
        "total_columns": total_columns,
        "year": year,
        "month": month,
        "emp_name": request.session["emp_name"],
        "employee_name": employee_name,              # viewed employee
        "viewer_name": request.session["emp_name"],
        "read_only": read_only,
        "viewing_emp_code": emp_code,


        "leaves_taken": leaves_taken,
        "leaves_remaining": leaves_remaining,
        "yearly_leave_limit": YEARLY_LEAVES_ALLOWED,


        "total_working": total_working,
        "total_break": total_break,
        "leaves": leaves,
        "month_nav_url": month_nav_url,

    })

def raise_leave_request(emp_code, from_date, to_date, reason, leave_type):

    try:
        with connection.cursor() as cursor:
            # Insert leave request
            cursor.execute("""
                INSERT INTO LeaveRequests
                (EmployeeCode, FromDate, ToDate, Reason, Status, LeaveType)
                OUTPUT INSERTED.LeaveId
                VALUES (%s, %s, %s, %s, 'PENDING', %s)
            """, [emp_code, from_date, to_date, reason, leave_type])

            leave_id = cursor.fetchone()[0]

            # 🔹 Get manager from Employees.Team
            cursor.execute("""
                SELECT Team FROM Employees WHERE EmployeeCode = %s
            """, [emp_code])

            row = cursor.fetchone()
            if not row or not row[0]:
                raise Exception("Manager not found")

            manager_code = row[0]

            # Insert notification
            cursor.execute("""
                INSERT INTO Notifications
                (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
                VALUES (%s, %s, %s, %s, 'LEAVE', %s)
            """, [
                emp_code,
                manager_code,
                "New Leave Request",
                f"{emp_code} applied leave from {from_date} to {to_date} ({leave_type})",
                leave_id
            ])

        connection.commit()

    except Exception as e:
        raise e

def update_leave_status(leave_id, status, manager_code):
    with connection.cursor() as cursor:
        # Update the leave request status
        cursor.execute("""
            UPDATE LeaveRequests
            SET Status = %s
            WHERE LeaveId = %s
        """, [status, leave_id])

        # Get the employee code from the leave request
        cursor.execute("""
            SELECT EmployeeCode FROM LeaveRequests WHERE LeaveId = %s
        """, [leave_id])

        emp_code = cursor.fetchone()[0]

        # Insert notification into the Notifications table
        cursor.execute("""
            INSERT INTO Notifications
            (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
            VALUES (%s, %s, %s, %s, 'LEAVE_RESPONSE', %s)
        """, [
            manager_code,
            emp_code,
            "Leave Request Update",
            f"{emp_code} leave request has been {status}",
            leave_id
        ])

    # Commit the transaction
    connection.commit()

def send_notification(sender_code, receiver_code, title, message, n_type, related_id=None):
    """Utility to insert notification via raw SQL"""
    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO Notifications
            (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [
            sender_code,
            receiver_code,
            title,
            message,
            n_type,
            related_id
        ])
    connection.commit()

def notifications(request):
    emp_code = request.session.get("emp_code")
    if not emp_code:
        return redirect("emp_login")

    sql = """
        SELECT NotificationID, Title, Message, Type, RelatedId, IsRead, CreatedAt
        FROM Notifications
        WHERE ReceiverEmpCode = %s
        ORDER BY CreatedAt DESC
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, [emp_code])
        rows = cursor.fetchall()

    notifications = []
    for r in rows:
        notifications.append({
            'NotificationID': r[0],
            'Title': r[1],
            'Message': r[2],
            'Type': r[3],
            'RelatedId': r[4],
            'IsRead': r[5],
            'CreatedAt': r[6]
        })

    return render(request, "myapp/notifications.html", {
        "notifications": notifications
    })

def notifications_api(request):
    emp_code = request.session.get("emp_code")
    notifications = []
    count = 0

    if emp_code:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM Notifications WHERE ReceiverEmpCode = %s AND IsRead = 0", [emp_code])
            count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Notifications WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'LEAVE'", [emp_code])
            unread_leave_req = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Notifications WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'LEAVE_RESPONSE'", [emp_code])
            unread_leave_resp = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Notifications WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'MANUAL_PUNCH'", [emp_code])
            unread_manual_req = cursor.fetchone()[0]

            cursor.execute("""
                SELECT TOP 5 NotificationID, Message, CreatedAt, IsRead, RelatedId, Type, Title
                FROM Notifications
                WHERE ReceiverEmpCode = %s
                ORDER BY CreatedAt DESC
            """, [emp_code])
            rows = cursor.fetchall()

        notifications = [{
            'id': r[0],
            'message': r[1],
            'created_at': r[2].strftime("%Y-%m-%d %H:%M") if r[2] else "",
            'is_read': r[3],
            'related_id': r[4],
            'type': r[5],
            'title': r[6]
        } for r in rows]

    return JsonResponse({
        'count': count,
        'unread_leave_req': unread_leave_req,
        'unread_leave_resp': unread_leave_resp,
        'unread_manual_req': unread_manual_req,
        'notifications': notifications
    })

def open_notification(request, notif_id):
    emp_code = request.session.get("emp_code")
    if not emp_code:
        return redirect("emp_login")

    with connection.cursor() as cursor:
        # Get notification details
        cursor.execute("""
            SELECT RelatedId, Type
            FROM Notifications
            WHERE NotificationID = %s AND ReceiverEmpCode = %s
        """, [notif_id, emp_code])
        row = cursor.fetchone()
        
        if not row:
            return redirect('notifications')

        related_id, n_type = row

        # Mark as read
        cursor.execute("""
            UPDATE Notifications
            SET IsRead = 1
            WHERE NotificationID = %s
        """, [notif_id])

    connection.commit()

    # Smart redirection
    if n_type == 'LEAVE':
        # Managers go to "My Team" to see pending leaves
        return redirect('reporting_team')
    elif n_type == 'LEAVE_RESPONSE':
        # Employees go to their own leave request/history page
        return redirect('leave_request')
    elif n_type == 'MANUAL_PUNCH':
        # Managers go to the manual punch approval page
        return redirect('team_head_approval')
    elif n_type == 'MANUAL_PUNCH_RESPONSE':
        # Employees go to their manual punch history page
        return redirect('manual_punch')
    elif n_type == 'HELPDESK':
        # Go to ticket list (or detail if we have it)
        return redirect('helpdesk_ticket_list')
    elif n_type == 'SURVEY':
        return redirect('pulse_survey_list')
    elif n_type == 'ANNOUNCEMENT':
        # Regular employees usually see them on dashboard or a specific list
        return redirect('dashboard')
    elif n_type == 'ASSET':
        return redirect('asset_list')
    elif n_type == 'EXPENSE':
        # If resolver, go to approval list, if owner, go to claim list
        role = request.session.get("allocate_position")
        if role in ['HR', 'IT', 'Finance'] or EmployeeReporting.objects.filter(ReportsToEmpCode=emp_code).exists():
             return redirect('expense_approval_list')
        return redirect('expense_list')
        
    return redirect('dashboard')

def mark_all_notifications_read(request):
    emp_code = request.session.get("emp_code")
    if emp_code:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE Notifications
                SET IsRead = 1
                WHERE ReceiverEmpCode = %s AND IsRead = 0
            """, [emp_code])
        connection.commit()
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': 'success'})
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))


# -----------------------------
# Updated leave_request view
# -----------------------------
def leave_request(request):
    emp_code = request.session.get("emp_code")
    if not emp_code:
        return redirect("emp_login")
    trainee = is_trainee(emp_code)
    # ===============================
    # Determine custom month
    # ===============================
    year = int(request.GET.get("year", date.today().year))
    month = int(request.GET.get("month", date.today().month))

    today = date.today()

    # 🔑 Shift month after 25th
    if today.day >= 26:
        month += 1
        if month > 12:
            month = 1
            year += 1

    start_date, end_date = get_custom_month_range(year, month)

    # ===============================
    # Ensure balances exist
    # ===============================
    today = date.today()
    ensure_quarter_balance(emp_code, today)
    ensure_monthly_balance(emp_code, today)

    quarter_balances = get_current_balances(emp_code, today)
    
    # Monthly balances adjusted for custom month range
    used_permission = get_used_permission_hours(emp_code, start_date, end_date)
    used_grace = get_used_monthly_leaves(emp_code, "Grace Time", start_date, end_date)

    monthly_balances = {
        #"Grace Time": max(MONTHLY_LEAVE_POLICY["Grace Time"] - used_grace, 0),
        "Permission": max(MONTHLY_LEAVE_POLICY["Permission"] - used_permission, 0)
    }

    balances = {**quarter_balances, **monthly_balances}

    # ===============================
    # FETCH LEAVE HISTORY
    # ===============================
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT LeaveId, FromDate, ToDate, LeaveType, Reason, Status, ApprovedBy
            FROM LeaveRequests
            WHERE EmployeeCode=%s
            ORDER BY FromDate DESC
        """, [emp_code])

        leave_requests = [{
            "LeaveId": r[0],
            "FromDate": r[1],
            "ToDate": r[2],
            "LeaveType": r[3],
            "Reason": r[4],
            "Status": r[5],
            "ApprovedBy": r[6]
        } for r in cursor.fetchall()]

    # ===============================
    # SUBMIT LEAVE
    # ===============================
    if request.method == "POST":
        from_date = date.fromisoformat(request.POST["from_date"])
        to_date = date.fromisoformat(request.POST["to_date"])
        leave_type = request.POST["leave_type"]
        reason = request.POST["reason"]

        if to_date < from_date:
            messages.error(request, "Invalid date range")
            return redirect("leave_request")

        # Validation for future payroll cycles (beyond the 25th of the current cycle)
        today_val = date.today()
        # get_cycle_year_month returns the month/year the current day belongs to (26th->next month)
        cy, cm = get_cycle_year_month(today_val)
        _, cycle_end_date = get_custom_month_range(cy, cm)
        
        if from_date > cycle_end_date or to_date > cycle_end_date:
            messages.error(request, "You can only apply for leave for current and previous payroll cycles.")
            return redirect("leave_request")

        days = (to_date - from_date).days + 1
        permission_hours = 0

        # -------------------------------
        # Permission validation
        # -------------------------------
        if leave_type == "Permission":
            permission_hours = int(request.POST.get("permission_hours", 0))

            if from_date != to_date:
                messages.error(request, "Permission must be applied for a single day")
                return redirect("leave_request")

            if permission_hours not in (1, 2):
                messages.error(request, "Invalid permission duration")
                return redirect("leave_request")

            remaining = monthly_balances["Permission"]
            if permission_hours > remaining:
                messages.error(request, "Permission limit exceeded for this month")
                return redirect("leave_request")

        # -------------------------------
        # Monthly leaves (Grace Time)
        # -------------------------------
        elif leave_type in monthly_balances:
            if days > 1:
                messages.error(request, f"{leave_type} can be applied only once per day")
                return redirect("leave_request")
            if monthly_balances[leave_type] <= 0:
                messages.error(request, f"{leave_type} limit exceeded this month")
                return redirect("leave_request")

        # -------------------------------
        # Quarterly leaves
        # -------------------------------
        if leave_type in quarter_balances:
            if trainee:
                messages.error(request, "Trainees are not eligible for quarterly leaves")
                return redirect("leave_request")
            if days > quarter_balances[leave_type]:
                messages.error(request, f"{leave_type} limit exceeded this quarter")
                return redirect("leave_request")

        # -------------------------------
        # Insert leave request
        # -------------------------------
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO LeaveRequests
                (EmployeeCode, FromDate, ToDate, Reason, LeaveType,
                 PermissionHours, Status, CreatedAt)
                VALUES (%s,%s,%s,%s,%s,%s,'PENDING',GETDATE())
            """, [emp_code, from_date, to_date, reason, leave_type, permission_hours])
            
            # 🔹 Notify Manager
            cursor.execute("SELECT Team FROM Employees WHERE EmployeeCode = %s", [emp_code])
            row = cursor.fetchone()
            if row and row[0]:
                manager_code = row[0]
                cursor.execute("""
                    INSERT INTO Notifications
                    (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
                    VALUES (%s, %s, %s, %s, 'LEAVE', (SELECT MAX(LeaveId) FROM LeaveRequests))
                """, [
                    emp_code,
                    manager_code,
                    "New Leave Request",
                    f"{request.session.get('emp_name')} applied for {leave_type} from {from_date}"
                ])

        connection.commit()
        messages.success(request, "Leave applied successfully")
        return redirect(f"/?month={month}&year={year}")

    # ===============================
    # PAGE RENDER
    # ===============================
    return render(request, "myapp/leave_request.html", {
        "leave_requests": leave_requests,
        "balances": balances,
        "used_leave_types": get_monthly_used_leave_types(emp_code, start_date, end_date),
        "quarter_leave_counts": get_quarter_leave_counts(emp_code),
        "used_permission_hours": used_permission,
        "custom_month": {"year": year, "month": month, "start_date": start_date, "end_date": end_date}
    })

# ============================================================
# LEAVE APPROVAL LIST
# ============================================================
def leave_approval_list(request):
    emp_code = request.session.get("emp_code")
    status = request.GET.get("status", "PENDING")

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT lr.LeaveId,
                   lr.EmployeeCode,
                   e.EmployeeName,
                   e.Team as TLCode,
                   tl.EmployeeName as TLName,
                   lr.FromDate,
                   lr.ToDate,
                   lr.LeaveType,
                   lr.PermissionHours,
                   lr.Status
            FROM LeaveRequests lr
            JOIN Employees e ON e.EmployeeCode = lr.EmployeeCode
            LEFT JOIN Employees tl ON tl.EmployeeCode = e.Team
            WHERE lr.Status = %s
            ORDER BY lr.CreatedAt DESC
        """, [status])

        rows = cursor.fetchall()

    leaves = [{
        "leave_id": r[0],
        "employee_code": r[1],
        "employee_name": r[2],
        "tl_code": r[3],
        "tl_name": r[4],
        "from": r[5],
        "to": r[6],
        "type": r[7],
        "permission_hours": r[8],
        "status": r[9],
    } for r in rows]

    return render(request, "myapp/leave_list.html", {
        "leaves": leaves,
        "current_status": status
    })


from .models import EmployeeReporting


def employee_reporting(request):
    query = "SELECT * FROM EmployeeReporting"
    
    # Execute query and fetch the results as tuples
    with connection.cursor() as cursor:
        cursor.execute(query)
        employees = cursor.fetchall()

    # Manually map columns to their index
    columns = ['EmployeeCode', 'ReportsToEmpCode', 'Role', 'CreatedAt', 'UpdatedAt']
    employees_with_headers = [
        dict(zip(columns, employee)) for employee in employees
    ]

    # Print the result to check the structure
    print(employees_with_headers)
    
    return render(request, 'myapp/employee_reporting.html', {'employees': employees_with_headers})











def hrdashboard(request):
    emp_name = request.session.get("emp_name")
    dept_name = request.session.get("department")

    # -----------------------------
    # Date filter
    # -----------------------------
    selected_date_str = request.GET.get('date')
    selected_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()

    # -----------------------------
    # Get all employees and mapping to Team Lead
    # -----------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EmployeeCode, EmployeeName, Team
            FROM Employees
            WHERE Team IS NOT NULL
            AND LTRIM(RTRIM(Team)) != ''
        """)
        all_emp_rows = cursor.fetchall()

    # Build EmployeeCode -> EmployeeName mapping
    emp_name_map = {emp_code: emp_name for emp_code, emp_name, _ in all_emp_rows}

    # Employee -> Team Lead mapping (Team contains EmployeeCode)
    emp_teamlead_map = {}
    for emp_code, _, team_lead_id in all_emp_rows:
        if team_lead_id:
            team_lead_name = emp_name_map.get(team_lead_id, "Unknown")
            emp_teamlead_map[emp_code] = team_lead_name

    # All employees
    all_employees = [emp for emp, _, _ in all_emp_rows]

    # -----------------------------
    # Holidays and Week-offs for selected_date
    # -----------------------------
    company_hols = set()
    emp_hols = defaultdict(set)
    if all_employees:
        with connection.cursor() as cursor:
            # Company-wide
            cursor.execute("SELECT Date FROM CompanyHolidayWeekOff WHERE Date = %s", [selected_date])
            company_hols = {r[0] for r in cursor.fetchall()}

            # Employee-specific
            placeholders = ",".join(["%s"] * len(all_employees))
            cursor.execute(f"SELECT EmployeeCode, Date FROM EmployeeHolidayWeekOff WHERE EmployeeCode IN ({placeholders}) AND Date = %s", [*all_employees, selected_date])
            for ec, dt in cursor.fetchall():
                emp_hols[ec].add(dt)

    # -----------------------------
    # Attendance SQL
    # -----------------------------
    attendance = set()
    if all_employees:
        placeholders = ",".join(["%s"] * len(all_employees))
        calendar_sql = f"""
            SELECT UserId, CAST(LogDate AS DATE) AS LogDay
            FROM (
                SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
            ) dl
            WHERE UserId IN ({placeholders})
              AND LogDate >= %s
              AND LogDate < DATEADD(day, 1, %s)
            GROUP BY UserId, CAST(LogDate AS DATE)
        """
        with connection.cursor() as cursor:
            cursor.execute(calendar_sql, all_employees + [selected_date, selected_date])
            attendance = {row[0] for row in cursor.fetchall()}

    present_count = len(attendance)

    # -----------------------------
    # Approved leave employees
    # -----------------------------
    placeholders = ",".join(["%s"] * len(all_employees)) if all_employees else "NULL"
    leave_sql = f"""
        SELECT DISTINCT EmployeeCode
        FROM LeaveRequests
        WHERE UPPER(Status) = 'APPROVED'
        AND EmployeeCode IN ({placeholders})
        AND %s BETWEEN FromDate AND ToDate
    """
    with connection.cursor() as cursor:
        cursor.execute(leave_sql, all_employees + [selected_date])
        approved_leave_employees = {row[0] for row in cursor.fetchall()}

    approved_leave_count = len(approved_leave_employees)

    # -----------------------------
    # Absent count calculation (Improved)
    # -----------------------------
    # An employee is absent if:
    # 1. Not present
    # 2. Not on approved leave
    # 3. Not on holiday/week-off
    
    absent_employees = set()
    holiday_employees_count = 0
    
    for emp in all_employees:
        is_present = emp in attendance
        is_on_leave = emp in approved_leave_employees
        is_holiday = selected_date in company_hols or selected_date in emp_hols[emp]
        
        if not is_present and not is_on_leave:
            if is_holiday:
                holiday_employees_count += 1
            else:
                absent_employees.add(emp)
                
    absent_count = len(absent_employees)

    # -----------------------------
    # 7-Day Trend (Attendance)
    # -----------------------------
    trend_labels = []
    trend_present = []
    trend_absent = []
    
    for i in range(6, -1, -1):
        day = selected_date - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        trend_labels.append(day.strftime("%d %b"))
        
        # 1. Fetch Present for the day
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT UserId
                FROM (
                    SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
                    UNION ALL
                    SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
                    UNION ALL
                    SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
                    UNION ALL
                    SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
                ) dl
                WHERE CAST(LogDate AS DATE) = %s
                  AND UserId IN ({",".join(["%s"] * len(all_employees))})
                GROUP BY UserId
            """, [day_str] + all_employees)
            day_attendance = {row[0] for row in cursor.fetchall()}
            p_count = len(day_attendance)
            
            # 2. Fetch On Leave for the day
            cursor.execute(f"""
                SELECT DISTINCT EmployeeCode
                FROM LeaveRequests
                WHERE UPPER(Status) = 'APPROVED'
                  AND EmployeeCode IN ({",".join(["%s"] * len(all_employees))})
                  AND %s BETWEEN FromDate AND ToDate
            """, all_employees + [day_str])
            day_leaves = {row[0] for row in cursor.fetchall()}
            l_count = len(day_leaves)
            
            # 3. Fetch Holidays for the day
            cursor.execute("SELECT COUNT(*) FROM CompanyHolidayWeekOff WHERE Date = %s", [day_str])
            is_company_hol = cursor.fetchone()[0] > 0
            
            cursor.execute(f"SELECT EmployeeCode FROM EmployeeHolidayWeekOff WHERE EmployeeCode IN ({','.join(['%s']*len(all_employees))}) AND Date = %s", all_employees + [day_str])
            day_emp_hols = {row[0] for row in cursor.fetchall()}

            # 4. Calculate Absent for the day
            day_absent_count = 0
            for emp in all_employees:
                if emp not in day_attendance and emp not in day_leaves:
                    if not is_company_hol and emp not in day_emp_hols:
                        day_absent_count += 1
            
            trend_present.append(p_count)
            trend_absent.append(max(0, day_absent_count))

    # -----------------------------
    # Department Wise Stats
    # -----------------------------
    emp_dept_map = {}
    with connection.cursor() as cursor:
        cursor.execute("SELECT EmployeeCode, d.DepartmentFName FROM Employees e LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId WHERE EmployeeCode IN ({})".format(",".join(["%s"] * len(all_employees))), all_employees)
        emp_dept_map = dict(cursor.fetchall())
        
    final_dept_stats = defaultdict(lambda: {"present": 0, "total": 0})
    for emp in all_employees:
        d_name = emp_dept_map.get(emp, "Other")
        final_dept_stats[d_name]["total"] += 1
        if emp in attendance:
            final_dept_stats[d_name]["present"] += 1
    
    dept_labels = list(final_dept_stats.keys())
    dept_present = [final_dept_stats[d]["present"] for d in dept_labels]
    dept_total = [final_dept_stats[d]["total"] for d in dept_labels]


    # -----------------------------
    # Pending leaves count
    # -----------------------------
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM LeaveRequests WHERE UPPER(Status) = 'PENDING'")
        pending_leaves = cursor.fetchone()[0]

    # -----------------------------
    # Count per team for bar chart
    # -----------------------------
    team_emp_map = defaultdict(list)
    for emp_code, _, team_lead_id in all_emp_rows:
        team_lead_name = emp_name_map.get(team_lead_id, "No Team")
        team_emp_map[team_lead_name].append(emp_code)

    teams = sorted(team_emp_map.keys())
    total_counts = []
    present_counts_team = []
    absent_counts_team = []
    leave_counts_team = []

    for team in teams:
        emp_list = team_emp_map[team]
        total = len(emp_list)
        present = len([emp for emp in emp_list if emp in attendance])
        on_leave = len([emp for emp in emp_list if emp in approved_leave_employees])

        # Team specific absent calculation using holiday logic
        absent = 0
        for emp in emp_list:
            if emp not in attendance and emp not in approved_leave_employees:
                if not (selected_date in company_hols or selected_date in emp_hols[emp]):
                    absent += 1

        total_counts.append(total)
        present_counts_team.append(present)
        absent_counts_team.append(absent)
        leave_counts_team.append(on_leave)

    # -----------------------------
    # Pie chart data
    # -----------------------------
    pie_labels = ["Present", "Absent", "Approved Leave"]
    pie_values = [present_count, absent_count, approved_leave_count]

    # -----------------------------
    # Leave details for table
    # -----------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                LeaveId, EmployeeCode, LeaveType, FromDate, ToDate,
                Reason, Status, ApprovedBy, CreatedAt
            FROM LeaveRequests
            ORDER BY CreatedAt DESC
        """)
        columns = [col[0] for col in cursor.description]
        leaves = [dict(zip(columns, row)) for row in cursor.fetchall()]

    for leave in leaves:
        leave["Days"] = (leave["ToDate"] - leave["FromDate"]).days + 1
    
    # [Lines 4801 to 4899 logic was about final_teams, total_present, total_absent]
    # I need to ensure consistency here too.
    
    selected_date_str_sql = selected_date.strftime("%Y-%m-%d")
 
    attendance_sql = """
        WITH TeamHierarchy AS (
            SELECT EmployeeCode, EmployeeName, Team
            FROM Employees
            WHERE Team IS NOT NULL
            AND LTRIM(RTRIM(Team)) != ''
            UNION ALL
            SELECT e.EmployeeCode, e.EmployeeName, e.Team
            FROM Employees e
            INNER JOIN TeamHierarchy th ON e.Team = th.EmployeeCode
        )
        SELECT DISTINCT
            th.EmployeeCode, th.EmployeeName, th.Team,
            CASE
                WHEN dl.UserId IS NOT NULL THEN 'PRESENT'
                ELSE 'ABSENT'
            END AS Status
        FROM TeamHierarchy th
        LEFT JOIN (
            SELECT DISTINCT UserId
            FROM (
                SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
            ) all_logs
            WHERE CAST(LogDate AS DATE) = %s
        ) dl ON dl.UserId = th.EmployeeCode
        WHERE th.EmployeeName NOT LIKE %s
        ORDER BY th.EmployeeCode
    """
 
    with connection.cursor() as cursor:
        cursor.execute(attendance_sql, [selected_date_str_sql, "del_%"])
        rows = cursor.fetchall()
 
    team_data = defaultdict(list)
    for r in rows:
        t_id = r[2] if len(r) > 3 else "My Team"
        # Determine if actually absent (not holiday/leave)
        status = r[3]
        if status == 'ABSENT':
            if r[0] in approved_leave_employees:
                status = 'ON_LEAVE'
            elif selected_date in company_hols or selected_date in emp_hols[r[0]]:
                status = 'HOLIDAY'
        
        team_data[t_id].append({
            "emp_code": r[0],
            "emp_name": r[1],
            "status": status,
        })

    team_codes = list(team_data.keys())
    team_name_map = {}
    if team_codes:
        placeholders = ",".join(["%s"] * len(team_codes))
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode IN ({placeholders})", team_codes)
            team_name_map = dict(cursor.fetchall())

    final_teams = []
    for team_code, members in team_data.items():
        p_c = sum(1 for m in members if m["status"] == "PRESENT")
        a_c = sum(1 for m in members if m["status"] == "ABSENT")
        final_teams.append({
            "team_code": team_code,
            "team_name": team_name_map.get(team_code, team_code),
            "members": members,
            "total": len(members),
            "present": p_c,
            "absent": a_c,
        })

    all_members = [emp for members in team_data.values() for emp in members]
    total_count = len(all_employees)
    total_present = present_count
    total_absent = absent_count
    shrinkage = round((total_absent / total_count) * 100, 2) if total_count else 0

    return render(request, "myapp/hrdashboard.html", {
        "team": final_teams,
        "total_count": total_count,
        "total_presents": total_present,
        "total_absents": total_absent,
        "shrinkage": shrinkage,
        "teams": teams,
        "total_counts": total_counts,
        "present_counts_team": present_counts_team,
        "absent_counts_team": absent_counts_team,
        "leave_counts_team": leave_counts_team,
        "pie_labels": pie_labels,
        "pie_values": pie_values,
        "date": selected_date,
        "leaves": leaves,
        "emp_name": emp_name,
        "dept_name": dept_name,
        "total_employees": total_count,
        "total_present": total_present,
        "pending_leaves": pending_leaves,
        "approved_leaves": approved_leave_count,
        "total_absent": total_absent,
        "trend_labels": trend_labels,
        "trend_present": trend_present,
        "trend_absent": trend_absent,
        "dept_labels": dept_labels,
        "dept_present": dept_present,
        "dept_total": dept_total,
    })
def manual_punch(request):
    msg = ""
    if request.method == "POST":
        emp_code = request.POST.get("emp_code")
        punch_date = request.POST.get("punch_date")
        punch_time = request.POST.get("punch_time")
        punch_type = request.POST.get("punch_type")
        remarks = request.POST.get("remarks")

        sql = """
            INSERT INTO ManualPunches
            (EmployeeCode, PunchDate, PunchTime, PunchType, Remarks, ApprovalStatus)
            VALUES (%s, %s, %s, %s, %s, 'PENDING')
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [
                emp_code, punch_date, punch_time, punch_type, remarks
            ])
            punch_id = cursor.lastrowid # Or fetch if needed, but ManualPunches has AutoInc

            # 🔹 Get manager from Employees.Team
            cursor.execute("SELECT Team FROM Employees WHERE EmployeeCode = %s", [emp_code])
            row = cursor.fetchone()
            if row and row[0]:
                manager_code = row[0]
                cursor.execute("""
                    INSERT INTO Notifications
                    (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
                    VALUES (%s, %s, %s, %s, 'MANUAL_PUNCH', %s)
                """, [
                    emp_code,
                    manager_code,
                    "Manual Punch Request",
                    f"{emp_code} requested a manual punch for {punch_date}",
                    0 # Manual punches don't have a single unique ID that we use for redirection in the same way yet, but we can use 0 or punch_id if we want specialized views later.
                ])

        connection.commit()
        msg = "Punch sent for Team Head approval."

    sql_list = """
        SELECT mp.PunchId, mp.EmployeeCode, e.EmployeeName, mp.PunchDate, mp.PunchTime, mp.PunchType,
               mp.Remarks, mp.ApprovalStatus
        FROM ManualPunches mp
        LEFT JOIN Employees e ON mp.EmployeeCode = e.EmployeeCode
        ORDER BY mp.PunchDate DESC, mp.PunchTime DESC
    """
    with connection.cursor() as cursor:
        cursor.execute(sql_list)
        rows = cursor.fetchall()

    punches = [{
        "id": r[0],
        "emp_code": r[1],
        "emp_name": r[2],
        "date": r[3],
        "time": r[4],
        "type": r[5],
        "remarks": r[6],
        "status": r[7],
    } for r in rows]

    return render(request, "myapp/manual_punch.html", {
        "punches": punches,
        "msg": msg
    })


def edit_manual_punch(request, punch_id):
    if request.method == "POST":
        emp_code = request.POST.get("emp_code")
        punch_date = request.POST.get("punch_date")
        punch_time = request.POST.get("punch_time")
        punch_type = request.POST.get("punch_type")
        remarks = request.POST.get("remarks")

        sql = """
            UPDATE ManualPunches
            SET EmployeeCode=%s, PunchDate=%s, PunchTime=%s, 
                PunchType=%s, Remarks=%s
            WHERE PunchId=%s
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [
                emp_code, punch_date, punch_time,
                punch_type, remarks, punch_id
            ])

        return redirect("manual_punch")

    sql = """
        SELECT PunchId, EmployeeCode, PunchDate, PunchTime, PunchType, Remarks
        FROM ManualPunches WHERE PunchId=%s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [punch_id])
        r = cursor.fetchone()

    data = {
        "id": r[0],
        "emp_code": r[1],
        "date": r[2],
        "time": r[3],
        "type": r[4],
        "remarks": r[5],
    }

    return render(request, "myapp/manual_punch_edit.html", {"data": data})
def team_head_approval(request):
    team_head_code = request.session.get("emp_code")

    if not team_head_code:
        return redirect("login")

    approval_sql = """
        WITH TeamHierarchy AS (
            SELECT EmployeeCode, EmployeeName, Team
            FROM Employees
            WHERE Team = %s

            UNION ALL

            SELECT e.EmployeeCode, e.EmployeeName, e.Team
            FROM Employees e
            JOIN TeamHierarchy th ON e.Team = th.EmployeeCode
        )
        SELECT
            mp.PunchId,
            mp.EmployeeCode,
            th.EmployeeName,
            mp.PunchDate,
            mp.PunchTime,
            mp.PunchType,
            mp.Remarks
        FROM ManualPunches mp
        JOIN TeamHierarchy th
            ON th.EmployeeCode = mp.EmployeeCode
        WHERE mp.ApprovalStatus = 'PENDING'
        OPTION (MAXRECURSION 100)
    """

    with connection.cursor() as cursor:
        cursor.execute(approval_sql, [team_head_code])
        rows = cursor.fetchall()

    punches = [{
        "id": r[0],
        "emp_code": r[1],
        "emp_name": r[2],
        "date": r[3],
        "time": r[4],
        "type": r[5],
        "remarks": r[6],
    } for r in rows]

    return render(request, "myapp/team_head_approval.html", {
        "punches": punches
    })
def approve_manual_punch(request, punch_id):
    approver = request.session.get("emp_code")

    sql = """
        UPDATE ManualPunches
        SET ApprovalStatus = 'APPROVED',
            ApprovedBy = %s,
            ApprovedOn = GETDATE()
        WHERE PunchId = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [approver, punch_id])
        
        # Notify Employee
        cursor.execute("SELECT EmployeeCode, PunchDate FROM ManualPunches WHERE PunchId = %s", [punch_id])
        row = cursor.fetchone()
        if row:
            emp_code_notify, p_date = row
            cursor.execute("""
                INSERT INTO Notifications
                (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
                VALUES (%s, %s, %s, %s, 'MANUAL_PUNCH_RESPONSE', %s)
            """, [
                approver,
                emp_code_notify,
                "Manual Punch Approved",
                f"Your manual punch for {p_date} has been approved",
                punch_id
            ])

    connection.commit()
    return redirect("team_head_approval")

def reject_manual_punch(request, punch_id):
    approver = request.session.get("emp_code")

    sql = """
        UPDATE ManualPunches
        SET ApprovalStatus = 'REJECTED',
            ApprovedBy = %s,
            ApprovedOn = GETDATE()
        WHERE PunchId = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [approver, punch_id])
        
        # Notify Employee
        cursor.execute("SELECT EmployeeCode, PunchDate FROM ManualPunches WHERE PunchId = %s", [punch_id])
        row = cursor.fetchone()
        if row:
            emp_code_notify, p_date = row
            cursor.execute("""
                INSERT INTO Notifications
                (SenderEmpCode, ReceiverEmpCode, Title, Message, Type, RelatedId)
                VALUES (%s, %s, %s, %s, 'MANUAL_PUNCH_RESPONSE', %s)
            """, [
                approver,
                emp_code_notify,
                "Manual Punch Rejected",
                f"Your manual punch for {p_date} has been rejected",
                punch_id
            ])

    connection.commit()
    return redirect("team_head_approval")


def delete_manual_punch(request, punch_id):
    sql = "DELETE FROM ManualPunches WHERE PunchId=%s"
    with connection.cursor() as cursor:
        cursor.execute(sql, [punch_id])

    return redirect("manual_punch")




# Employee details page for hr
def user_list(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT e.EmployeeCode, e.EmployeeName, d.DepartmentFName, e.Designation, e.Gender, e.DOJ, e.EmployementType, e.FatherName, e.MotherName, e.ResidentialAddress, e.PermanentAddress, e.ContactNo, e.Email, e.DOB, e.PlaceOfBirth, e.Location, e.BLOODGROUP, e.Team, e.AadhaarNumber 
            FROM Employees e
            LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId
            ORDER BY e.EmployeeName
        """)
        users = cursor.fetchall()

    return render(request, "myapp/user_list.html", {"users": users})
def my_profile(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    emp_code = request.session["emp_code"]
    
    import base64

    # -------------------------
    # Fetch employee + department
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT e.EmployeeCode, e.EmployeeName, d.DepartmentFName, e.Designation, e.Gender, e.DOJ, e.EmployementType, e.FatherName, e.MotherName, e.ResidentialAddress, e.PermanentAddress, e.ContactNo, e.Email, e.DOB, e.PlaceOfBirth, e.Location, e.BLOODGROUP, e.Team, e.AadhaarNumber, e.EmployeePhoto,
                   e.Qualification, e.MaritalStatus, e.TotalExperience, e.PreviousOrganization, e.BGVDetails, e.AAPCCertification, e.CredentialNumber, e.EmergencyContactNumber, e.PANNumber, e.UANNumber
            FROM Employees e
            LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId
            WHERE e.EmployeeCode = %s
        """, [emp_code])
        row = cursor.fetchone()

    photo_b64 = None
    if row and row[19]:
        try:
            # Depending on pyodbc/mssql driver, it might be returned as bytes or bytearray
            photo_bytes = row[19]
            if isinstance(photo_bytes, memoryview):
                photo_bytes = photo_bytes.tobytes()
            photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
        except Exception:
            pass

    profile = {
        "emp_code": row[0],
        "name": row[1],
        "department_name": row[2],
        "designation": row[3], 
        "gender": row[4], 
        "doj": row[5], 
        "employement_type": row[6], 
        "father_name": row[7], 
        "mother_name": row[8], 
        "residential_address": row[9], 
        "permanent_address": row[10], 
        "contact_no": row[11], 
        "email": row[12], 
        "dob": row[13], 
        "place_of_birth": row[14], 
        "location": row[15], 
        "blood_group": row[16], 
        "team": row[17], 
        "aadhaar_number": row[18],
        "photo": photo_b64,
        "qualification": row[20],
        "marital_status": row[21],
        "total_experience": row[22],
        "prev_org": row[23],
        "bgv_details": row[24],
        "aapc_cert": row[25],
        "credential_no": row[26],
        "emergency_contact": row[27],
        "pan_number": row[28],
        "uan_number": row[29]
    }

    # -------------------------
    # Fetch bank details
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT BankName, AccountNumber, IFSCCode
            FROM EmployeeBankDetails
            WHERE EmployeeCode = %s
        """, [emp_code])
        bank_row = cursor.fetchone()

    bank = {
        "bank_name": bank_row[0] if bank_row else None,
        "account_number": bank_row[1] if bank_row else None,
        "ifsc_code": bank_row[2] if bank_row else None,
    }

    return render(request, "myapp/my_profile.html", {
        "profile": profile,
        "bank": bank
    })
# Employee profile page
def user_profile(request, emp_code):
    emp_code = urllib.parse.unquote(emp_code).strip()

    # -------------------------
    # Fetch employee + department
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT e.EmployeeCode, e.EmployeeName, d.DepartmentFName, e.Designation, e.Gender, e.DOJ, e.EmployementType, e.FatherName, e.MotherName, e.ResidentialAddress, e.PermanentAddress, e.ContactNo, e.Email, e.DOB, e.PlaceOfBirth, e.Location, e.BLOODGROUP, e.Team, e.AadhaarNumber, e.EmployeePhoto,
                   e.Qualification, e.MaritalStatus, e.TotalExperience, e.PreviousOrganization, e.BGVDetails, e.AAPCCertification, e.CredentialNumber, e.EmergencyContactNumber, e.PANNumber, e.UANNumber
            FROM Employees e
            LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId
            WHERE e.EmployeeCode = %s
        """, [emp_code])
        row = cursor.fetchone()

    if not row:
        return render(request, "myapp/user_profile.html", {"profile": None})

    photo_b64 = None
    if row and row[19]:
        try:
            photo_bytes = row[19]
            if isinstance(photo_bytes, memoryview):
                photo_bytes = photo_bytes.tobytes()
            photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
        except Exception:
            photo_b64 = None

    profile = {
        "emp_code": row[0],
        "name": row[1],
        "department_name": row[2],
        "designation": row[3], 
        "gender": row[4], 
        "doj": row[5], 
        "employement_type": row[6], 
        "father_name": row[7], 
        "mother_name": row[8], 
        "residential_address": row[9], 
        "permanent_address": row[10], 
        "contact_no": row[11], 
        "email": row[12], 
        "dob": row[13], 
        "place_of_birth": row[14], 
        "location": row[15], 
        "blood_group": row[16], 
        "team": row[17], 
        "aadhaar_number": row[18],
        "photo": photo_b64,
        "qualification": row[20],
        "marital_status": row[21],
        "total_experience": row[22],
        "prev_org": row[23],
        "bgv_details": row[24],
        "aapc_cert": row[25],
        "credential_no": row[26],
        "emergency_contact": row[27],
        "pan_number": row[28],
        "uan_number": row[29]
    }

    # -------------------------
    # Fetch bank details
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT BankName, AccountNumber, IFSCCode
            FROM EmployeeBankDetails
            WHERE EmployeeCode = %s
        """, [emp_code])
        bank_row = cursor.fetchone()

    bank = {
        "bank_name": bank_row[0] if bank_row else None,
        "account_number": bank_row[1] if bank_row else None,
        "ifsc_code": bank_row[2] if bank_row else None,
    }

    # -------------------------
    # Check Active Status (System Credentials)
    # -------------------------
    is_active = EmployeePassword.objects.filter(Employee_id=emp_code).exists()

    return render(request, "myapp/user_profile.html", {
        "profile": profile,
        "bank": bank,
        "is_active": is_active
    })

@csrf_exempt
def edit_user_profile(request, emp_code):
    # Decode URL-encoded emp_code
    emp_code = urllib.parse.unquote(emp_code).strip()
    print(f"Editing profile for emp_code: '{emp_code}'")

    import base64

    # -------------------------
    # Fetch employee profile
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EmployeeCode, EmployeeName, DepartmentId, Designation, Gender, DOJ, EmployementType, FatherName, MotherName, ResidentialAddress, PermanentAddress, ContactNo, Email, DOB, PlaceOfBirth, Location, BLOODGROUP, Team, AadhaarNumber, EmployeePhoto,
                   Qualification, MaritalStatus, TotalExperience, PreviousOrganization, BGVDetails, AAPCCertification, CredentialNumber, EmergencyContactNumber, PANNumber, UANNumber
            FROM Employees
            WHERE EmployeeCode = %s
        """, [emp_code])
        row = cursor.fetchone()

    if not row:
        return render(request, "myapp/edit_user_profile.html", {
            "profile": None,
            "bank": None,
            "departments": []
        })

    photo_b64 = None
    if row[19]:
        try:
            photo_bytes = row[19]
            if isinstance(photo_bytes, memoryview):
                photo_bytes = photo_bytes.tobytes()
            photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
        except Exception:
            pass

    profile = {
        "emp_code": row[0],
        "name": row[1],
        "department_id": row[2],
        "designation": row[3], 
        "gender": row[4], 
        "doj": row[5], 
        "employement_type": row[6], 
        "father_name": row[7], 
        "mother_name": row[8], 
        "residential_address": row[9], 
        "permanent_address": row[10], 
        "contact_no": row[11], 
        "email": row[12], 
        "dob": row[13], 
        "place_of_birth": row[14], 
        "location": row[15], 
        "blood_group": row[16], 
        "team": row[17], 
        "aadhaar_number": row[18],
        "photo": photo_b64,
        "qualification": row[20],
        "marital_status": row[21],
        "total_experience": row[22],
        "prev_org": row[23],
        "bgv_details": row[24],
        "aapc_cert": row[25],
        "credential_no": row[26],
        "emergency_contact": row[27],
        "pan_number": row[28],
        "uan_number": row[29]
    }

    # -------------------------
    # Fetch bank details
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT BankName, AccountNumber, IFSCCode
            FROM EmployeeBankDetails
            WHERE EmployeeCode = %s
        """, [emp_code])
        bank_row = cursor.fetchone()

    bank = {
        "bank_name": bank_row[0] if bank_row else "",
        "account_number": bank_row[1] if bank_row else "",
        "ifsc_code": bank_row[2] if bank_row else "",
    }

    # -------------------------
    # Handle POST (Save)
    # -------------------------
    if request.method == "POST":
        # Employee fields
        name = request.POST.get("name")
        employment_type = request.POST.get("employment_type")
        department_id = request.POST.get("department_id")
        designation = request.POST.get("designation")
        gender = request.POST.get("gender")
        doj = request.POST.get("doj")
        employement_type = request.POST.get("employement_type")
        father_name = request.POST.get("father_name")
        mother_name = request.POST.get("mother_name")
        residential_address = request.POST.get("residential_address")
        permanent_address = request.POST.get("permanent_address")
        contact_no = request.POST.get("contact_no")
        email = request.POST.get("email")
        dob = request.POST.get("dob")
        place_of_birth = request.POST.get("place_of_birth")
        location = request.POST.get("location")
        blood_group = request.POST.get("blood_group")
        team = request.POST.get("team")
        aadhaar_number = request.POST.get("aadhaar_number")
        qualification = request.POST.get("qualification")
        marital_status = request.POST.get("marital_status")
        total_experience = request.POST.get("total_experience")
        prev_org = request.POST.get("prev_org")
        bgv_details = request.POST.get("bgv_details")
        aapc_cert = request.POST.get("aapc_cert")
        credential_no = request.POST.get("credential_no")
        emergency_contact = request.POST.get("emergency_contact")
        pan_number = request.POST.get("pan_number")
        uan_number = request.POST.get("uan_number")

        # Bank fields
        bank_name = request.POST.get("bank_name")
        account_number = request.POST.get("account_number")
        ifsc_code = request.POST.get("ifsc_code")

        with connection.cursor() as cursor:
            # Handle photo upload
            photo = request.FILES.get("profile_photo")
            photo_data = None
            if photo:
                try:
                    photo_data = photo.read()
                except Exception as e:
                    messages.error(request, f"Error processing photo: {str(e)}")

            if photo_data:
                cursor.execute("""
                    UPDATE Employees
                    SET EmployeeName=%s,
                        DepartmentId=%s,
                        Designation=%s,
                        Gender=%s, 
                        DOJ=%s, 
                        EmployementType=%s, 
                        FatherName=%s, 
                        MotherName=%s, 
                        ResidentialAddress=%s, 
                        PermanentAddress=%s, 
                        ContactNo=%s, 
                        Email=%s, 
                        DOB=%s, 
                        PlaceOfBirth=%s, 
                        Location=%s, 
                        BLOODGROUP=%s, 
                        Team=%s, 
                        AadhaarNumber=%s,
                        Qualification=%s,
                        MaritalStatus=%s,
                        TotalExperience=%s,
                        PreviousOrganization=%s,
                        BGVDetails=%s,
                        AAPCCertification=%s,
                        CredentialNumber=%s,
                        EmergencyContactNumber=%s,
                        PANNumber=%s,
                        UANNumber=%s,
                        EmployeePhoto=%s
                    WHERE EmployeeCode=%s
                """, [
                    name, department_id, designation, gender, doj, employment_type, father_name, mother_name, residential_address, permanent_address, contact_no, email, dob, place_of_birth, location, blood_group, team, aadhaar_number, 
                    qualification, marital_status, total_experience, prev_org, bgv_details, aapc_cert, credential_no, emergency_contact, pan_number, uan_number,
                    photo_data, emp_code
                ])
                # Sync session photo if editing own profile
                if emp_code == request.session.get("emp_code"):
                    try:
                        request.session["emp_photo"] = base64.b64encode(photo_data).decode('utf-8')
                        print(f"Synced session photo for {emp_code}")
                    except Exception as e:
                        print(f"Error syncing session photo: {e}")
            else:
                cursor.execute("""
                    UPDATE Employees
                    SET EmployeeName=%s,
                        DepartmentId=%s,
                        Designation=%s,
                        Gender=%s, 
                        DOJ=%s, 
                        EmployementType=%s, 
                        FatherName=%s, 
                        MotherName=%s, 
                        ResidentialAddress=%s, 
                        PermanentAddress=%s, 
                        ContactNo=%s, 
                        Email=%s, 
                        DOB=%s, 
                        PlaceOfBirth=%s, 
                        Location=%s, 
                        BLOODGROUP=%s, 
                        Team=%s, 
                        AadhaarNumber=%s,
                        Qualification=%s,
                        MaritalStatus=%s,
                        TotalExperience=%s,
                        PreviousOrganization=%s,
                        BGVDetails=%s,
                        AAPCCertification=%s,
                        CredentialNumber=%s,
                        EmergencyContactNumber=%s,
                        PANNumber=%s,
                        UANNumber=%s
                    WHERE EmployeeCode=%s
                """, [
                    name, department_id, designation, gender, doj, employment_type, father_name, mother_name, residential_address, permanent_address, contact_no, email, dob, place_of_birth, location, blood_group, team, aadhaar_number,
                    qualification, marital_status, total_experience, prev_org, bgv_details, aapc_cert, credential_no, emergency_contact, pan_number, uan_number,
                    emp_code
                ])

            # Insert or Update bank details
            cursor.execute("""
                SELECT 1 FROM EmployeeBankDetails WHERE EmployeeCode=%s
            """, [emp_code])

            if cursor.fetchone():
                cursor.execute("""
                    UPDATE EmployeeBankDetails
                    SET BankName=%s,
                        AccountNumber=%s,
                        IFSCCode=%s
                    WHERE EmployeeCode=%s
                """, [bank_name, account_number, ifsc_code, emp_code])
            else:
                cursor.execute("""
                    INSERT INTO EmployeeBankDetails
                    (EmployeeCode, BankName, AccountNumber, IFSCCode)
                    VALUES (%s, %s, %s, %s)
                """, [emp_code, bank_name, account_number, ifsc_code])

        return redirect('user_profile', emp_code=emp_code)

    # -------------------------
    # Fetch departments
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DepartmentId, DepartmentFName FROM Departments
        """)
        departments = cursor.fetchall()

    # -------------------------
    # Render page
    # -------------------------
    return render(request, "myapp/edit_user_profile.html", {
        "profile": profile,
        "bank": bank,
        "departments": departments
    })



# holidays and week offs 
def holiday_list(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT Date, DayType, HolidayName
            FROM CompanyHolidayWeekOff
            ORDER BY Date
        """)
        holidays = cursor.fetchall()

    return render(request, "myapp/holiday_list.html", {
        "holidays": holidays
    })

@csrf_exempt
def add_holiday(request):
    if request.method == "POST":
        date = request.POST.get("date")
        day_type = request.POST.get("day_type")
        holiday_name = request.POST.get("holiday_name")

        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO CompanyHolidayWeekOff
                (Date, DayType, HolidayName)
                VALUES (%s, %s, %s)
            """, [date, day_type, holiday_name])

        return redirect("holiday_list")

    return render(request, "myapp/add_holiday.html")

@csrf_exempt
def delete_employee_off(request):
    if request.method == "POST":
        record_id = request.POST.get("id")

        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM EmployeeHolidayWeekOff
                WHERE Id = %s
            """, [record_id])

    return redirect("employee_off_list")
#employee holidays lists and holidays
def employee_off_list(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT e.EmployeeCode, emp.EmployeeName, e.Date, e.DayType, e.Reason, e.Id
            FROM EmployeeHolidayWeekOff e
            JOIN Employees emp ON e.EmployeeCode = emp.EmployeeCode
            ORDER BY e.Date DESC
        """)
        records = cursor.fetchall()

    return render(request, "myapp/employee_off_list.html", {
        "records": records
    })



@csrf_exempt
def add_employee_off(request):
    # Fetch teams
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT Team
            FROM Employees
            ORDER BY Team
        """)
        teams = [row[0] for row in cursor.fetchall()]

        cursor.execute("""
            SELECT EmployeeCode, EmployeeName, Team
            FROM Employees
            ORDER BY EmployeeName
        """)
        employees = cursor.fetchall()

    if request.method == "POST":
        teams = request.POST.getlist("teams[]")
        employee_codes = request.POST.getlist("employee_codes[]")

        from_date = request.POST.get("from_date")
        to_date = request.POST.get("to_date")
        day_type = request.POST.get("day_type")
        reason = request.POST.get("reason")

        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")

        # Get employees list
        with connection.cursor() as cursor:
            if "ALL" in employee_codes:
                if "ALL" in teams:
                    cursor.execute("SELECT EmployeeCode FROM Employees")
                else:
                    cursor.execute("""
                        SELECT EmployeeCode FROM Employees
                        WHERE Team IN %s
                    """, [tuple(teams)])
                emp_list = [row[0] for row in cursor.fetchall()]
            else:
                emp_list = employee_codes

        with connection.cursor() as cursor:
            current = start
            while current <= end:
                for emp in emp_list:
                    cursor.execute("""
                        INSERT INTO EmployeeHolidayWeekOff
                        (EmployeeCode, Date, DayType, Reason)
                        VALUES (%s, %s, %s, %s)
                    """, [emp, current.date(), day_type, reason])
                current += timedelta(days=1)

        return redirect("employee_off_list")

    return render(request, "myapp/add_employee_off.html", {
        "teams": teams,
        "employees": employees
    })
def get_salary_cycle(year, month):
    """
    Given salary month (YYYY, MM),
    return cycle start (26 prev month) and end (25 current month)
    """
    if month == 1:
        start_date = date(year - 1, 12, 26)
    else:
        start_date = date(year, month - 1, 26)

    end_date = date(year, month, 25)
    return start_date, end_date
from datetime import datetime, timedelta, date
from django.db import connection


def get_present_absent(emp_code, start_date, end_date):
    attendance = {}          # date -> 0 / 0.5 / 1
    employee_shifts = {}
    permission_days = set()
    half_days = set()
    rest_days = set()

    with connection.cursor() as cursor:

        # ==============================
        # Shift allocation
        # ==============================
        cursor.execute("""
            SELECT a.FromDate, ISNULL(a.ToDate, '2099-12-31'), s.BeginTime
            FROM EmployeeShiftAllocation a
            JOIN Shifts s ON s.ShiftId = a.ShiftId
            WHERE a.EmployeeCode=%s
              AND (
                    a.FromDate BETWEEN %s AND %s
                 OR a.ToDate BETWEEN %s AND %s
                 OR (a.FromDate <= %s AND a.ToDate >= %s)
              )
        """, [emp_code,
              start_date, end_date,
              start_date, end_date,
              start_date, end_date])

        for fs, fe, begin_time in cursor.fetchall():
            fs = fs if isinstance(fs, date) else fs.date()
            fe = fe if isinstance(fe, date) else fe.date()

            if isinstance(begin_time, str):
                begin_time = datetime.strptime(begin_time, "%H:%M").time()

            d = fs
            while d <= fe:
                employee_shifts[d] = begin_time
                d += timedelta(days=1)

        # ==============================
        # Device logs
        # ==============================
        cursor.execute("""
            SELECT CAST(LogDate AS DATE), MIN(CAST(LogDate AS TIME))
            FROM (
                SELECT UserId, LogDate FROM dbo.DeviceLogs_3_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_2_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_1_2026
                UNION ALL
                SELECT UserId, LogDate FROM dbo.DeviceLogs_12_2025
            ) d
            WHERE d.UserId=%s
              AND CAST(d.LogDate AS DATE) BETWEEN %s AND %s
            GROUP BY CAST(d.LogDate AS DATE)
        """, [emp_code, start_date, end_date])

        device_in = {r[0]: r[1] for r in cursor.fetchall()}

        # ==============================
        # Manual punches
        # ==============================
        cursor.execute("""
            SELECT PunchDate, MIN(PunchTime)
            FROM ManualPunches
            WHERE EmployeeCode=%s
              AND ApprovalStatus='APPROVED'
              AND PunchDate BETWEEN %s AND %s
            GROUP BY PunchDate
        """, [emp_code, start_date, end_date])

        manual_in = {r[0]: r[1] for r in cursor.fetchall()}

        # ==============================
        # Permission
        # ==============================
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode=%s
              AND Status='APPROVED'
              AND LeaveType='Permission'
        """, [emp_code])

        for f, t in cursor.fetchall():
            f_d = f.date() if isinstance(f, datetime) else f
            t_d = t.date() if isinstance(t, datetime) else t
            d = max(f_d, start_date)
            while d <= min(t_d, end_date):
                permission_days.add(d)
                d += timedelta(days=1)

        # ==============================
        # Half Day leave
        # ==============================
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode=%s
              AND Status='APPROVED'
              AND LeaveType='Half Day'
        """, [emp_code])

        for f, t in cursor.fetchall():
            f_d = f.date() if isinstance(f, datetime) else f
            t_d = t.date() if isinstance(t, datetime) else t
            d = max(f_d, start_date)
            while d <= min(t_d, end_date):
                half_days.add(d)
                d += timedelta(days=1)

        # ==============================
        # Full day leaves
        # ==============================
        cursor.execute("""
            SELECT FromDate, ToDate
            FROM LeaveRequests
            WHERE EmployeeCode=%s
              AND Status='APPROVED'
              AND LeaveType NOT IN ('Permission', 'Half Day')
        """, [emp_code])

        for f, t in cursor.fetchall():
            f_d = f.date() if isinstance(f, datetime) else f
            t_d = t.date() if isinstance(t, datetime) else t
            d = max(f_d, start_date)
            while d <= min(t_d, end_date):
                attendance[d] = 1
                d += timedelta(days=1)

        # ==============================
        # Holidays
        # ==============================
        cursor.execute("""
            SELECT Date FROM CompanyHolidayWeekOff
            WHERE Date BETWEEN %s AND %s
        """, [start_date, end_date])

        for (d,) in cursor.fetchall():
            rest_days.add(d)

        cursor.execute("""
            SELECT Date FROM EmployeeHolidayWeekOff
            WHERE EmployeeCode=%s
              AND Date BETWEEN %s AND %s
        """, [emp_code, start_date, end_date])

        for (d,) in cursor.fetchall():
            rest_days.add(d)

    # ==============================
    # Attendance calculation
    # ==============================
    d = start_date
    while d <= end_date:
        if d not in attendance:
            shift_start = employee_shifts.get(d)
            in_time = device_in.get(d) or manual_in.get(d)

            if in_time and shift_start:
                late_minutes = (
                    datetime.combine(d, in_time) -
                    datetime.combine(d, shift_start)
                ).total_seconds() / 60

                attendance[d] = 0.5 if late_minutes >= 1 else 1
            elif in_time:
                attendance[d] = 1

        # Overrides
        if d in permission_days:
            attendance[d] = 1
        elif d in half_days:
            attendance[d] = 0.5

        d += timedelta(days=1)

    # ==============================
    # Sandwich Leave Logic
    # ==============================
    # Any holiday/weekly-off (rest_day) sandwiched between two absences
    # will also be counted as absent (LWP).
    for rd in sorted(list(rest_days)):
        # Find nearest working day before rd
        prev_wd = rd - timedelta(days=1)
        while prev_wd >= start_date and prev_wd in rest_days:
            prev_wd -= timedelta(days=1)
        
        # Find nearest working day after rd
        next_wd = rd + timedelta(days=1)
        while next_wd <= end_date and next_wd in rest_days:
            next_wd += timedelta(days=1)
            
        # Neighbors outside range are assumed 'Present' for safety
        prev_status = attendance.get(prev_wd, 0) if prev_wd >= start_date else 1
        next_status = attendance.get(next_wd, 0) if next_wd <= end_date else 1
        
        if prev_status == 0 and next_status == 0:
            # Both sides are absent -> this holiday is also an absence
            # (stays out of attendance map, effectively 0)
            pass
        else:
            # Paid holiday
            attendance[rd] = 1

    # ==============================
    # Final counts
    # ==============================
    total_days = (end_date - start_date).days + 1
    present = sum(attendance.values())
    absent = max(0, total_days - present)

    return present, absent, total_days

def breakup_from_earned_gross(earned_gross):
    basic = earned_gross * Decimal("0.50")
    hra = earned_gross * Decimal("0.30")
    da = earned_gross * Decimal("0.20")

    pf_base = earned_gross - hra
    pf = pf_base * Decimal("0.12")

    if earned_gross >= 21000:
        esi = Decimal("0.00")
    else:
        esi = earned_gross * Decimal("0.0075")

    return (
        basic.quantize(Decimal("0.01")),
        hra.quantize(Decimal("0.01")),
        da.quantize(Decimal("0.01")),
        pf.quantize(Decimal("0.01")),
        esi.quantize(Decimal("0.01")),
    )


def salary_list(request):
    year = int(request.GET.get("year", date.today().year))
    month = int(request.GET.get("month", date.today().month))

    start_date, end_date = get_salary_cycle(year, month)

    employees = []

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                e.EmployeeCode,
                e.EmployeeName,
                d.DepartmentFName,
                s.TotalGross
            FROM Employees e
            LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId
            LEFT JOIN EmployeeSalary s ON e.EmployeeCode = s.EmployeeCode
        """)
        rows = cursor.fetchall()

    for emp_code, name, dept, gross in rows:
        gross = Decimal(gross or 0)

        present, absent, total_days = get_present_absent(
            emp_code, start_date, end_date
        )

        total_days = Decimal(total_days)

        earned_gross = (
            (gross / total_days) * Decimal(present)
            if total_days > 0 else Decimal("0.00")
        ).quantize(Decimal("0.01"))

        basic, hra, da, pf, esi = breakup_from_earned_gross(earned_gross)
        pt = 208
        net_salary = (earned_gross - pf - esi).quantize(
            Decimal("0.01")
        )
        net_salary = net_salary - pt
        employees.append({
            "emp_code": emp_code,
            "name": name,
            "department": dept,
            "total_gross": gross,
            "earned_gross": earned_gross,
            "present_days": present,
            "absent_days": absent,
            "basic": basic,
            "hra": hra,
            "da": da,
            "pf": pf,
            "esi": esi,
            "net_salary": net_salary,
            "pt" : pt
        })

    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    years = list(range(date.today().year - 5, date.today().year + 1))

    return render(request, "myapp/salary_list.html", {
        "employees": employees,
        "selected_month": month,
        "selected_year": year,
        "months": months,
        "years": years,
        "cycle_start": start_date,
        "cycle_end": end_date,
    })
def salary_password(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    if request.method == "POST":
        password = request.POST.get("password")
        emp_code = request.session["emp_code"]

        pwd_obj = EmployeePassword.objects.filter(Employee_id=emp_code).first()

        if pwd_obj and check_password(password, pwd_obj.PasswordHash):
            # ✅ Verified
            request.session["salary_verified"] = True
            return redirect("salary_slip")
        else:
            messages.error(request, "Invalid password")

    return render(request, "myapp/salary_password.html")
def salary_slip(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")

    # 🔐 Salary slip password check
    if not request.session.get("salary_verified"):
        return redirect("salary_password")

    emp_code = request.session["emp_code"]

    # -------------------------
    # Month / Year
    # -------------------------
    year = int(request.GET.get("year", date.today().year))
    month = int(request.GET.get("month", date.today().month))

    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    years = list(range(date.today().year - 5, date.today().year + 1))

    # -------------------------
    # Salary Cycle (26–25)
    # -------------------------
    start_date, end_date = get_salary_cycle(year, month)

    # -------------------------
    # Employee Master Data
    # -------------------------
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                e.EmployeeName,
                e.DOJ,
                e.Location,
                e.Designation,
                e.EmployementType,
                d.DepartmentFName,
                s.TotalGross
            FROM Employees e
            LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId
            LEFT JOIN EmployeeSalary s ON e.EmployeeCode = s.EmployeeCode
            WHERE e.EmployeeCode = %s
        """, [emp_code])

        row = cursor.fetchone()

    if not row:
        return render(request, "myapp/salary_slip.html", {"error": "Employee not found"})

    emp_name, doj, location, designation, employementtype, department, total_gross = row
    total_gross = Decimal(total_gross or 0)

    # -------------------------
    # Attendance Summary
    # -------------------------
    present_days, absent_days, total_days = get_present_absent(
        emp_code, start_date, end_date
    )

    working_days = total_days  # holidays already counted as present
    paid_days = present_days

    if working_days <= 0:
        return render(request, "myapp/salary_slip.html", {"error": "Invalid working days"})

    # -------------------------
    # Earned Gross Salary
    # -------------------------
    earned_gross = (
        (total_gross / Decimal(working_days)) * Decimal(paid_days)
    ).quantize(Decimal("0.01"))

    # -------------------------
    # Salary Breakup
    # -------------------------
    basic, hra, allowance, pf, esi = breakup_from_earned_gross(earned_gross)

    total_deductions = (pf + esi).quantize(Decimal("0.01"))
    total_deductions = total_deductions + 208
    net_salary = (earned_gross - total_deductions).quantize(Decimal("0.01"))

    # -------------------------
    # Context
    # -------------------------
    context = {
        "emp_code": emp_code,
        "emp_name": emp_name,

        "month": month,
        "months": months,
        "year": year,
        "years": years,

        "doj": doj.date() if doj else "",
        "department": department,
        "location": location,
        "designation": designation,
        "employementtype": employementtype,

        "total_days": total_days,
        "working_days": working_days,
        "present_days": paid_days,
        "absent_days": absent_days,

        "basic": basic,
        "hra": hra,
        "allowance": allowance,
        "pf": pf,
        "esi": esi,

        "gross_salary": earned_gross,
        "total_deductions": total_deductions,
        "net_salary": net_salary,
    }

    return render(request, "myapp/salary_slip.html", context)

def salary_detail(request, emp_code):
    emp_code = urllib.parse.unquote(emp_code)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                e.EmployeeCode,
                e.EmployeeName,
                d.DepartmentFName,
                s.TotalGross
            FROM Employees e
            LEFT JOIN Departments d ON e.DepartmentId = d.DepartmentId
            LEFT JOIN EmployeeSalary s ON e.EmployeeCode = s.EmployeeCode
            WHERE e.EmployeeCode = %s
        """, [emp_code])
        row = cursor.fetchone()

    if not row:
        return redirect("salary_list")

    gross = Decimal(row[3] or 0)

    basic, hra, da, pf, esi = breakup_from_earned_gross(gross)
    pt = 208
    salary = {
        "emp_code": row[0],
        "name": row[1],
        "department": row[2],
        "gross": gross,
        "basic": basic,
        "hra": hra,
        "da": da,
        "pf": pf,
        "esi": esi,
        "pt": pt,
        "net": gross - pf - esi - pt,

    }

    return render(request, "myapp/salary_detail.html", {
        "salary": salary
    })
def salary_edit(request, emp_code):
    emp_code = urllib.parse.unquote(emp_code)

    if request.method == "POST":
        gross = Decimal(request.POST.get("total_gross") or 0)

        with connection.cursor() as cursor:
            cursor.execute("""
                MERGE EmployeeSalary AS t
                USING (SELECT %s AS EmployeeCode) s
                ON t.EmployeeCode = s.EmployeeCode
                WHEN MATCHED THEN
                    UPDATE SET TotalGross = %s
                WHEN NOT MATCHED THEN
                    INSERT (EmployeeCode, TotalGross)
                    VALUES (%s, %s);
            """, [emp_code, gross, emp_code, gross])

        return redirect("salary_detail", emp_code=emp_code)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT TotalGross
            FROM EmployeeSalary
            WHERE EmployeeCode=%s
        """, [emp_code])
        gross = cursor.fetchone()
        gross = gross[0] if gross else 0

    return render(request, "myapp/salary_edit.html", {
        "emp_code": emp_code,
        "gross": gross
    })
def org_play(request):
    """
    Builds a hierarchical tree of employees for the Org Chart.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                e.EmployeeName, 
                e.EmployeeCode, 
                e.Team AS ManagerCode, 
                e.Designation,
                d.DepartmentFName AS Department,
                e.EmployeePhoto
            FROM dbo.Employees e
            LEFT JOIN dbo.Departments d ON e.DepartmentId = d.DepartmentId
            WHERE e.EmployeeCode IS NOT NULL
        """)
        rows = cursor.fetchall()

    employees = {}
    # First pass: Create all employee nodes
    for name, code, manager_code, designation, dept, photo_bin in rows:
        photo_b64 = None
        if photo_bin:
            try:
                photo_bytes = photo_bin
                if isinstance(photo_bytes, memoryview):
                    photo_bytes = photo_bytes.tobytes()
                photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
            except Exception:
                photo_b64 = None

        employees[code] = {
            "name": name,
            "code": code,
            "manager_code": manager_code,
            "designation": designation or "Employee",
            "department": dept or "—",
            "photo": photo_b64,
            "children": []
        }

    # Second pass: Build hierarchy
    root_codes = []
    for code, emp in employees.items():
        manager_code = emp["manager_code"]
        if manager_code and manager_code in employees:
            employees[manager_code]["children"].append(emp)
        else:
            root_codes.append(code)

    # Recursive function to set depth
    def set_depth(node, depth):
        node["level"] = depth
        for child in node["children"]:
            set_depth(child, depth + 1)

    # Set depth for all root trees
    root_nodes = []
    for code in root_codes:
        root_node = employees[code]
        set_depth(root_node, 0)
        root_nodes.append(root_node)

    # Sort children alphabetically
    for emp in employees.values():
        emp["children"].sort(key=lambda x: x["name"])

    # Prioritize TOP_EMP_CODE
    TOP_EMP_CODE = "MLV101"
    if TOP_EMP_CODE in employees:
        root_nodes.sort(key=lambda x: x["code"] != TOP_EMP_CODE)

    return render(request, "myapp/org_chart.html", {
        "roots": root_nodes
    })
def download_mis_template(request):
    import io
    import pandas as pd
    from django.http import HttpResponse

    # Grouped headers for human readability (Row 1)
    row1 = ['' for _ in range(6)] + ['FPC Production']*5 + ['Audit Production']*7 + ['Overall Quality']*4 + ['Assessment Scores']*3
    
    # Official mapping headers (Row 2 - this is what header=1 looks for)
    row2 = [
        'S no', 'Date', 'Project Name', 'MLV ID', 'MLV Name', 'Team',
        'Total Count', 'Total Page', 'Total Icd', 'Target', 'Achived %', # FPC
        'Total Count', 'Total Page', 'Total Icd', 'Error Count', 'Target', 'U1%', 'Achived %', # Audit
        'Total Chart', 'Total Icd', 'Total Errors', 'QA%', # Quality
        'Total Ques', 'Total Correct', '%' # Assessment
    ]

    df = pd.DataFrame([row1, row2])
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, header=False, sheet_name='Master_data')
    
    output.seek(0)
    response = HttpResponse(
        output.read(), 
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=MIS_Data_Template.xlsx'
    return response

def upload_master_data(request):
    if request.method == 'POST' and request.FILES.get('excel_file'):
        excel_file = request.FILES['excel_file']
        try:
            # First, check if sheet exists
            xl = pd.ExcelFile(excel_file)
            if 'Master_data' not in xl.sheet_names:
                messages.error(request, "Excel file does not have a sheet named 'Master_data'. Please use the official template.")
                return redirect('upload_master_data')

            # Use the second row (index 1) as header
            df = pd.read_excel(excel_file, sheet_name='Master_data', header=1)
            df = df.fillna(0)
            
            required_cols = ['Date', 'Project Name', 'MLV ID', 'MLV Name']
            existing_cols = df.columns.tolist()
            
            missing = [col for col in required_cols if col not in existing_cols]
            if missing:
                messages.error(request, f"Missing required columns in Row 2: {', '.join(missing)}. Found: {existing_cols}")
                return redirect('upload_master_data')

            count = 0
            
            def safe_int(val):
                try:
                    if pd.isna(val) or val == '': return 0
                    return int(float(val))
                except (ValueError, TypeError):
                    return 0

            def safe_float(val):
                try:
                    if pd.isna(val) or val == '': return 0.0
                    return float(val)
                except (ValueError, TypeError):
                    return 0.0

            with connection.cursor() as cursor:
                for index, row in df.iterrows():
                    # Skip rows where mandatory ID is missing
                    if not row.get('MLV ID') or str(row['MLV ID']).strip() == '0' or str(row['MLV ID']).lower() == 'nan':
                        continue

                    row_date = row['Date']
                    if hasattr(row_date, 'date'):
                        row_date = row_date.date()
                    elif isinstance(row_date, str):
                        try:
                            row_date = datetime.strptime(row_date, '%Y-%m-%d').date()
                        except:
                            continue
                    elif pd.isna(row_date) or row_date == 0:
                        continue

                    def scale_perc(val):
                        v = safe_float(val)
                        if v <= 5.0 and v > 0:
                            return v * 100
                        return v

                    # Mapping based on the second row headers
                    # Pandas adds .1, .2 to duplicate column names
                    params = [
                        str(row['MLV ID']),
                        row_date,
                        str(row['Project Name']),
                        safe_int(row.get('S no', 0)) if row.get('S no') else None,
                        str(row['MLV Name']),
                        str(row.get('Team', '')),
                        safe_int(row.get('Total Count', 0)),
                        safe_int(row.get('Total Page', 0)),
                        safe_int(row.get('Total Icd', 0)),
                        safe_int(row.get('Target', 0)),
                        scale_perc(row.get('Achived %', 0)),
                        safe_int(row.get('Total Count.1', 0)),
                        safe_int(row.get('Total Page.1', 0)),
                        safe_int(row.get('Total Icd.1', 0)),
                        safe_int(row.get('Error Count', 0)),
                        safe_int(row.get('Target.1', 0)),
                        scale_perc(row.get('U1%', 0)),
                        scale_perc(row.get('Achived %.1', 0)),
                        safe_int(row.get('Total Chart', 0)),
                        safe_int(row.get('Total Icd.2', 0)),
                        safe_int(row.get('Total Errors', 0)),
                        scale_perc(row.get('QA%', 0)),
                        safe_int(row.get('Total Ques', 0)),
                        safe_int(row.get('Total Correct', 0)),
                        scale_perc(row.get('%', 0))
                    ]

                    # Raw SQL MERGE for MSSQL
                    sql = """
                    MERGE INTO MasterData AS target
                    USING (SELECT %s AS MLV_ID, %s AS Date, %s AS ProjectName) AS source
                    ON (target.MLV_ID = source.MLV_ID AND target.Date = source.Date AND target.ProjectName = source.ProjectName)
                    WHEN MATCHED THEN
                        UPDATE SET 
                            SNo = %s, MLV_Name = %s, Team = %s,
                            FPC_TotalCount = %s, FPC_TotalPage = %s, FPC_TotalIcd = %s, FPC_Target = %s, FPC_AchievedPercent = %s,
                            Audit_TotalCount = %s, Audit_TotalPage = %s, Audit_TotalIcd = %s, Audit_ErrorCount = %s, Audit_Target = %s, Audit_U1Percent = %s, Audit_AchievedPercent = %s,
                            Quality_TotalChart = %s, Quality_TotalIcd = %s, Quality_TotalErrors = %s, Quality_QAPercent = %s,
                            Assessment_TotalQues = %s, Assessment_TotalCorrect = %s, Assessment_Percent = %s
                    WHEN NOT MATCHED THEN
                        INSERT (MLV_ID, Date, ProjectName, SNo, MLV_Name, Team, 
                                FPC_TotalCount, FPC_TotalPage, FPC_TotalIcd, FPC_Target, FPC_AchievedPercent,
                                Audit_TotalCount, Audit_TotalPage, Audit_TotalIcd, Audit_ErrorCount, Audit_Target, Audit_U1Percent, Audit_AchievedPercent,
                                Quality_TotalChart, Quality_TotalIcd, Quality_TotalErrors, Quality_QAPercent,
                                Assessment_TotalQues, Assessment_TotalCorrect, Assessment_Percent)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """
                    # Parameters duplicated for MERGE MATCHED and NOT MATCHED paths
                    merge_params = params[:3] + params[3:] + params
                    cursor.execute(sql, merge_params)
                    count += 1
            
            messages.success(request, f"Successfully uploaded {count} records using Raw SQL.")
            return redirect('upload_master_data')
        except Exception as e:
            messages.error(request, f"Error processing Excel: {str(e)}")
            return redirect('upload_master_data')
            
    return render(request, 'myapp/upload_master_data.html')

def mis_dashboard(request):
    emp_code = request.session.get('emp_code')
    role = request.session.get('allocate_position', '')
    if not emp_code:
        return redirect('emp_login')
    
    from_date = request.GET.get('from_date', '1900-01-01')
    to_date = request.GET.get('to_date', '2100-12-31')
    user_id_filter = request.GET.get('user_id', '')
    tl_code_filter = request.GET.get('tl_code', '')
    
    def get_all_subordinates(cursor, supervisor_codes):
        if not supervisor_codes:
            return []
        placeholders = ",".join(["%s"] * len(supervisor_codes))
        cursor.execute(f"SELECT EmployeeCode FROM Employees WHERE Team IN ({placeholders})", supervisor_codes)
        subs = [r[0] for r in cursor.fetchall()]
        if subs:
            return subs + get_all_subordinates(cursor, subs)
        return []

    with connection.cursor() as cursor:
        is_team_lead = False
        tl_list = []
        user_list = []
        
        # Hierarchy-based visibility
        all_subs = get_all_subordinates(cursor, [emp_code])
        if role in ['IT', 'HR']:
            # Global access
            all_subs = [] # Special case for global
        
        # Determine visibility and fetch filter lists
        if role in ['MANAGER', 'AM', 'IT', 'HR'] or all_subs:
            is_team_lead = True # Show employee column
            
            # TL List: Employees in hierarchy who are TLs
            if role in ['IT', 'HR']:
                cursor.execute("SELECT EmployeeCode, EmployeeName FROM Employees WHERE AllocatePosition = 'TL' ORDER BY EmployeeName")
            elif all_subs:
                placeholders = ",".join(["%s"] * len(all_subs))
                cursor.execute(f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode IN ({placeholders}) AND AllocatePosition = 'TL' ORDER BY EmployeeName", all_subs)
            else:
                tl_list = [] # No subordinates, no TLs
                # Skip execute if no subs
                goto_users = True 
            
            if not locals().get('goto_users'):
                tl_list = [dict(zip(['code', 'name'], row)) for row in cursor.fetchall()]
            
            # User List
            if tl_code_filter:
                cursor.execute("SELECT EmployeeCode, EmployeeName FROM Employees WHERE Team = %s ORDER BY EmployeeName", [tl_code_filter])
            elif role in ['IT', 'HR']:
                cursor.execute("SELECT EmployeeCode, EmployeeName FROM Employees ORDER BY EmployeeName")
            elif all_subs:
                placeholders = ",".join(["%s"] * len(all_subs))
                cursor.execute(f"SELECT EmployeeCode, EmployeeName FROM Employees WHERE (EmployeeCode IN ({placeholders}) OR EmployeeCode = %s) ORDER BY EmployeeName", all_subs + [emp_code])
                user_list = [dict(zip(['code', 'name'], row)) for row in cursor.fetchall()]
            else:
                cursor.execute("SELECT EmployeeCode, EmployeeName FROM Employees WHERE EmployeeCode = %s ORDER BY EmployeeName", [emp_code])
                user_list = [dict(zip(['code', 'name'], row)) for row in cursor.fetchall()]
        
        # Base query for records
        sql_records = "SELECT * FROM MasterData WHERE Date BETWEEN %s AND %s"
        params = [from_date, to_date]
        
        # Apply Visibility / Filters
        if role in ['IT', 'HR']:
            if user_id_filter:
                sql_records += " AND MLV_ID = %s"
                params.append(user_id_filter)
            elif tl_code_filter:
                sql_records += " AND MLV_ID IN (SELECT EmployeeCode FROM Employees WHERE Team = %s)"
                params.append(tl_code_filter)
        elif all_subs:
            # Hierarchical view
            if user_id_filter and user_id_filter in all_subs:
                sql_records += " AND MLV_ID = %s"
                params.append(user_id_filter)
            elif tl_code_filter and tl_code_filter in all_subs:
                sql_records += " AND MLV_ID IN (SELECT EmployeeCode FROM Employees WHERE Team = %s)"
                params.append(tl_code_filter)
            else:
                # Default: see all subordinates + self
                view_ids = all_subs + [emp_code]
                placeholders = ",".join(["%s"] * len(view_ids))
                sql_records += f" AND MLV_ID IN ({placeholders})"
                params.extend(view_ids)
        else:
            # Regular user visibility (no subordinates)
            sql_records += " AND MLV_ID = %s"
            params.append(emp_code)
            
        sql_records += " ORDER BY Date DESC"
        cursor.execute(sql_records, params)
        
        # Fetch records as dict-like objects
        columns = [col[0] for col in cursor.description]
        records = []
        for row in cursor.fetchall():
            r = dict(zip(columns, row))
            # Fix scaling: if stored as fraction (1.0 = 100%), scale to 100
            for k in ['FPC_AchievedPercent', 'Audit_AchievedPercent', 'Quality_QAPercent', 'Assessment_Percent']:
                if r.get(k) is not None and float(r[k]) <= 5.0 and float(r[k]) > 0:
                    r[k] = float(r[k]) * 100
            records.append(r)
        
        # Summary Query (Apply same hierarchical filters)
        sql_summary = """
        SELECT 
            SUM(FPC_TotalCount), SUM(FPC_TotalPage), SUM(FPC_TotalIcd), SUM(FPC_Target), AVG(FPC_AchievedPercent),
            SUM(Audit_TotalCount), SUM(Audit_TotalPage), SUM(Audit_TotalIcd), SUM(Audit_ErrorCount), SUM(Audit_Target), AVG(Audit_U1Percent), AVG(Audit_AchievedPercent),
            SUM(Quality_TotalChart), SUM(Quality_TotalIcd), SUM(Quality_TotalErrors), AVG(Quality_QAPercent),
            SUM(Assessment_TotalQues), SUM(Assessment_TotalCorrect), AVG(Assessment_Percent)
        FROM MasterData
        WHERE Date BETWEEN %s AND %s
        """
        summary_params = [from_date, to_date]
        
        if role in ['IT', 'HR']:
            if user_id_filter:
                sql_summary += " AND MLV_ID = %s"
                summary_params.append(user_id_filter)
            elif tl_code_filter:
                sql_summary += " AND MLV_ID IN (SELECT EmployeeCode FROM Employees WHERE Team = %s)"
                summary_params.append(tl_code_filter)
        elif all_subs:
            if user_id_filter and user_id_filter in all_subs:
                sql_summary += " AND MLV_ID = %s"
                summary_params.append(user_id_filter)
            elif tl_code_filter and tl_code_filter in all_subs:
                sql_summary += " AND MLV_ID IN (SELECT EmployeeCode FROM Employees WHERE Team = %s)"
                summary_params.append(tl_code_filter)
            else:
                view_ids = all_subs + [emp_code]
                placeholders = ",".join(["%s"] * len(view_ids))
                sql_summary += f" AND MLV_ID IN ({placeholders})"
                summary_params.extend(view_ids)
        else:
            sql_summary += " AND MLV_ID = %s"
            summary_params.append(emp_code)
            
        cursor.execute(sql_summary, summary_params)
        s = cursor.fetchone()
        
        def per(num, den):
            if den and den > 0:
                return (float(num) / float(den)) * 100
            return 0

        def scale_avg(val):
            if val is not None and float(val) <= 5.0 and float(val) > 0:
                return float(val) * 100
            return val or 0

        summary = {
            'fpc_count': s[0] or 0, 
            'fpc_page': s[1] or 0, 
            'fpc_icd': s[2] or 0, 
            'fpc_target': s[3] or 0, 
            'fpc_avg': per(s[0], s[3]),  # Production % = Total Count / Target
            
            'audit_count': s[5] or 0, 
            'audit_page': s[6] or 0, 
            'audit_icd': s[7] or 0, 
            'audit_error': s[8] or 0, 
            'audit_target': s[9] or 0, 
            'audit_u1': scale_avg(s[10]), 
            'audit_avg': per(s[5], s[9]), # Audit Achieved% = Total Count / Target
            
            'quality_chart': s[12] or 0, 
            'quality_icd': s[13] or 0, 
            'quality_error': s[14] or 0, 
            'quality_avg': (1 - (float(s[14] or 0) / float(s[13]))) * 100 if s[13] and s[13] > 0 else 0, # Quality % = (1 - Error / ICD) * 100
            'quality_efr': per(s[14], s[13]), # EFR % = Total Error / Total ICD
            
            'assess_ques': s[16] or 0, 
            'assess_correct': s[17] or 0, 
            'assess_avg': per(s[17], s[16]) # Assessment % = Achieved Score / Total Score
        }

    context = {
        'records': records,
        'is_team_lead': is_team_lead,
        'summary': summary,
        'from_date': request.GET.get('from_date', ''),
        'to_date': request.GET.get('to_date', ''),
        'emp_name': request.session.get('emp_name'),
        'role': role,
        'tl_list': tl_list,
        'user_list': user_list,
        'selected_user_id': user_id_filter,
        'selected_tl_code': tl_code_filter,
    }
    return render(request, 'myapp/mis_dashboard.html', context)
    
def export_mis_data(request):
    emp_code = request.session.get('emp_code')
    role = request.session.get('allocate_position', '')
    if not emp_code:
        return redirect('emp_login')
    
    from_date = request.GET.get('from_date', '1900-01-01')
    to_date = request.GET.get('to_date', '2100-12-31')
    user_id_filter = request.GET.get('user_id', '')
    tl_code_filter = request.GET.get('tl_code', '')
    
    def get_all_subordinates(cursor, supervisor_codes):
        if not supervisor_codes:
            return []
        placeholders = ",".join(["%s"] * len(supervisor_codes))
        cursor.execute(f"SELECT EmployeeCode FROM Employees WHERE Team IN ({placeholders})", supervisor_codes)
        subs = [r[0] for r in cursor.fetchall()]
        if subs:
            return subs + get_all_subordinates(cursor, subs)
        return []

    with connection.cursor() as cursor:
        all_subs = get_all_subordinates(cursor, [emp_code])
        if role in ['IT', 'HR']:
            all_subs = [] # Global

        # Base query for records
        sql_records = "SELECT * FROM MasterData WHERE Date BETWEEN %s AND %s"
        params = [from_date, to_date]
        
        # Apply Visibility / Filters (same as mis_dashboard)
        if role in ['IT', 'HR']:
            if user_id_filter:
                sql_records += " AND MLV_ID = %s"
                params.append(user_id_filter)
            elif tl_code_filter:
                sql_records += " AND MLV_ID IN (SELECT EmployeeCode FROM Employees WHERE Team = %s)"
                params.append(tl_code_filter)
        elif all_subs:
            if user_id_filter and user_id_filter in all_subs:
                sql_records += " AND MLV_ID = %s"
                params.append(user_id_filter)
            elif tl_code_filter and tl_code_filter in all_subs:
                sql_records += " AND MLV_ID IN (SELECT EmployeeCode FROM Employees WHERE Team = %s)"
                params.append(tl_code_filter)
            else:
                view_ids = all_subs + [emp_code]
                placeholders = ",".join(["%s"] * len(view_ids))
                sql_records += f" AND MLV_ID IN ({placeholders})"
                params.extend(view_ids)
        else:
            sql_records += " AND MLV_ID = %s"
            params.append(emp_code)
            
        sql_records += " ORDER BY Date DESC"
        cursor.execute(sql_records, params)
        columns = [col[0] for col in cursor.description]
        data = cursor.fetchall()
        
    df = pd.DataFrame(data, columns=columns)
    
    # Fix scaling for Excel output
    perc_cols = ['FPC_AchievedPercent', 'Audit_AchievedPercent', 'Quality_QAPercent', 'Assessment_Percent', 'Audit_U1Percent']
    for col in perc_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: x * 100 if x is not None and float(x) <= 5.0 and float(x) > 0 else (x or 0))

    # Reorder/Select columns for export
    export_cols = [
        'Date', 'ProjectName', 'MLV_ID', 'MLV_Name', 'Team',
        'FPC_TotalCount', 'FPC_TotalPage', 'FPC_TotalIcd', 'FPC_Target', 'FPC_AchievedPercent',
        'Audit_TotalCount', 'Audit_TotalPage', 'Audit_TotalIcd', 'Audit_ErrorCount', 'Audit_Target', 'Audit_U1Percent', 'Audit_AchievedPercent',
        'Quality_TotalChart', 'Quality_TotalIcd', 'Quality_TotalErrors', 'Quality_QAPercent',
        'Assessment_TotalQues', 'Assessment_TotalCorrect', 'Assessment_Percent'
    ]
    df = df[[c for c in export_cols if c in df.columns]]
    
    # User-friendly column names
    df.columns = [c.replace('_', ' ') for c in df.columns]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='MIS_Data')
    
    output.seek(0)
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=MIS_Performance_Report.xlsx'
    return response


# Announcements CRUD
def announcement_list(request):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
    
    announcements = CompanyAnnouncement.objects.all().order_by('-CreatedAt')
    return render(request, "myapp/announcement_list.html", {"announcements": announcements})

def add_announcement(request):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    if request.method == "POST":
        title = request.POST.get("title")
        content = request.POST.get("content")
        announcement = CompanyAnnouncement.objects.create(Title=title, Content=content)
        
        # Notify All Employees
        all_emps = Employees.objects.all()
        for emp in all_emps:
            send_notification(
                sender_code=request.session["emp_code"],
                receiver_code=emp.EmployeeCode,
                title="New Announcement",
                message=f"A new announcement has been posted: {title}",
                n_type='ANNOUNCEMENT',
                related_id=announcement.id
            )
        
        return redirect("announcement_list")
        
    return render(request, "myapp/add_edit_announcement.html")

def edit_announcement(request, pk):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    announcement = CompanyAnnouncement.objects.get(pk=pk)
    if request.method == "POST":
        announcement.Title = request.POST.get("title")
        announcement.Content = request.POST.get("content")
        announcement.IsActive = request.POST.get("is_active") == "on"
        announcement.save()
        return redirect("announcement_list")
        
    return render(request, "myapp/add_edit_announcement.html", {"announcement": announcement})

def delete_announcement(request, pk):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    CompanyAnnouncement.objects.filter(pk=pk).delete()
    return redirect("announcement_list")

# Onboarding Views
def candidate_register(request):
    emp_code = request.GET.get("emp_code", "").strip()
    if not emp_code:
        emp_code = request.session.get("emp_code", "").strip()
        
    initial_data = {
        "full_name": "", "email": "", "contact": "", "father_name": "", 
        "mother_name": "", "dob": "", "doj": "", "gender": "", "place_of_birth": "", 
        "blood_group": "", "aadhaar": "", "residential_address": "", 
        "permanent_address": "", "bank_name": "", "account_number": "", "ifsc": ""
    }
    
    existing_photo = None
    if emp_code:
        # 1. Try to find photo in existing OnboardingRequest
        req = OnboardingRequest.objects.filter(EmployeeCode=emp_code).order_by("-CreatedAt").first()
        if req and req.Photo:
            try:
                photo_bytes = req.Photo
                if isinstance(photo_bytes, memoryview):
                    photo_bytes = photo_bytes.tobytes()
                existing_photo = base64.b64encode(photo_bytes).decode('utf-8')
            except Exception:
                pass

        # 2. If no photo in OnboardingRequest, try Employees table
        if not existing_photo:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT e.EmployeeName, e.Email, e.ContactNo, e.FatherName, e.MotherName, e.DOB, e.Gender, e.PlaceOfBirth, e.BLOODGROUP, e.AadhaarNumber, e.ResidentialAddress, e.PermanentAddress, b.BankName, b.AccountNumber, b.IFSCCode, e.DOJ, e.EmployeePhoto,
                           e.MaritalStatus, e.Designation, e.Qualification, e.TotalExperience, e.PreviousOrganization, e.BGVDetails, e.AAPCCertification, e.CredentialNumber, e.EmergencyContactNumber, e.PANNumber, e.UANNumber
                    FROM Employees e
                    LEFT JOIN EmployeeBankDetails b ON e.EmployeeCode = b.EmployeeCode
                    WHERE e.EmployeeCode = %s
                """, [emp_code])
                row = cursor.fetchone()
                if row:
                    initial_data = {
                        "full_name": row[0], "email": row[1], "contact": row[2],
                        "father_name": row[3], "mother_name": row[4], "dob": row[5] if row[5] else "",
                        "gender": row[6], "place_of_birth": row[7], "blood_group": row[8],
                        "aadhaar": row[9], "residential_address": row[10], "permanent_address": row[11],
                        "bank_name": row[12], "account_number": row[13], "ifsc": row[14],
                        "doj": row[15] if row[15] else "",
                        "marital_status": row[17], "designation": row[18], "qualification": row[19],
                        "total_experience": row[20], "prev_org": row[21], "bgv_details": row[22],
                        "aapc_cert": row[23], "credential_no": row[24], "emergency_contact": row[25],
                        "pan_number": row[26], "uan_number": row[27]
                    }
                    if not existing_photo:
                        photo_bin = row[16]
                        if photo_bin:
                            try:
                                photo_bytes = photo_bin
                                if isinstance(photo_bytes, memoryview):
                                    photo_bytes = photo_bytes.tobytes()
                                existing_photo = base64.b64encode(photo_bytes).decode('utf-8')
                            except Exception:
                                pass

    if request.method == "POST":
        full_name = request.POST.get("full_name", "")
        email = request.POST.get("email", "")
        contact = request.POST.get("contact", "")
        father = request.POST.get("father_name", "")
        mother = request.POST.get("mother_name", "")
        dob = request.POST.get("dob", "")
        doj = request.POST.get("doj", "")
        gender = request.POST.get("gender", "")
        pob = request.POST.get("place_of_birth", "")
        blood = request.POST.get("blood_group", "")
        aadhaar = request.POST.get("aadhaar", "")
        res_addr = request.POST.get("residential_address", "")
        perm_addr = request.POST.get("permanent_address", "")
        bank = request.POST.get("bank_name", "")
        acc_no = request.POST.get("account_number", "")
        ifsc = request.POST.get("ifsc", "")
        
        # New fields
        marital_status = request.POST.get("marital_status", "")
        qualification = request.POST.get("qualification", "")
        designation = request.POST.get("designation", "")
        total_exp = request.POST.get("total_experience", "")
        prev_org = request.POST.get("prev_org", "")
        bgv = request.POST.get("bgv_details", "")
        aapc = request.POST.get("aapc_cert", "")
        credential = request.POST.get("credential_no", "")
        emergency = request.POST.get("emergency_contact", "")
        pan = request.POST.get("pan_number", "")
        uan = request.POST.get("uan_number", "")
        
        # Use existing emp_code if available in session/context
        final_emp_code = emp_code or request.session.get("emp_code", "").strip()

        photo = request.FILES.get("photo")
        photo_bin = None
        if photo:
            photo_bin = photo.read()
            
        OnboardingRequest.objects.create(
            FullName=full_name, Email=email, ContactNo=contact,
            FatherName=father, MotherName=mother, DOB=dob or None,
            DateOfJoining=doj or None,
            Gender=gender, PlaceOfBirth=pob, BloodGroup=blood,
            AadhaarNumber=aadhaar, ResidentialAddress=res_addr,
            PermanentAddress=perm_addr, BankName=bank,
            AccountNumber=acc_no, IFSCCode=ifsc, Photo=photo_bin,
            EmployeeCode=final_emp_code,
            # New fields
            Qualification=qualification, MaritalStatus=marital_status,
            Designation=designation, TotalExperience=total_exp,
            PreviousOrganization=prev_org, BGVDetails=bgv,
            AAPCCertification=aapc, CredentialNumber=credential,
            EmergencyContactNumber=emergency, PANNumber=pan, UANNumber=uan
        )
        return render(request, "myapp/register_success.html")
        
    return render(request, "myapp/candidate_register.html", {
        "initial_data": initial_data, 
        "emp_code": emp_code,
        "existing_photo": existing_photo
    })

def onboarding_list(request):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
    
    requests = OnboardingRequest.objects.all().order_by("-CreatedAt")
    return render(request, "myapp/onboarding_list.html", {"requests": requests})

def onboarding_detail(request, pk):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    req = OnboardingRequest.objects.get(pk=pk)
    photo_b64 = None
    if req.Photo:
        photo_b64 = base64.b64encode(req.Photo).decode('utf-8')
    elif req.EmployeeCode:
        # Fallback to existing employee photo if this is an update and no new photo was provided
        with connection.cursor() as cursor:
            cursor.execute("SELECT EmployeePhoto FROM Employees WHERE EmployeeCode = %s", [req.EmployeeCode])
            row = cursor.fetchone()
            if row and row[0]:
                photo_bytes = row[0]
                if isinstance(photo_bytes, memoryview):
                    photo_bytes = photo_bytes.tobytes()
                photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
        
    return render(request, "myapp/onboarding_detail.html", {"req": req, "photo": photo_b64})

@transaction.atomic
def onboarding_action(request, pk, action):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    onboard_req = OnboardingRequest.objects.get(pk=pk)
    
    if action == "accept":
        # Get Employee Code from the saved OnboardingRequest
        new_code = onboard_req.EmployeeCode
        if not new_code:
            messages.error(request, "This application does not have an associated Employee Code.")
            return redirect("onboarding_detail", pk=pk)

        # Process Employee Record
        with connection.cursor() as cursor:
            # Check if employee already exists
            cursor.execute("SELECT 1 FROM Employees WHERE EmployeeCode = %s", [new_code])
            exists = cursor.fetchone()
            
            if exists:
                # Update existing employee
                # Only update photo if provided
                if onboard_req.Photo:
                    cursor.execute("""
                        UPDATE Employees 
                        SET EmployeeName = %s, FatherName = %s, MotherName = %s, DOB = %s, Gender = %s, 
                            PlaceOfBirth = %s, BLOODGROUP = %s, AadhaarNumber = %s, ResidentialAddress = %s, 
                            PermanentAddress = %s, ContactNo = %s, Email = %s, EmployeePhoto = %s, DOJ = %s,
                            Qualification = %s, MaritalStatus = %s, Designation = %s, TotalExperience = %s,
                            PreviousOrganization = %s, BGVDetails = %s, AAPCCertification = %s,
                            CredentialNumber = %s, EmergencyContactNumber = %s, PANNumber = %s, UANNumber = %s
                        WHERE EmployeeCode = %s
                    """, [
                        onboard_req.FullName, onboard_req.FatherName, onboard_req.MotherName, 
                        onboard_req.DOB, onboard_req.Gender, onboard_req.PlaceOfBirth, 
                        onboard_req.BloodGroup, onboard_req.AadhaarNumber, onboard_req.ResidentialAddress, 
                        onboard_req.PermanentAddress, onboard_req.ContactNo, onboard_req.Email, 
                        onboard_req.Photo, onboard_req.DateOfJoining or date.today(),
                        onboard_req.Qualification, onboard_req.MaritalStatus, onboard_req.Designation,
                        onboard_req.TotalExperience, onboard_req.PreviousOrganization, onboard_req.BGVDetails,
                        onboard_req.AAPCCertification, onboard_req.CredentialNumber,
                        onboard_req.EmergencyContactNumber, onboard_req.PANNumber, onboard_req.UANNumber,
                        new_code
                    ])
                else:
                    cursor.execute("""
                        UPDATE Employees 
                        SET EmployeeName = %s, FatherName = %s, MotherName = %s, DOB = %s, Gender = %s, 
                            PlaceOfBirth = %s, BLOODGROUP = %s, AadhaarNumber = %s, ResidentialAddress = %s, 
                            PermanentAddress = %s, ContactNo = %s, Email = %s, DOJ = %s,
                            Qualification = %s, MaritalStatus = %s, Designation = %s, TotalExperience = %s,
                            PreviousOrganization = %s, BGVDetails = %s, AAPCCertification = %s,
                            CredentialNumber = %s, EmergencyContactNumber = %s, PANNumber = %s, UANNumber = %s
                        WHERE EmployeeCode = %s
                    """, [
                        onboard_req.FullName, onboard_req.FatherName, onboard_req.MotherName, 
                        onboard_req.DOB, onboard_req.Gender, onboard_req.PlaceOfBirth, 
                        onboard_req.BloodGroup, onboard_req.AadhaarNumber, onboard_req.ResidentialAddress, 
                        onboard_req.PermanentAddress, onboard_req.ContactNo, onboard_req.Email, 
                        onboard_req.DateOfJoining or date.today(),
                        onboard_req.Qualification, onboard_req.MaritalStatus, onboard_req.Designation,
                        onboard_req.TotalExperience, onboard_req.PreviousOrganization, onboard_req.BGVDetails,
                        onboard_req.AAPCCertification, onboard_req.CredentialNumber,
                        onboard_req.EmergencyContactNumber, onboard_req.PANNumber, onboard_req.UANNumber,
                        new_code
                    ])
                action_msg = "updated"
            else:
                # Insert new employee (Omit EmployeeId as it is an identity column)
                cursor.execute("""
                    INSERT INTO Employees (EmployeeCode, EmployeeName, FatherName, MotherName, DOB, Gender, PlaceOfBirth, BLOODGROUP, AadhaarNumber, ResidentialAddress, PermanentAddress, ContactNo, Email, EmployeePhoto, DepartmentId, Designation, EmployementType, DOJ,
                                           Qualification, MaritalStatus, TotalExperience, PreviousOrganization, BGVDetails, AAPCCertification, CredentialNumber, EmergencyContactNumber, PANNumber, UANNumber)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, [
                    new_code, onboard_req.FullName, onboard_req.FatherName,
                    onboard_req.MotherName, onboard_req.DOB, onboard_req.Gender,
                    onboard_req.PlaceOfBirth, onboard_req.BloodGroup, onboard_req.AadhaarNumber,
                    onboard_req.ResidentialAddress, onboard_req.PermanentAddress,
                    onboard_req.ContactNo, onboard_req.Email, onboard_req.Photo,
                    1, onboard_req.Designation or "Trainee", "Full Time", onboard_req.DateOfJoining or date.today(),
                    onboard_req.Qualification, onboard_req.MaritalStatus, onboard_req.TotalExperience,
                    onboard_req.PreviousOrganization, onboard_req.BGVDetails, onboard_req.AAPCCertification,
                    onboard_req.CredentialNumber, onboard_req.EmergencyContactNumber, onboard_req.PANNumber, onboard_req.UANNumber
                ])
                action_msg = "created"
            
            # Upsert Bank Details
            cursor.execute("SELECT 1 FROM EmployeeBankDetails WHERE EmployeeCode = %s", [new_code])
            bank_exists = cursor.fetchone()
            if bank_exists:
                cursor.execute("""
                    UPDATE EmployeeBankDetails SET BankName = %s, AccountNumber = %s, IFSCCode = %s 
                    WHERE EmployeeCode = %s
                """, [onboard_req.BankName, onboard_req.AccountNumber, onboard_req.IFSCCode, new_code])
            else:
                cursor.execute("""
                    INSERT INTO EmployeeBankDetails (EmployeeCode, BankName, AccountNumber, IFSCCode)
                    VALUES (%s, %s, %s, %s)
                """, [new_code, onboard_req.BankName, onboard_req.AccountNumber, onboard_req.IFSCCode])
            
        # Create Password only if it doesn't exist
        if not EmployeePassword.objects.filter(Employee_id=new_code).exists():
            from django.contrib.auth.hashers import make_password
            EmployeePassword.objects.create(
                Employee_id=new_code,
                PasswordHash=make_password("Welcome@123")
            )
        
        onboard_req.Status = "ACCEPTED"
        onboard_req.save()
        messages.success(request, f"Employee {onboard_req.FullName} ({new_code}) successfully {action_msg}.")
        
    elif action == "reject":
        onboard_req.Status = "REJECTED"
        onboard_req.save()
        messages.warning(request, f"Onboarding request for {onboard_req.FullName} rejected.")
    
    elif action == "delete":
        onboard_req.delete()
        messages.error(request, "Onboarding request deleted.")
        
    return redirect("onboarding_list")
def toggle_candidate_registration(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return redirect("dashboard")
    
    from .models import AppConfiguration
    config, created = AppConfiguration.objects.get_or_create(
        ConfigKey='show_candidate_register',
        defaults={'ConfigValue': 'True'}
    )
    
    if config.ConfigValue == 'True':
        config.ConfigValue = 'False'
    else:
        config.ConfigValue = 'True'
    
    config.save()
    return redirect('onboarding_list')

# ==========================================
# Expense & Reimbursement Module
# ==========================================

def expense_request(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    if request.method == "POST":
        expense_type = request.POST.get("expense_type")
        amount = request.POST.get("amount")
        description = request.POST.get("description")
        expense_date = request.POST.get("expense_date")
        receipt_file = request.FILES.get("receipt")
        
        emp_code = request.session["emp_code"]
        employee = Employees.objects.get(EmployeeCode=emp_code)
        
        # Binary storage for receipt
        receipt_bin = None
        if receipt_file:
            receipt_bin = receipt_file.read()
            
        claim = ExpenseClaim.objects.create(
            Employee=employee,
            ExpenseType=expense_type,
            Amount=amount,
            Description=description,
            ExpenseDate=expense_date,
            Receipt=receipt_bin,
            Status='PENDING_MANAGER'
        )
        
        # Notify Manager
        # We need to find the manager from EmployeeReporting
        reporters = EmployeeReporting.objects.filter(EmployeeCode=emp_code).first()
        if reporters and reporters.ReportsToEmpCode:
            send_notification(
                sender_code=emp_code,
                receiver_code=reporters.ReportsToEmpCode,
                title="New Expense Claim",
                message=f"{employee.EmployeeName} submitted a {expense_type} claim for {amount}.",
                n_type='EXPENSE',
                related_id=claim.id
            )
        
        messages.success(request, "Expense claim submitted successfully!")
        return redirect("expense_list")
        
    return render(request, "myapp/expense_request.html", {
        "expense_types": ['Travel', 'Food', 'Internet', 'Other']
    })

def expense_list(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    claims = ExpenseClaim.objects.filter(
        Employee__EmployeeCode=request.session["emp_code"]
    ).order_by('-CreatedAt')
    
    return render(request, "myapp/expense_list.html", {"claims": claims})

def expense_approval_list(request):
    emp_code = request.session.get("emp_code")
    role = request.session.get("allocate_position")
    
    if not emp_code:
        return redirect("emp_login")
        
    pending_claims = []
    
    # 1. Manager Level
    # Find employees who report to this user
    reporters = EmployeeReporting.objects.filter(ReportsToEmpCode=emp_code).values_list('EmployeeCode', flat=True)
    if reporters:
        mgr_claims = ExpenseClaim.objects.filter(
            Employee__EmployeeCode__in=reporters,
            Status='PENDING_MANAGER'
        )
        pending_claims.extend(mgr_claims)
        
    # 2. Finance Level
    if role == 'Finance':
        fin_claims = ExpenseClaim.objects.filter(Status='PENDING_FINANCE')
        pending_claims.extend(fin_claims)
        
    # 3. Admin Level
    if role in ['HR', 'IT']:
        adm_claims = ExpenseClaim.objects.filter(Status='PENDING_ADMIN')
        pending_claims.extend(adm_claims)
        
    # Remove duplicates if any (though status should prevent this)
    pending_claims = sorted(list(set(pending_claims)), key=lambda x: x.CreatedAt, reverse=True)
    
    return render(request, "myapp/expense_approval_list.html", {
        "pending_claims": pending_claims,
        "role": role
    })

@csrf_exempt
def expense_approve_action(request, claim_id, action):
    if not request.session.get("emp_code"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
        
    try:
        claim = ExpenseClaim.objects.get(id=claim_id)
        role = request.session.get("allocate_position")
        emp_code = request.session.get("emp_code")
        remarks = request.POST.get("remarks", "")
        
        if action == 'REJECT':
            claim.Status = 'REJECTED'
            if claim.Status == 'PENDING_MANAGER': claim.ManagerRemarks = remarks
            elif claim.Status == 'PENDING_FINANCE': claim.FinanceRemarks = remarks
            else: claim.AdminRemarks = remarks
            claim.save()
            
            # Notify Employee
            send_notification(
                sender_code=emp_code,
                receiver_code=claim.Employee.EmployeeCode,
                title="Expense Claim Rejected",
                message=f"Your {claim.ExpenseType} claim for {claim.Amount} has been rejected.",
                n_type='EXPENSE',
                related_id=claim.id
            )
            
            return redirect("expense_approval_list")

        # Approval Logic
        if claim.Status == 'PENDING_MANAGER':
            # Verify if this user is the actual manager
            is_mgr = EmployeeReporting.objects.filter(EmployeeCode=claim.Employee.EmployeeCode, ReportsToEmpCode=emp_code).exists()
            if not is_mgr:
                return JsonResponse({"status": "error", "message": "Not authorized as manager"}, status=403)
            claim.Status = 'PENDING_FINANCE'
            claim.ManagerRemarks = remarks
            
        elif claim.Status == 'PENDING_FINANCE':
            if role != 'Finance':
                return JsonResponse({"status": "error", "message": "Not authorized as Finance"}, status=403)
            claim.Status = 'PENDING_ADMIN'
            claim.FinanceRemarks = remarks
            
        elif claim.Status == 'PENDING_ADMIN':
            if role not in ['HR', 'IT']:
                return JsonResponse({"status": "error", "message": "Not authorized as Admin"}, status=403)
            # Admin can marks as PAID directly or APPROVED_FOR_PAYMENT
            claim.Status = 'PAID'
            claim.AdminRemarks = remarks
            
        claim.save()
        
        # Notify Employee on Approval/Paid
        status_msg = "approved and moved to next level"
        if claim.Status == 'PAID':
            status_msg = "paid"
        
        send_notification(
            sender_code=emp_code,
            receiver_code=claim.Employee.EmployeeCode,
            title="Expense Claim Updated",
            message=f"Your {claim.ExpenseType} claim for {claim.Amount} has been {status_msg}.",
            n_type='EXPENSE',
            related_id=claim.id
        )
        
        messages.success(request, f"Claim {claim_id} approved successfully.")
        return redirect("expense_approval_list")
        
    except ExpenseClaim.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Claim not found"}, status=404)

def expense_receipt_view(request, claim_id):
    # Security: Only employee or approver should see this.
    # For now, keeping it simple.
    try:
        claim = ExpenseClaim.objects.get(id=claim_id)
        if not claim.Receipt:
            return JsonResponse({"error": "No receipt found"}, status=404)
        return HttpResponse(claim.Receipt, content_type="image/jpeg")
    except Exception:
        return JsonResponse({"error": "Error loading receipt"}, status=404)

# ==========================================
# Asset Management Module
# ==========================================

def asset_list(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    role = request.session.get("allocate_position")
    if role not in ["HR", "IT"]:
        # Regular employees see only their allocated assets
        allocated_assets = AssetAllocation.objects.filter(
            Employee__EmployeeCode=request.session["emp_code"],
            ReturnedDate__isnull=True
        ).select_related('Asset')
        return render(request, "myapp/asset_list_employee.html", {"allocations": allocated_assets})

    # HR/IT see full inventory
    query = request.GET.get('q', '')
    assets = Asset.objects.all().order_by('AssetTag')
    if query:
        assets = assets.filter(
            models.Q(AssetTag__icontains=query) | 
            models.Q(Name__icontains=query) | 
            models.Q(SerialNumber__icontains=query)
        )
    
    return render(request, "myapp/asset_list.html", {"assets": assets, "query": query})

def asset_form(request, pk=None):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    asset = None
    if pk:
        asset = Asset.objects.get(pk=pk)
        
    if request.method == "POST":
        tag = request.POST.get("asset_tag")
        name = request.POST.get("name")
        a_type = request.POST.get("asset_type")
        sn = request.POST.get("serial_number")
        model = request.POST.get("model")
        p_date = request.POST.get("purchase_date") or None
        w_expiry = request.POST.get("warranty_expiry") or None
        
        if asset:
            asset.AssetTag = tag
            asset.Name = name
            asset.AssetType = a_type
            asset.SerialNumber = sn
            asset.Model = model
            asset.PurchaseDate = p_date
            asset.WarrantyExpiry = w_expiry
            asset.save()
        else:
            Asset.objects.create(
                AssetTag=tag, Name=name, AssetType=a_type,
                SerialNumber=sn, Model=model, PurchaseDate=p_date,
                WarrantyExpiry=w_expiry
            )
        return redirect("asset_list")
        
    return render(request, "myapp/asset_form.html", {"asset": asset})

def asset_allocate(request, asset_id):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    asset = Asset.objects.get(id=asset_id)
    employees = Employees.objects.all().order_by('EmployeeName')
    
    if request.method == "POST":
        emp_code = request.POST.get("employee")
        date = request.POST.get("allocated_date")
        condition = request.POST.get("condition")
        
        employee = Employees.objects.get(EmployeeCode=emp_code)
        
        # Create allocation
        AssetAllocation.objects.create(
            Asset=asset, Employee=employee,
            AllocatedDate=date, ConditionOnAllocation=condition
        )
        
        # Update asset status
        asset.Status = 'ALLOCATED'
        asset.save()
        
        # Notify Employee
        send_notification(
            sender_code=request.session["emp_code"],
            receiver_code=employee.EmployeeCode,
            title="Asset Allocated",
            message=f"New asset {asset.AssetTag} ({asset.Name}) has been allocated to you.",
            n_type='ASSET',
            related_id=asset.id
        )
        
        messages.success(request, f"Asset {asset.AssetTag} allocated to {employee.EmployeeName}")
        return redirect("asset_list")
        
    return render(request, "myapp/asset_allocate.html", {"asset": asset, "employees": employees})

def asset_return(request, allocation_id):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    allocation = AssetAllocation.objects.get(id=allocation_id)
    if request.method == "POST":
        date = request.POST.get("returned_date")
        condition = request.POST.get("condition")
        
        allocation.ReturnedDate = date
        allocation.ConditionOnReturn = condition
        allocation.save()
        
        # Update asset status
        asset = allocation.Asset
        asset.Status = 'AVAILABLE'
        asset.save()
        
        # Notify Employee
        send_notification(
            sender_code=request.session["emp_code"],
            receiver_code=allocation.Employee.EmployeeCode,
            title="Asset Returned",
            message=f"Your returned asset {asset.AssetTag} ({asset.Name}) has been processed.",
            n_type='ASSET',
            related_id=asset.id
        )
        
        messages.success(request, f"Asset {asset.AssetTag} returned successfully.")
        return redirect("asset_list")
        
    return render(request, "myapp/asset_return.html", {"allocation": allocation})

def asset_history(request, asset_id):
    asset = Asset.objects.get(id=asset_id)
    history = AssetAllocation.objects.filter(Asset=asset).order_by('-AllocatedDate')
    return render(request, "myapp/asset_history.html", {"asset": asset, "history": history})

# ==========================================
# Employee Engagement & Well-being
# ==========================================

# --- Helpdesk Ticketing ---
def helpdesk_ticket_list(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    emp_code = request.session["emp_code"]
    role = request.session.get("allocate_position")
    
    # Check if user is a resolver (HR or IT)
    is_resolver = role in ["HR", "IT"]
    
    if is_resolver:
        # Show all open tickets FOR THEIR SPECIFIC CATEGORY
        # We use icontains or iexact depending on how strict we want to be
        tickets = HelpdeskTicket.objects.filter(Category__iexact=role).order_by('-CreatedAt')
    else:
        # Regular employees see only their tickets
        tickets = HelpdeskTicket.objects.filter(Employee__EmployeeCode=emp_code).order_by('-CreatedAt')

    # SLA Refresh: Check for overdue tickets
    now = timezone.now()
    overdue_tickets_qs = tickets.filter(Status__in=['OPEN', 'IN_PROGRESS'], DueDate__lt=now, IsSLAExceeded=False)
    if overdue_tickets_qs.exists():
        overdue_tickets_qs.update(IsSLAExceeded=True)
    
    # Dashboard Stats
    stats = {
        'total': tickets.count(),
        'open': tickets.filter(Status='OPEN').count(),
        'progress': tickets.filter(Status='IN_PROGRESS').count(),
        'resolved': tickets.filter(Status__in=['RESOLVED', 'CLOSED']).count(),
        'overdue': tickets.filter(IsSLAExceeded=True).count()
    }
        
    return render(request, "myapp/helpdesk_ticket_list.html", {
        "tickets": tickets,
        "is_resolver": is_resolver,
        "stats": stats
    })

def helpdesk_ticket_create(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    if request.method == "POST":
        category = request.POST.get("category", "").strip()
        subject = request.POST.get("subject")
        description = request.POST.get("description")
        priority = request.POST.get("priority")
        
        emp_code = request.session["emp_code"]
        employee = Employees.objects.get(EmployeeCode=emp_code)
        
        # Calculate DueDate based on Priority
        now = timezone.now()
        durations = {
            'Urgent': 4,
            'High': 24,
            'Medium': 48,
            'Low': 72
        }
        
        # Optionally get from AppConfiguration
        for p, d in durations.items():
            config_key = f'SLA_{p}'
            config = AppConfiguration.objects.filter(ConfigKey=config_key).first()
            if config:
                try:
                    durations[p] = int(config.ConfigValue)
                except ValueError:
                    pass
            else:
                # Create default config if it doesn't exist
                AppConfiguration.objects.get_or_create(
                    ConfigKey=config_key,
                    defaults={'ConfigValue': str(d), 'Description': f'SLA duration in hours for {p} priority tickets'}
                )

        duration_hours = durations.get(priority, 48)
        due_date = now + timedelta(hours=duration_hours)

        ticket = HelpdeskTicket.objects.create(
            Employee=employee,
            Category=category,
            Subject=subject,
            Description=description,
            Priority=priority,
            DueDate=due_date
        )
        
        # Notify Resolvers (matching the category/position exactly)
        resolvers = Employees.objects.filter(AllocatePosition__iexact=category)
        for res in resolvers:
            send_notification(
                sender_code=emp_code,
                receiver_code=res.EmployeeCode,
                title="New Support Ticket",
                message=f"New {category} ticket: {subject}",
                n_type='HELPDESK',
                related_id=ticket.id
            )
        
        messages.success(request, "Support ticket raised successfully!")
        return redirect("helpdesk_ticket_list")
        
    dynamic_categories = list(Employees.objects.values_list('AllocatePosition', flat=True).distinct())
    # Remove None, empty strings, and excluded positions
    skip_positions = ["CODER", "MANAGER", "TL", "VP"]
    dynamic_categories = [c for c in dynamic_categories if c and c.strip() and c.upper() not in skip_positions]
    
    if not dynamic_categories:
        dynamic_categories = ['General']

    return render(request, "myapp/helpdesk_ticket_form.html", {
        "categories": dynamic_categories,
        "priorities": ['Low', 'Medium', 'High', 'Urgent']
    })

def helpdesk_ticket_update(request, ticket_id):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    ticket = HelpdeskTicket.objects.get(id=ticket_id)
    if request.method == "POST":
        status = request.POST.get("status")
        resolution = request.POST.get("resolution")
        assign_to = request.POST.get("assign_to")
        
        ticket.Status = status
        ticket.Resolution = resolution
        if assign_to:
            ticket.AssignedTo = Employees.objects.get(EmployeeCode=assign_to)
        
        # SLA Handling on Resolution
        if status in ['RESOLVED', 'CLOSED'] and not ticket.ResolvedAt:
            ticket.ResolvedAt = timezone.now()
            if ticket.DueDate and ticket.ResolvedAt > ticket.DueDate:
                ticket.IsSLAExceeded = True
        elif status not in ['RESOLVED', 'CLOSED']:
            ticket.ResolvedAt = None # Reset if reopened
            # IsSLAExceeded is not reset because once breached, it stays breached usually, 
            # but for this logic we'll let helpdesk_ticket_list handle the live check.
            
        ticket.save()
        
        # Notify Ticket Owner
        send_notification(
            sender_code=request.session["emp_code"],
            receiver_code=ticket.Employee.EmployeeCode,
            title="Ticket Update",
            message=f"Your ticket #{ticket.id} has been updated to {status}.",
            n_type='HELPDESK',
            related_id=ticket.id
        )
        
        messages.success(request, f"Ticket #{ticket.id} updated.")
        return redirect("helpdesk_ticket_list")
        
    resolvers = Employees.objects.all() # Or filter by HR/IT if dept info is reliable
    return render(request, "myapp/helpdesk_ticket_update.html", {"ticket": ticket, "resolvers": resolvers})

def helpdesk_ticket_detail(request, ticket_id):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    ticket = HelpdeskTicket.objects.get(id=ticket_id)
    emp_code = request.session["emp_code"]
    role = request.session.get("allocate_position")
    
    # Permission check: Owner or Resolver
    is_resolver = role in ["HR", "IT"]
    if not is_resolver and ticket.Employee.EmployeeCode != emp_code:
        return render(request, "myapp/unauthorized.html")
        
    return render(request, "myapp/helpdesk_ticket_detail.html", {"ticket": ticket})

# --- Kudos & Recognition ---
def kudos_wall(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    if request.method == "POST":
        to_emp_code = request.POST.get("to_employee")
        message = request.POST.get("message")
        category = request.POST.get("category", "Great Work")
        
        from_emp = Employees.objects.get(EmployeeCode=request.session["emp_code"])
        to_emp = Employees.objects.get(EmployeeCode=to_emp_code)
        
        Kudos.objects.create(
            FromEmployee=from_emp, 
            ToEmployee=to_emp, 
            Message=message,
            Category=category
        )
        messages.success(request, "Kudos sent successfully!")
        return redirect("kudos_wall")
        
    kudos_list = Kudos.objects.all().order_by('-CreatedAt')
    
    # Enrich kudos with like info
    current_emp_code = request.session["emp_code"]
    for kudos in kudos_list:
        kudos.like_count = kudos.likes.count()
        kudos.is_liked = kudos.likes.filter(Employee_id=current_emp_code).exists()
        
    employees = Employees.objects.exclude(EmployeeCode=current_emp_code).order_by('EmployeeName')
    
    categories = [c[0] for c in Kudos.CATEGORY_CHOICES]
    
    return render(request, "myapp/kudos_wall.html", {
        "kudos_list": kudos_list, 
        "employees": employees,
        "categories": categories
    })

@csrf_exempt
def toggle_kudos_like(request, kudos_id):
    if not request.session.get("emp_code"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    if request.method == "POST":
        emp_code = request.session["emp_code"]
        kudos = Kudos.objects.get(id=kudos_id)
        
        like, created = KudosLike.objects.get_or_create(Kudos=kudos, Employee_id=emp_code)
        
        if not created:
            like.delete()
            liked = False
        else:
            liked = True
            
        return JsonResponse({
            "status": "success",
            "liked": liked,
            "like_count": kudos.likes.count()
        })
    
    return JsonResponse({"status": "error", "message": "Invalid method"}, status=405)

# --- Pulse Surveys ---
def pulse_survey_list(request):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    if request.session.get("allocate_position") in ["HR", "IT"]:
        surveys = PulseSurvey.objects.all().order_by('-CreatedAt')
    else:
        surveys = PulseSurvey.objects.filter(IsActive=True).order_by('-CreatedAt')
        
    responded_ids = SurveyResponse.objects.filter(Employee__EmployeeCode=request.session["emp_code"]).values_list('Survey_id', flat=True)
    
    return render(request, "myapp/pulse_survey_list.html", {
        "surveys": surveys,
        "responded_ids": responded_ids
    })

def pulse_survey_submit(request, survey_id):
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    survey = PulseSurvey.objects.get(id=survey_id)
    if request.method == "POST":
        emp_code = request.session["emp_code"]
        employee = Employees.objects.get(EmployeeCode=emp_code)
        
        # Create a single response record for the employee/survey
        response, created = SurveyResponse.objects.get_or_create(
            Survey=survey,
            Employee=employee
        )
        
        # Save answers for each question
        for question in survey.questions.all():
            rating = request.POST.get(f"rating_{question.id}")
            comment = request.POST.get(f"comment_{question.id}")
            if rating:
                SurveyAnswer.objects.update_or_create(
                    Response=response,
                    Question=question,
                    defaults={"Rating": rating, "Comment": comment}
                )
        
        messages.success(request, "Thank you for your feedback!")
        return redirect("pulse_survey_list")
        
    return render(request, "myapp/pulse_survey_form.html", {"survey": survey})

def pulse_survey_create(request):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
        
    if request.method == "POST":
        title = request.POST.get("title")
        description = request.POST.get("description") or title
        questions = request.POST.getlist("questions[]")
        
        survey = PulseSurvey.objects.create(Title=title, Description=description)
        
        # Create Question objects
        for q_text in questions:
            if q_text.strip():
                SurveyQuestion.objects.create(Survey=survey, QuestionText=q_text)
        
        # Notify All Employees
        all_emps = Employees.objects.all()
        for emp in all_emps:
            send_notification(
                sender_code=request.session["emp_code"],
                receiver_code=emp.EmployeeCode,
                title="New Pulse Survey",
                message=f"A new survey '{title}' has been launched. Please provide your feedback.",
                n_type='SURVEY',
                related_id=survey.id
            )
            
        messages.success(request, "New pulse survey launched with multiple questions!")
        return redirect("pulse_survey_list")
        
    return render(request, "myapp/pulse_survey_create.html")

def pulse_survey_results(request, survey_id):
    survey = PulseSurvey.objects.get(id=survey_id)
    responses = SurveyResponse.objects.filter(Survey=survey)
    resp_count = responses.count()
    
    total_employees = Employees.objects.count()
    participation_rate = round((resp_count / total_employees * 100), 1) if total_employees > 0 else 0
    
    from django.db.models import Count, Avg
    
    question_data = []
    for q in survey.questions.all():
        # Rating Distribution for this question
        answers = SurveyAnswer.objects.filter(Question=q)
        dist_map = {r['Rating']: r['count'] for r in answers.values('Rating').annotate(count=Count('Rating'))}
        
        distribution = []
        for i in range(1, 6):
            count = dist_map.get(i, 0)
            percent = round((count / resp_count * 100), 1) if resp_count > 0 else 0
            distribution.append({'rate': i, 'count': count, 'percent': percent})
            
        avg_rating = answers.aggregate(Avg('Rating'))['Rating__avg'] or 0
        
        question_data.append({
            'question': q,
            'avg_rating': round(avg_rating, 2),
            'distribution': distribution,
            'answers': answers.exclude(Comment__isnull=True).exclude(Comment='')
        })
        
    return render(request, "myapp/pulse_survey_results.html", {
        "survey": survey,
        "question_data": question_data,
        "total": resp_count,
        "participation_rate": participation_rate
    })

def pulse_survey_toggle(request, survey_id):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
    
    survey = PulseSurvey.objects.get(id=survey_id)
    survey.IsActive = not survey.IsActive
    survey.save()
    messages.success(request, f"Survey '{survey.Title}' {'activated' if survey.IsActive else 'closed'}.")
    return redirect("pulse_survey_list")

def pulse_survey_delete(request, survey_id):
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
    
    PulseSurvey.objects.filter(id=survey_id).delete()
    messages.error(request, "Survey deleted successfully.")
    return redirect("pulse_survey_list")

def helpdesk_ticket_delete(request, ticket_id):
    # Added for completeness if HR wants to cleanup
    if request.session.get("allocate_position") not in ["HR", "IT"]:
        return render(request, "myapp/unauthorized.html")
    HelpdeskTicket.objects.get(id=ticket_id).delete()
    return redirect("helpdesk_ticket_list")

def deactivate_user(request, emp_code):
    """
    Removes system credentials for a user when their access is no longer authorized.
    Restricted to HR and IT personnel.
    """
    if not request.session.get("emp_code"):
        return redirect("emp_login")
        
    role = request.session.get("allocate_position")
    if role not in ["HR", "IT"]:
        messages.error(request, "Unauthorized: Only HR or IT can deactivate users.")
        return redirect("dashboard")

    # Decode URL-encoded emp_code
    emp_code = urllib.parse.unquote(emp_code).strip()
    
    # 1. Check if EmployeePassword exists
    password_record = EmployeePassword.objects.filter(Employee_id=emp_code).first()
    
    if password_record:
        # 2. Delete credentials (Remove System Credentials)
        password_record.delete()
        
        # 3. Log the action (Optional: Add to Notifications or a separate log)
        messages.success(request, f"System credentials for {emp_code} have been removed. User is now deactivated.")
    else:
        messages.warning(request, f"No active credentials found for {emp_code}.")

    return redirect("user_profile", emp_code=urllib.parse.quote(emp_code))
