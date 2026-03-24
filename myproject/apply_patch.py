import sys

path = r'c:\Users\medle\Documents\pys\myproject\myapp\views.py'
with open(path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

# Update hr_attendance loop
start = 884 # line 885
end = 893 # line 894
new_loop = [
    '    for emp_code, emp_name, team_code, team_name, log_day, log_time in rows:\n',
    '        eff_day = log_day if log_day else selected_date\n',
    '        emp_info = employees[eff_day][emp_code]\n',
    '        emp_info["name"] = emp_name\n',
    '        emp_info["team"] = team_name\n',
    '        if log_time:\n',
    '            emp_info["logs"].append(log_time)\n',
    '            if (eff_day, emp_code) not in punch_map:\n',
    '                punch_map[(eff_day, emp_code)] = log_time  # first punch = IN\n',
    '        team_emp_set.add(emp_code)\n'
]
lines[start:end] = new_loop

# Update today_attendance loop (shifted by 0 as loops have same length)
# Original today_attendance was at 1081-1089
# Since we didn't add/remove lines in hr_attendance loop, it should still be around 1081
# Let's find it by searching for the "Hierarchical Team + Device Logs" comment or similar
for i, line in enumerate(lines):
    if 'rows = cursor.fetchall()' in line and i > 1000:
        search_start = i + 1
        break
else:
    search_start = 1074

start_today = search_start + 7 # usually 1081
lines[start_today:start_today+9] = [
    '    for emp_code, emp_name, team_code, team_name, log_day, log_time in rows:\n',
    '        eff_day = log_day if log_day else selected_date\n',
    '        emp_info = employees[eff_day][emp_code]\n',
    '        emp_info["name"] = emp_name\n',
    '        emp_info["team"] = team_name\n',
    '        if log_time:\n',
    '            emp_info["logs"].append(log_time)\n',
    '            if (eff_day, emp_code) not in punch_map:\n',
    '                punch_map[(eff_day, emp_code)] = log_time  # first punch = IN\n',
    '        team_emp_set.add(emp_code)\n'
]

# Update punch_status references
for i in range(len(lines)):
    if 'punch_status_map.get(emp_code)' in lines[i]:
        lines[i] = lines[i].replace('punch_status_map.get(emp_code)', 'daily_status_map.get((log_day, emp_code))')

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("Patch applied successfully")
