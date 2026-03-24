import os

file_path = r'c:\Users\medle\Documents\pys\myproject\myapp\views.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. dashboard sandwich logic at ~1919
old_dashboard = """        elif current_date in holidays:
            day_info.update({
                "status": holidays[current_date],  # Holiday / WeekOff
                "break": "00:00"
            })
        elif weekday == 6:
            day_info.update({
                "status": "Weekly Off",
                "break": "00:00"
            })"""

new_dashboard = """        elif current_date in holidays or weekday == 6:
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
                })"""

# 2. hr_attendance loop fix at ~1025
old_hr = """    for emp_code, log_day, log_time in rows:
        if not log_day: continue
        log_day = log_day.date() if isinstance(log_day, datetime) else log_day
        if log_day not in employees: continue # Range check
        
        emp_info = employees[log_day][emp_code]
        if log_time:
            emp_info["logs"].append(log_time)
            if (log_day, emp_code) not in punch_map:
                punch_map[(log_day, emp_code)] = log_time"""

new_hr = """    for emp_code, log_day_v, log_time in (rows or []):
        if not log_day_v: continue
        log_day_cast: date = log_day_v.date() if isinstance(log_day_v, datetime) else log_day_v
        if log_day_cast not in employees: continue 
        
        emp_inf_ptr = employees[log_day_cast].get(emp_code)
        if emp_inf_ptr and log_time:
            cast(list, emp_inf_ptr["logs"]).append(log_time)
            if (log_day_cast, emp_code) not in punch_map:
                punch_map[(log_day_cast, emp_code)] = log_time"""

# 3. today_attendance loop fix at ~1334
old_today = """    for emp_code, log_day, log_time in rows:
        if not log_day: continue
        log_day = log_day.date() if isinstance(log_day, datetime) else log_day
        if log_day not in employees: continue # Range check
        
        emp_info = employees[log_day][emp_code]
        if log_time:
            emp_info["logs"].append(log_time)
            if (log_day, emp_code) not in punch_map:
                punch_map[(log_day, emp_code)] = log_time"""

new_today = """    for emp_code, log_day_v, log_time in (rows or []):
        if not log_day_v: continue
        log_day_cast: date = log_day_v.date() if isinstance(log_day_v, datetime) else log_day_v
        if log_day_cast not in employees: continue 
        
        emp_inf_ptr = employees[log_day_cast].get(emp_code)
        if emp_inf_ptr and log_time:
            cast(list, emp_inf_ptr["logs"]).append(log_time)
            if (log_day_cast, emp_code) not in punch_map:
                punch_map[(log_day_cast, emp_code)] = log_time"""

# Perform replacements
content = content.replace(old_dashboard, new_dashboard)
content = content.replace(old_hr, new_hr)
content = content.replace(old_today, new_today)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
