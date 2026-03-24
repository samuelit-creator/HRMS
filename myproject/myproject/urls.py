"""
URL configuration for myproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from myapp import views
urlpatterns = [
    path('admin/', admin.site.urls),
    path("logs/", views.live_punch_dashboard, name="live_punch_dashboard"),
    path("today_attendance/", views.today_attendance, name="today_attendance"),
    path("monthly_attendance/", views.monthly_attendance, name="monthly_attendance"),
    # urls.py
    path("login/", views.emp_login, name="emp_login"),
    path("logout/", views.emp_logout, name="emp_logout"),
    path("", views.dashboard, name="dashboard"),
    path("my_attendance/", views.my_attendance, name="my_attendance"),
    path("hr_attendance/", views.hr_attendance, name="hr_attendance"),
    # urls.py
    path("change-password/",views.change_password, name="change_password"),



    path('forgot_password/', views.forgot_password, name='forgot_password'),
    path('verify_code/', views.verify_code, name='verify_code'),


    
    path("my-team/", views.reporting_team_dashboard, name="reporting_team"),
    path(
        "spoc/employee/<str:target_emp_code>/",
        views.spoc_employee_dashboard,
        name="spoc_employee_dashboard"
    ),
    path("leave/request/", views.leave_request, name="leave_request"),
    path("notifications/", views.notifications, name="notifications"),
    path(
    'leave-approve-reject/<int:leave_id>/',
    views.leave_approve_reject,
    name='leave_approve_reject'
),
    path('employee-reporting/', views.employee_reporting, name='employee_reporting'),
    path('open-notification/<int:notif_id>/', views.open_notification, name='open_notification'),
    path('api/notifications/', views.notifications_api, name='notifications_api'),
    path('export-leaves-excel/', views.export_leaves_excel, name='export_leaves_excel'),
    path('notifications/mark-all-read/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('hrdashboard/', views.hrdashboard, name='hrdashboard'),
    path("manual-punch/", views.manual_punch, name="manual_punch"),
    path("team-head-approval/", views.team_head_approval, name="team_head_approval"),
    path("approve-manual-punch/<int:punch_id>/", views.approve_manual_punch, name="approve_manual_punch"),
    path("reject-manual-punch/<int:punch_id>/", views.reject_manual_punch, name="reject_manual_punch"),

    path("manual-punch/delete/<int:punch_id>/", views.delete_manual_punch, name="delete_manual_punch"),
    path("manual-punch/edit/<int:punch_id>/", views.edit_manual_punch, name="edit_manual_punch"),
    path('users/', views.user_list, name='user_list'),
    path('users/<path:emp_code>/edit/', views.edit_user_profile, name='edit_user_profile'),
    path('users/<path:emp_code>/', views.user_profile, name='user_profile'),
    path('my-profile/', views.my_profile, name='my_profile'),



    path("holidays/", views.holiday_list, name="holiday_list"),
    path("holidays/add/", views.add_holiday, name="add_holiday"),
    path('delete_employee_off/', views.delete_employee_off, name='delete_employee_off'),  # new
    path("employee-off/", views.employee_off_list, name="employee_off_list"),
    path("employee-off/add/", views.add_employee_off, name="add_employee_off"),
    path("salary-slip/", views.salary_slip, name="salary_slip"),
    path("salary-password/", views.salary_password, name="salary_password"),

    path("salary-list/", views.salary_list, name="salary_list"),
    path("salary/<path:emp_code>/edit/", views.salary_edit, name="salary_edit"),
    path("salary/<path:emp_code>/", views.salary_detail, name="salary_detail"),
    path("leaves/", views.leave_approval_list, name="leave_list"),
    path("leave/approve-reject/<int:leave_id>/", views.leave_approve_reject),
    # urls.py
    path("leave/delete/<int:leave_id>/", views.delete_leave_request, name="delete_leave"),



    
    path("shift-allocation/", views.shift_allocation, name="shift_allocation"),
    path("shift-allocation/delete/<int:id>/", views.delete_shift_allocation),
    
    path("org-play/", views.org_play, name="org_play"),
    path("mis-dashboard/", views.mis_dashboard, name="mis_dashboard"),
    path("upload-master-data/", views.upload_master_data, name="upload_master_data"),
    path("export-mis-data/", views.export_mis_data, name="export_mis_data"),
    path("download-mis-template/", views.download_mis_template, name="download_mis_template"),

    # Announcements
    path('announcements/', views.announcement_list, name='announcement_list'),
    path('announcements/add/', views.add_announcement, name='add_announcement'),
    path('announcements/<int:pk>/edit/', views.edit_announcement, name='edit_announcement'),
    path('announcements/<int:pk>/delete/', views.delete_announcement, name='delete_announcement'),

    # Onboarding
    path('register/', views.candidate_register, name='candidate_register'),
    path('onboarding/list/', views.onboarding_list, name='onboarding_list'),
    path('onboarding/<int:pk>/', views.onboarding_detail, name='onboarding_detail'),
    path('onboarding/<int:pk>/<str:action>/', views.onboarding_action, name='onboarding_action'),
    path('toggle-candidate-registration/', views.toggle_candidate_registration, name='toggle_candidate_registration'),

    # Expense & Reimbursement
    path('expense/request/', views.expense_request, name='expense_request'),
    path('expense/my-claims/', views.expense_list, name='expense_list'),
    path('expense/approvals/', views.expense_approval_list, name='expense_approval_list'),
    path('expense/approve/<int:claim_id>/<str:action>/', views.expense_approve_action, name='expense_approve'),
    path('expense/receipt/<int:claim_id>/', views.expense_receipt_view, name='expense_receipt'),

    # Asset Management
    path('assets/', views.asset_list, name='asset_list'),
    path('assets/add/', views.asset_form, name='asset_create'),
    path('assets/edit/<int:pk>/', views.asset_form, name='asset_edit'),
    path('assets/allocate/<int:asset_id>/', views.asset_allocate, name='asset_allocate'),
    path('assets/return/<int:allocation_id>/', views.asset_return, name='asset_return'),
    path('assets/history/<int:asset_id>/', views.asset_history, name='asset_history'),

    # Employee Engagement & Well-being
    path('helpdesk/', views.helpdesk_ticket_list, name='helpdesk_ticket_list'),
    path('helpdesk/create/', views.helpdesk_ticket_create, name='helpdesk_ticket_create'),
    path('helpdesk/update/<int:ticket_id>/', views.helpdesk_ticket_update, name='helpdesk_ticket_update'),
    path('helpdesk/detail/<int:ticket_id>/', views.helpdesk_ticket_detail, name='helpdesk_ticket_detail'),
    path('kudos/', views.kudos_wall, name='kudos_wall'),
    path('kudos/like/<int:kudos_id>/', views.toggle_kudos_like, name='toggle_kudos_like'),
    path('surveys/', views.pulse_survey_list, name='pulse_survey_list'),
    path('surveys/create/', views.pulse_survey_create, name='pulse_survey_create'),
    path('surveys/submit/<int:survey_id>/', views.pulse_survey_submit, name='pulse_survey_submit'),
    path('surveys/results/<int:survey_id>/', views.pulse_survey_results, name='pulse_survey_results'),
    path('surveys/toggle/<int:survey_id>/', views.pulse_survey_toggle, name='pulse_survey_toggle'),
    path('surveys/delete/<int:survey_id>/', views.pulse_survey_delete, name='pulse_survey_delete'),
    path('helpdesk/delete/<int:ticket_id>/', views.helpdesk_ticket_delete, name='helpdesk_ticket_delete'),
    path('security-showcase/', views.security_showcase, name='security_showcase'),





    





    path('users/<path:emp_code>/deactivate/', views.deactivate_user, name='deactivate_user'),
]
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
