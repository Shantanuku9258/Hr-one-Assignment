# Engineering Decisions

### 1. Indexes

A unique index on `employees.emp_code` prevents duplicate employee codes. The compound index on `employees.department` and `employees.joined_on` supports department and joining-date queries. The separate `joined_on` index supports queries that filter only by joining date. A unique compound index on `attendance_logs.emp_code` and `attendance_logs.date` prevents duplicate daily attendance records. The index on `attendance_logs.date` and `attendance_logs.emp_code` supports date-based attendance listing. I would avoid adding indexes without checking query plans because every index increases storage and write costs.

### 2. Punch-in race

Two simultaneous requests can both pass the initial existence check. The unique compound index allows only one insert to succeed. The other insert raises `DuplicateKeyError`, which the API converts to HTTP 409.

### 3. Ties

The leaderboard uses ranking based on total late minutes. Employees tied at the cutoff can share the same rank, so the response may contain more than the requested limit. The employee code provides deterministic ordering among tied results.

### 4. Headcount

The department summary starts from employee records and joins attendance data. Therefore, employees without attendance logs can still contribute to department headcount.

### 5. Scaling

For substantially more data, I would inspect execution plans and query latency, optimize indexes for measured query patterns, and add automated performance tests before making larger architectural changes.