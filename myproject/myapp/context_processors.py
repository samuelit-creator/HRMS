from django.db import connection
import base64

def notifications_context(request):
    emp_code = request.session.get("emp_code")
    if not emp_code:
        return {}

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT TOP 5 NotificationID, Title, Message, IsRead, RelatedId, Type, CreatedAt
            FROM Notifications
            WHERE ReceiverEmpCode = %s
            ORDER BY CreatedAt DESC
        """, [emp_code])

        rows = cursor.fetchall()
        notifications = []
        for r in rows:
            notifications.append({
                'id': r[0],
                'title': r[1],
                'message': r[2],
                'is_read': r[3],
                'related_id': r[4],
                'type': r[5],
                'created_at': r[6]
            })

        cursor.execute("""
            SELECT COUNT(*)
            FROM Notifications
            WHERE ReceiverEmpCode = %s AND IsRead = 0
        """, [emp_code])
        unread_count = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM Notifications
            WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'LEAVE'
        """, [emp_code])
        unread_leave_req = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM Notifications
            WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'LEAVE_RESPONSE'
        """, [emp_code])
        unread_leave_resp = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM Notifications
            WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'MANUAL_PUNCH'
        """, [emp_code])
        unread_manual_req = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM Notifications
            WHERE ReceiverEmpCode = %s AND IsRead = 0 AND Type = 'MANUAL_PUNCH_RESPONSE'
        """, [emp_code])
        unread_manual_resp = cursor.fetchone()[0]

    return {
        "notifications_list": notifications,
        "notification_count": unread_count,
        "unread_leave_req_count": unread_leave_req,
        "unread_leave_resp_count": unread_leave_resp,
        "unread_manual_req_count": unread_manual_req,
        "unread_manual_resp_count": unread_manual_resp
    }
def emp_info(request):
    emp_code = request.session.get("emp_code")
    emp_photo = request.session.get("emp_photo")

    # Fallback for active sessions that haven't re-logged since the update
    if emp_code and not emp_photo:
        with connection.cursor() as cursor:
            cursor.execute("SELECT EmployeePhoto FROM Employees WHERE EmployeeCode = %s", [emp_code])
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    photo_bytes = row[0]
                    if isinstance(photo_bytes, memoryview):
                        photo_bytes = photo_bytes.tobytes()
                    emp_photo = base64.b64encode(photo_bytes).decode('utf-8')
                    request.session["emp_photo"] = emp_photo # Cache it in session
                except Exception:
                    pass

    return {
        "emp_id": request.session.get("emp_id"),
        "emp_code": emp_code,
        "emp_name": request.session.get("emp_name"),
        "emp_photo": emp_photo,
    }


def allocate_position_context(request):
    from .models import AppConfiguration
    try:
        config = AppConfiguration.objects.get(ConfigKey='show_candidate_register')
        show_reg = config.ConfigValue == 'True'
    except:
        show_reg = True
        
    return {
        "user_allocate_position": request.session.get("allocate_position"),
        "show_candidate_register": show_reg
    }