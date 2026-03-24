import pyodbc
from datetime import datetime, date, timedelta
from collections import defaultdict

# DB configuration based on settings.py
conn_str = 'Driver={ODBC Driver 17 for SQL Server};Server=DESKTOP-8IL9EIE\\SQLEXPRESS;Database=etimetracklite1;Trusted_Connection=yes;'

def get_leave_data():
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    # Query approved Casual and Sick leaves
    # Using raw SQL for LeaveRequests as it's not a Django model
    query = """
    SELECT EmployeeCode, FromDate, ToDate, LeaveType
    FROM LeaveRequests
    WHERE Status = 'APPROVED'
      AND LeaveType IN ('Casual Leave', 'Sick Leave')
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    
    # Also fetch employee names for better reporting
    cursor.execute("SELECT EmployeeCode, EmployeeName FROM Employees")
    emp_names = {row[0]: row[1] for row in cursor.fetchall()}
    
    conn.close()
    return rows, emp_names

def process_leaves(rows):
    # Aggregated days: (EmployeeCode, Year, Month) -> total_days
    aggregates = defaultdict(float)
    
    for emp_code, from_date, to_date, leave_type in rows:
        if not from_date or not to_date:
            continue
            
        # Standardize dates
        if isinstance(from_date, datetime):
            curr = from_date.date()
        else:
            curr = from_date
            
        if isinstance(to_date, datetime):
            last = to_date.date()
        else:
            last = to_date
        
        while curr <= last:
            # Add 1 day to the specific month
            key = (emp_code, curr.year, curr.month)
            aggregates[key] += 1
            curr += timedelta(days=1)
            
    return aggregates

def main():
    print("Fetching leave data from MSSQL (etimetracklite1)...")
    try:
        rows, emp_names = get_leave_data()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return

    print(f"Found {len(rows)} approved leave records.")
    
    print("Processing leave days by month...")
    aggregates = process_leaves(rows)
    
    print("\nEmployees with more than 2 days of combined Casual and Sick leave in one month:")
    print("-" * 85)
    print(f"{'Employee Code':<15} {'Employee Name':<30} {'Year-Month':<12} {'Total Days'}")
    print("-" * 85)
    
    found = False
    # Sort by Year, Month descending, then by EmployeeCode
    sorted_keys = sorted(aggregates.keys(), key=lambda x: (x[1], x[2], x[0]), reverse=True)
    
    # Filter and display
    for key in sorted_keys:
        total_days = aggregates[key]
        if total_days > 2:
            emp_code, year, month = key
            name = emp_names.get(emp_code, "Unknown")
            print(f"{emp_code:<15} {name:<30} {year}-{month:02}      {total_days}")
            found = True
            
    if not found:
        print("No employees found exceeding 2 days of combined Casual and Sick leave in a month.")

if __name__ == "__main__":
    main()
