# myapp/models.py
from django.db import models

class Departments(models.Model):
    DepartmentId = models.AutoField(primary_key=True)
    DepartmentFName = models.CharField(max_length=200)

    class Meta:
        managed = False   # existing table
        db_table = 'Departments'

class Employees(models.Model):
    EmployeeId = models.IntegerField()
    EmployeeCode = models.CharField(
        max_length=50,
        primary_key=True,        # ✅ REAL PK
        db_column="EmployeeCode"
    )
    EmployeeName = models.CharField(max_length=200)
    Department = models.ForeignKey(
        Departments,
        db_column='DepartmentId',
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True
    )
    # Extra fields used in raw SQL / templates
    Designation = models.CharField(max_length=200, null=True, blank=True)
    Qualification = models.CharField(max_length=200, null=True, blank=True)
    MaritalStatus = models.CharField(max_length=50, null=True, blank=True)
    TotalExperience = models.CharField(max_length=100, null=True, blank=True)
    PreviousOrganization = models.CharField(max_length=200, null=True, blank=True)
    BGVDetails = models.TextField(null=True, blank=True)
    AAPCCertification = models.CharField(max_length=10, null=True, blank=True)
    CredentialNumber = models.CharField(max_length=100, null=True, blank=True)
    EmergencyContactNumber = models.CharField(max_length=20, null=True, blank=True)
    PANNumber = models.CharField(max_length=20, null=True, blank=True)
    UANNumber = models.CharField(max_length=20, null=True, blank=True)
    AllocatePosition = models.CharField(max_length=100, null=True, blank=True)
    EmployeePhoto = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Employees'

class EmployeeReporting(models.Model):
    EmployeeCode = models.CharField(max_length=20)
    ReportsToEmpCode = models.CharField(max_length=20, null=True, blank=True)
    Role = models.CharField(max_length=50, null=True, blank=True)
    CreatedAt = models.DateTimeField(null=True, blank=True)
    UpdatedAt = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.EmployeeCode

class AttendanceLogs(models.Model):
    AttendanceLogId = models.AutoField(primary_key=True)
    AttendanceDate = models.DateTimeField()

    Employee = models.ForeignKey(
        Employees,
        db_column='EmployeeCode',
        on_delete=models.DO_NOTHING,
        related_name='attendance_logs'
    )

    InTime = models.CharField(max_length=510, null=True, blank=True)
    OutTime = models.CharField(max_length=510, null=True, blank=True)
    Status = models.CharField(max_length=510)

    class Meta:
        managed = False
        db_table = 'AttendanceLogs'

class DeviceLogs122025(models.Model):
    DeviceLogId = models.AutoField(
        primary_key=True,
        db_column='DeviceLogId'
    )
    DownloadDate = models.DateTimeField(
        db_column='DownloadDate',
        null=True
    )
    DeviceId = models.IntegerField(
        db_column='DeviceId',
        null=True
    )
    UserId = models.CharField(
        max_length=100,
        db_column='UserId'
    )
    LogDate = models.DateTimeField(
        db_column='LogDate'
    )
    Direction = models.CharField(
        max_length=200,
        db_column='Direction',
        null=True
    )
    AttDirection = models.CharField(
        max_length=510,
        db_column='AttDirection',
        null=True
    )

    class Meta:
        managed = False
        db_table = 'DeviceLogs_12_2025'

class EmployeeAttendance(models.Model):
    AttendanceId = models.AutoField(primary_key=True)

    Employee = models.ForeignKey(
        Employees,
        db_column='EmployeeCode',
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name='attendances'
    )
    
    AttendanceDate = models.DateField()

    PunchIn = models.DateTimeField(null=True, blank=True)
    PunchOut = models.DateTimeField(null=True, blank=True)
    
    WorkingSeconds = models.IntegerField(default=0)
    BreakSeconds = models.IntegerField(default=0)

    Status = models.CharField(
        max_length=50,
        choices=[
            ('PRESENT', 'Present'),
            ('ABSENT', 'Absent'),
            ('WEEKLY_OFF', 'Weekly Off'),
            ('HALF_DAY', 'Half Day'),
        ],
        default='ABSENT'
    )

    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True   # ✅ Django will create this table
        db_table = 'EmployeeAttendance'
        unique_together = ('Employee', 'AttendanceDate')

class EmployeePassword(models.Model):
    Employee = models.OneToOneField(
        Employees,
        to_field="EmployeeCode",         # ✅ FK → PK
        db_column="EmployeeCode",
        on_delete=models.DO_NOTHING,
        db_constraint=True
    )

    PasswordHash = models.CharField(max_length=255)

    class Meta:
        db_table = "EmployeePasswords"

class MasterData(models.Model):
    SNo = models.IntegerField(null=True, blank=True)
    Date = models.DateField()
    ProjectName = models.CharField(max_length=200)
    MLV_ID = models.CharField(max_length=50) # EmployeeCode
    MLV_Name = models.CharField(max_length=200)
    Team = models.CharField(max_length=100)
    
    # FPC Production
    FPC_TotalCount = models.IntegerField(default=0)
    FPC_TotalPage = models.IntegerField(default=0)
    FPC_TotalIcd = models.IntegerField(default=0)
    FPC_Target = models.IntegerField(default=0)
    FPC_AchievedPercent = models.FloatField(default=0.0)
    
    # Audit Production
    Audit_TotalCount = models.IntegerField(default=0)
    Audit_TotalPage = models.IntegerField(default=0)
    Audit_TotalIcd = models.IntegerField(default=0)
    Audit_ErrorCount = models.IntegerField(default=0)
    Audit_Target = models.IntegerField(default=0)
    Audit_U1Percent = models.FloatField(default=0.0)
    Audit_AchievedPercent = models.FloatField(default=0.0)
    
    # Overall Quality
    Quality_TotalChart = models.IntegerField(default=0)
    Quality_TotalIcd = models.IntegerField(default=0)
    Quality_TotalErrors = models.IntegerField(default=0)
    Quality_QAPercent = models.FloatField(default=0.0)
    
    # Assessment Scores
    Assessment_TotalQues = models.IntegerField(default=0)
    Assessment_TotalCorrect = models.IntegerField(default=0)
    Assessment_Percent = models.FloatField(default=0.0)

    class Meta:
        managed = True
        db_table = 'MasterData'
        unique_together = ('MLV_ID', 'Date', 'ProjectName')

class CompanyAnnouncement(models.Model):
    Title = models.CharField(max_length=255)
    Content = models.TextField()
    CreatedAt = models.DateTimeField(auto_now_add=True)
    IsActive = models.BooleanField(default=True)

    class Meta:
        db_table = 'CompanyAnnouncements'
        managed = True

    def __str__(self):
        return self.Title

class OnboardingRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
    ]

    FullName = models.CharField(max_length=200)
    EmployeeCode = models.CharField(max_length=50, null=True, blank=True)
    DateOfJoining = models.DateField(null=True, blank=True)
    Email = models.EmailField()
    ContactNo = models.CharField(max_length=20)
    FatherName = models.CharField(max_length=200)
    MotherName = models.CharField(max_length=200)
    DOB = models.DateField()
    Gender = models.CharField(max_length=20, choices=[('Male', 'Male'), ('Female', 'Female'), ('Other', 'Other')])
    PlaceOfBirth = models.CharField(max_length=200)
    BloodGroup = models.CharField(max_length=10)
    AadhaarNumber = models.CharField(max_length=20)
    ResidentialAddress = models.TextField()
    PermanentAddress = models.TextField()
    
    # Bank Details
    BankName = models.CharField(max_length=200)
    AccountNumber = models.CharField(max_length=50)
    IFSCCode = models.CharField(max_length=20)
    
    # Media
    Photo = models.BinaryField(null=True, blank=True)
    
    # New Fields
    Qualification = models.CharField(max_length=200, null=True, blank=True)
    MaritalStatus = models.CharField(max_length=50, null=True, blank=True)
    Designation = models.CharField(max_length=100, null=True, blank=True)
    TotalExperience = models.CharField(max_length=100, null=True, blank=True)
    PreviousOrganization = models.CharField(max_length=200, null=True, blank=True)
    BGVDetails = models.TextField(null=True, blank=True)
    AAPCCertification = models.CharField(max_length=10, null=True, blank=True)
    CredentialNumber = models.CharField(max_length=100, null=True, blank=True)
    EmergencyContactNumber = models.CharField(max_length=20, null=True, blank=True)
    PANNumber = models.CharField(max_length=20, null=True, blank=True)
    UANNumber = models.CharField(max_length=20, null=True, blank=True)
    
    # Status
    Status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'OnboardingRequests'
        managed = True

    def __str__(self):
        return self.FullName

class AppConfiguration(models.Model):
    ConfigKey = models.CharField(max_length=100, primary_key=True)
    ConfigValue = models.CharField(max_length=255)
    Description = models.TextField(null=True, blank=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'AppConfigurations'
        managed = True

    def __str__(self):
        return f"{self.ConfigKey}: {self.ConfigValue}"

class ExpenseClaim(models.Model):
    STATUS_CHOICES = [
        ('PENDING_MANAGER', 'Pending Manager'),
        ('PENDING_FINANCE', 'Pending Finance'),
        ('PENDING_ADMIN', 'Pending Admin'),
        ('APPROVED_FOR_PAYMENT', 'Approved for Payment'),
        ('REJECTED', 'Rejected'),
        ('PAID', 'Paid'),
    ]

    EXPENSE_TYPES = [
        ('Travel', 'Travel'),
        ('Food', 'Food'),
        ('Internet', 'Internet'),
        ('Other', 'Other'),
    ]

    Employee = models.ForeignKey(
        Employees,
        to_field="EmployeeCode",
        db_column="EmployeeCode",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name='expense_claims'
    )
    ExpenseType = models.CharField(max_length=50, choices=EXPENSE_TYPES)
    Amount = models.DecimalField(max_digits=10, decimal_places=2)
    Description = models.TextField()
    ExpenseDate = models.DateField()
    Receipt = models.BinaryField(null=True, blank=True)
    Status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING_MANAGER')
    
    ManagerRemarks = models.TextField(null=True, blank=True)
    FinanceRemarks = models.TextField(null=True, blank=True)
    AdminRemarks = models.TextField(null=True, blank=True)
    
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ExpenseClaims'
        managed = True

    def __str__(self):
        return f"{self.Employee.EmployeeCode} - {self.ExpenseType} - {self.Amount}"

class Asset(models.Model):
    STATUS_CHOICES = [
        ('AVAILABLE', 'Available'),
        ('ALLOCATED', 'Allocated'),
        ('RETIRED', 'Retired'),
        ('UNDER_REPAIR', 'Under Repair'),
    ]

    ASSET_TYPES = [
        ('Laptop', 'Laptop'),
        ('Mouse', 'Mouse'),
        ('Keyboard', 'Keyboard'),
        ('Phone', 'Phone'),
        ('Other', 'Other'),
    ]

    AssetTag = models.CharField(max_length=50, unique=True)
    Name = models.CharField(max_length=200)
    AssetType = models.CharField(max_length=50, choices=ASSET_TYPES)
    SerialNumber = models.CharField(max_length=100, null=True, blank=True)
    Model = models.CharField(max_length=100, null=True, blank=True)
    Status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='AVAILABLE')
    PurchaseDate = models.DateField(null=True, blank=True)
    WarrantyExpiry = models.DateField(null=True, blank=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'Assets'
        managed = True

    def __str__(self):
        return f"{self.AssetTag} - {self.Name}"

class AssetAllocation(models.Model):
    Asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='allocations')
    Employee = models.ForeignKey(
        Employees,
        to_field="EmployeeCode",
        db_column="EmployeeCode",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name='asset_allocations'
    )
    AllocatedDate = models.DateField()
    ReturnedDate = models.DateField(null=True, blank=True)
    ConditionOnAllocation = models.CharField(max_length=100, null=True, blank=True)
    ConditionOnReturn = models.CharField(max_length=100, null=True, blank=True)
    Remarks = models.TextField(null=True, blank=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'AssetAllocations'
        managed = True

class HelpdeskTicket(models.Model):
    CATEGORIES = [('HR', 'HR Support'), ('IT', 'IT Support'), ('Admin', 'Admin Support'), ('Payroll', 'Payroll Support')]
    PRIORITIES = [('Low', 'Low'), ('Medium', 'Medium'), ('High', 'High'), ('Urgent', 'Urgent')]
    STATUSES = [('OPEN', 'Open'), ('IN_PROGRESS', 'In Progress'), ('RESOLVED', 'Resolved'), ('CLOSED', 'Closed')]

    Employee = models.ForeignKey(Employees, to_field="EmployeeCode", db_column="EmployeeCode", on_delete=models.DO_NOTHING, db_constraint=False, related_name='helpdesk_tickets')
    Category = models.CharField(max_length=50, choices=CATEGORIES)
    Subject = models.CharField(max_length=200)
    Description = models.TextField()
    Priority = models.CharField(max_length=20, choices=PRIORITIES, default='Medium')
    Status = models.CharField(max_length=20, choices=STATUSES, default='OPEN')
    AssignedTo = models.ForeignKey(Employees, to_field="EmployeeCode", db_column="AssignedTo", on_delete=models.DO_NOTHING, db_constraint=False, null=True, blank=True, related_name='assigned_tickets')
    Resolution = models.TextField(null=True, blank=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)
    
    # SLA Fields
    DueDate = models.DateTimeField(null=True, blank=True)
    ResolvedAt = models.DateTimeField(null=True, blank=True)
    IsSLAExceeded = models.BooleanField(default=False)

    class Meta:
        db_table = 'HelpdeskTickets'
        managed = True

class Kudos(models.Model):
    CATEGORY_CHOICES = [
        ('Teamwork', 'Teamwork'),
        ('Innovation', 'Innovation'),
        ('Support', 'Support'),
        ('Leadership', 'Leadership'),
        ('Great Work', 'Great Work'),
        ('Other', 'Other'),
    ]
    FromEmployee = models.ForeignKey(Employees, to_field="EmployeeCode", db_column="FromEmployeeCode", on_delete=models.DO_NOTHING, db_constraint=False, related_name='kudos_sent')
    ToEmployee = models.ForeignKey(Employees, to_field="EmployeeCode", db_column="ToEmployeeCode", on_delete=models.DO_NOTHING, db_constraint=False, related_name='kudos_received')
    Category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Great Work')
    Message = models.TextField()
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'KudosWall'
        managed = True

class KudosLike(models.Model):
    Kudos = models.ForeignKey(Kudos, on_delete=models.CASCADE, related_name='likes')
    Employee = models.ForeignKey(Employees, to_field="EmployeeCode", db_column="EmployeeCode", on_delete=models.DO_NOTHING, db_constraint=False)
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'KudosLikes'
        managed = True
        unique_together = ('Kudos', 'Employee')

class PulseSurvey(models.Model):
    Title = models.CharField(max_length=200)
    Description = models.TextField()
    IsActive = models.BooleanField(default=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'PulseSurveys'
        managed = True

class SurveyQuestion(models.Model):
    Survey = models.ForeignKey(PulseSurvey, on_delete=models.CASCADE, related_name='questions')
    QuestionText = models.CharField(max_length=500)
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'SurveyQuestions'
        managed = True

class SurveyResponse(models.Model):
    Survey = models.ForeignKey(PulseSurvey, on_delete=models.CASCADE, related_name='responses')
    Employee = models.ForeignKey(Employees, to_field="EmployeeCode", db_column="EmployeeCode", on_delete=models.DO_NOTHING, db_constraint=False)
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'SurveyResponses'
        managed = True
        unique_together = ('Survey', 'Employee')

class SurveyAnswer(models.Model):
    Response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name='answers')
    Question = models.ForeignKey(SurveyQuestion, on_delete=models.CASCADE, related_name='answers')
    Rating = models.IntegerField() # 1-5
    Comment = models.TextField(null=True, blank=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'SurveyAnswers'
        managed = True
