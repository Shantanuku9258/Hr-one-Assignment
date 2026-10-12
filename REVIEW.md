# Code Review — Employee Attendance & Analytics API

## 1. Defects Found

### Defect 1: Database Indexes Created During Module Import

- **Location:** `app/main.py` — MongoDB index initialization
- **Problem:** Database indexes are created when the Python module is imported, rather than during the FastAPI application startup lifecycle.
- **How to identify:** Import the module in a test environment or start the application when MongoDB is unavailable.
- **Recommended fix:** Create indexes in a FastAPI lifespan handler and handle startup failures appropriately.

### Defect 2: Employee Creation Race Condition

- **Location:** `app/main.py` — `create_employee()`
- **Problem:** Two concurrent requests can both pass the initial employee-code existence check.
- **How to identify:** Submit simultaneous requests using the same `emp_code`.
- **Fix:** Enforce uniqueness through the MongoDB unique index and handle `DuplicateKeyError` with HTTP 409.

### Defect 3: Attendance Punch-In Race Condition

- **Location:** `app/main.py` — `punch_in()`
- **Problem:** Concurrent requests can both pass the preliminary check for an existing attendance record.
- **How to identify:** Send simultaneous punch-in requests for the same employee and attendance date.
- **Fix:** Use a unique compound index on `emp_code` and `date`, and convert duplicate-key errors into HTTP 409 responses.

### Defect 4: Concurrent Punch-Out Requests

- **Location:** `app/main.py` — `punch_out()`
- **Problem:** Concurrent requests must not overwrite an attendance record that has already been punched out.
- **How to identify:** Submit two punch-out requests for the same attendance record at nearly the same time.
- **Fix:** Use a conditional atomic update that requires `punch_out` to be `null`. Return HTTP 409 if the update does not modify the record.

## 2. Areas Investigated but Not Considered Defects

- **Employee uniqueness:** The initial existence check provides a clear response for normal duplicate requests. The unique database index provides the actual concurrency guarantee.
- **Attendance uniqueness:** The preliminary check is useful, while the unique compound index protects against simultaneous inserts.
- **Punch-out safety:** The conditional update prevents a second request from overwriting an already-recorded punch-out.
- **Pagination:** Employee and attendance listing perform filtering, sorting, and pagination in MongoDB instead of loading all matching records into application memory.

## 3. Verification Notes

These findings are based on the implementation and should be checked against the original starter code and assignment requirements before final submission. Additional defects should be documented with the affected function, triggering input, observed behavior, and corresponding fix.

Concurrency behavior and error responses should be verified with targeted tests before being reported as tested.