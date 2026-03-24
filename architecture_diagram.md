# Architecture Diagram: User Lifecycle Management

This diagram illustrates the user registration, authorization, and credential removal lifecycle within the HRMS.

## 1. User Lifecycle Flow

```mermaid
sequenceDiagram
    participant U as Candidate/User
    participant A as Admin (HR/IT)
    participant S as System (Django Views)
    participant DB as Database (MSSQL)

    Note over U, DB: Phase 1: Registration
    U->>S: Submit Registration (candidate_register)
    S->>DB: Save OnboardingRequest (PENDING)
    
    Note over A, DB: Phase 2: Authorization & Issuance
    A->>S: Review & Select "Accept" (onboarding_action)
    S->>DB: Create/Update Employee Record
    S->>DB: Insert EmployeePassword (Welcome@123)
    S->>DB: Update OnboardingRequest (ACCEPTED)
    
    Note over U, DB: Phase 4: Access removal
    A->>S: Select "Deactivate" (deactivate_user)
    S->>DB: Delete EmployeePassword Record
    A-->>U: Access Removed
```

## 2. Interaction Model

```mermaid
graph TD
    subgraph "Entry Points"
        CR["Candidate Register UI"]
        DL["Dashboard / Login UI"]
        PL["User Profile UI"]
    end

    subgraph "Core Logic (views.py)"
        V_CR["candidate_register"]
        V_OA["onboarding_action (Accept/Reject)"]
        V_DU["deactivate_user"]
        V_LG["emp_login / emp_logout"]
    end

    subgraph "Persistence (Database)"
        OR["Table: OnboardingRequests"]
        EM["Table: Employees"]
        EP["Table: EmployeePasswords"]
    end

    CR --> V_CR
    V_CR --> OR
    
    V_OA --> OR
    V_OA --> EM
    V_OA --> EP
    
    PL --> V_DU
    V_DU -- "DELETE" --> EP
    
    DL --> V_LG
    V_LG -- "CHECK" --> EP
```

## 3. Control Descriptions
- **onboarding_action**: The gateway for system access. Ensures a formal record exists BEFORE credentials are issued.
- **deactivate_user**: The kill-switch for system access. Deleting the `EmployeePassword` entry ensures the `emp_login` function will fail authentication immediately.
- **emp_login**: The enforcement point. It joins `Employees` with `EmployeePasswords` to verify both existence and credentials.
