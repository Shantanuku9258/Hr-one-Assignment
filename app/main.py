"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
import re
from datetime import datetime,date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, StrictInt,field_validator,model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError 

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]


db.employees.create_index(
    [("emp_code", 1)],
    unique=True,
)


db.employees.create_index(
    [("department", 1), ("joined_on", 1)],
    name="department_joined_on_idx",
)

# Supports headcount queries that filter only by joined_on.
db.employees.create_index(
    [("joined_on", 1)],
    name="joined_on_idx",
)

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True,
)

db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)],
    name="attendance_date_emp_code_idx",
)

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    date_str: str,
) -> int:
    shift_date = date.fromisoformat(date_str)
    start_hour, start_minute = map(int, shift_start.split(":"))

    shift_start_dt = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        start_hour,
        start_minute,
        tzinfo=IST,
    )

    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)

    punch_in_ist = punch_in.astimezone(IST)

    late_seconds = (punch_in_ist - shift_start_dt).total_seconds()

    if late_seconds <= 10 * 60:
        return 0

    return int(late_seconds // 60)



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    hours = (Decimal(seconds) / Decimal(3600)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(hours)



def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    shift_date = date.fromisoformat(date_str)

    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))

    shift_start_minutes = start_hour * 60 + start_minute
    shift_end_minutes = end_hour * 60 + end_minute

    shift_end_date = shift_date
    if shift_end_minutes <= shift_start_minutes:
        shift_end_date += timedelta(days=1)

    shift_end_dt = datetime(
        shift_end_date.year,
        shift_end_date.month,
        shift_end_date.day,
        end_hour,
        end_minute,
        tzinfo=IST,
    )

    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    punch_out_ist = punch_out.astimezone(IST)
    overtime_seconds = (punch_out_ist - shift_end_dt).total_seconds()

    if overtime_seconds < 30 * 60:
        return 0

    return int(overtime_seconds // 60)



def normalize_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize BSON/Python datetimes to aware UTC at whole-second precision."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0)


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp() * 1000)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value):
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must be EMP followed by 4-6 digits")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        if not value.strip() or len(value) > 100:
            raise ValueError("name must contain 1-100 characters")
        return value

   
    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if (
            len(value) > 120
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value)
        ):
            raise ValueError("Invalid email format")
        return value


    @field_validator("department")
    @classmethod
    def validate_department(cls, value):
        if not value.strip() or len(value) > 50:
            raise ValueError("department must contain 1-50 characters")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Shift time must use HH:MM format")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return value

    @model_validator(mode="after")
    def validate_shift_difference(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self





class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value not in {"PRESENT", "WFH", "ON_DUTY"}:
            raise ValueError(
                "status must be PRESENT, WFH, or ON_DUTY"
            )
        return value

   
    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value

        if not 100000000000 <= value <= 4102444800000:
            raise ValueError(
                "punched_at must be epoch milliseconds within the allowed range"
            )

        return value

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value

        if not 100000000000 <= value <= 4102444800000:
            raise ValueError(
                "punched_at must be epoch milliseconds within the allowed range"
            )

        return value





class AttendanceCorrectionIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str
    regularized_by: str

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_punch_timestamps(cls, value):
        if value is None:
            return value

        if not 100000000000 <= value <= 4102444800000:
            raise ValueError(
                "Timestamp must be epoch milliseconds within the allowed range"
            )

        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value):
        if not 5 <= len(value) <= 200:
            raise ValueError("reason must contain 5-200 characters")
        return value

    @field_validator("regularized_by")
    @classmethod
    def validate_regularized_by(cls, value):
        if not 1 <= len(value) <= 50:
            raise ValueError("regularized_by must contain 1-50 characters")
        return value






# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    doc.pop("_id", None)

    # Convert datetime to milliseconds for the API response
    doc["created_at"] = to_epoch_ms(doc["created_at"])

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    query = {}

    if department:
        query["department"] = department

    total = db.employees.count_documents(query)

    employees = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for employee in employees:
        if isinstance(employee.get("created_at"), datetime):
            employee["created_at"] = to_epoch_ms(
                employee["created_at"]
            )

    return {
        "items": employees,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )
    
    # Validate date filters using exact YYYY-MM-DD format.
    for date_value, field_name in (
        (date_from, "date_from"),
        (date_to, "date_to"),
    ):
        if date_value is not None:
            try:
                parsed_date = date.fromisoformat(date_value)
                if parsed_date.isoformat() != date_value:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field_name} must use YYYY-MM-DD format",
                )

    # Ensure the start date is not after the end date.
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to",
        )

    allowed_statuses = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if status:
        query["status"] = status

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            query["date"]["$gte"] = date_from

        if date_to:
            query["date"]["$lte"] = date_to

    # Count matching records in MongoDB.
    total = db.attendance_logs.count_documents(query)

    # Sort and paginate in MongoDB instead of loading all records.
    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    # Convert timestamps to epoch milliseconds for the API response.
   
    # Convert timestamps to epoch milliseconds for the API response.
    for doc in docs:
        doc["punch_in"] = to_epoch_ms(doc.get("punch_in"))
        doc["punch_out"] = to_epoch_ms(doc.get("punch_out"))

        # Convert timestamps in correction history.
        for entry in doc.get("history", []):
            if entry.get("at") is not None:
                entry["at"] = to_epoch_ms(entry["at"])

            for field, change in entry.get("changes", {}).items():
                if field in ("punch_in", "punch_out") and isinstance(change, dict):
                    for key in ("from", "to"):
                        if change.get(key) is not None:
                            change[key] = to_epoch_ms(change[key])


    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    # 1. Find the employee in MongoDB.
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 2. Convert the supplied epoch milliseconds to UTC.
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        )
    else:
        ts = datetime.now(timezone.utc)

    # Keep timestamps at whole-second precision.
    ts = ts.replace(microsecond=0)

    # 3. Convert the timestamp to Indian Standard Time.
    ts_ist = ts.astimezone(IST)

    # 4. Convert shift times to minutes after midnight.
    start_hour, start_minute = map(
        int, emp["shift_start"].split(":")
    )
    end_hour, end_minute = map(
        int, emp["shift_end"].split(":")
    )

    shift_start_minutes = start_hour * 60 + start_minute
    shift_end_minutes = end_hour * 60 + end_minute

    # 5. Determine the attendance date in IST.
    d = ts_ist.date().isoformat()

    # For an overnight shift, early-morning punches belong
    # to the previous day's shift when before shift end.
    if (
        shift_end_minutes <= shift_start_minutes
        and ts_ist.hour * 60 + ts_ist.minute < shift_end_minutes
    ):
        d = (ts_ist.date() - timedelta(days=1)).isoformat()

    # 6. Prevent duplicate attendance for the same employee/date.
    if db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "date": d}
    ):
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    # 7. Calculate lateness and create the attendance record.
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            emp["shift_start"],
            d,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    # 8. Save the record; the unique index also prevents duplicates.
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    # 9. Remove MongoDB's internal ID from the API response.
    doc.pop("_id", None)

    # Convert datetime values to epoch milliseconds.
    doc["punch_in"] = to_epoch_ms(doc["punch_in"])
    doc["punch_out"] = to_epoch_ms(doc["punch_out"])

    return doc



@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Use the supplied timestamp or the current UTC time.
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        )
    else:
        ts = datetime.now(timezone.utc)

    ts = ts.replace(microsecond=0)

    # Find the latest punch-in at or before this punch-out.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched out",
        )

    punch_in_time = record["punch_in"]

    # PyMongo may return a naive datetime representing UTC.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    duration_seconds = (ts - punch_in_time).total_seconds()

    if duration_seconds <= 0 or duration_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    work_hours = compute_work_hours(punch_in_time, ts)

    overtime_minutes = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )

    # Update only if another request hasn't already punched out.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": work_hours < 4.50,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched out",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)
    updated["punch_in"] = to_epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = to_epoch_ms(updated.get("punch_out"))

    return updated




@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrectionIn,
):
    # 1. Validate the attendance date.
    try:
        correction_date = datetime.strptime(date, "%Y-%m-%d").date()
        if correction_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    # 2. Separate attendance fields from audit information.
    request_data = body.model_dump(exclude_unset=True)
    reason = request_data.pop("reason")
    regularized_by = request_data.pop("regularized_by")

    changes = request_data

    if not changes or all(value is None for value in changes.values()):
        raise HTTPException(
            status_code=422,
            detail="At least one correction field is required",
        )

    # 3. Validate the requested status.
    allowed_statuses = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}

    if "status" in changes and changes["status"] is not None:
        if changes["status"] not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail="Invalid attendance status",
            )

    # 4. Find the attendance record and employee.
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 5. Convert requested timestamps to UTC datetimes.
    updated_fields = {}

    for field in ("status", "punch_in", "punch_out"):
        if field not in changes or changes[field] is None:
            continue

        new_value = changes[field]

        if field in ("punch_in", "punch_out"):
            new_value = datetime.fromtimestamp(
                new_value / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)

        old_value = record.get(field)
        if field in ("punch_in", "punch_out"):
            old_value = normalize_utc(old_value)
            new_value = normalize_utc(new_value)

        if old_value != new_value:
            updated_fields[field] = new_value

    if not updated_fields:
        raise HTTPException(
            status_code=422,
            detail="No fields changed",
        )

    # 6. Determine the final status and punch times.
    effective = {**record, **updated_fields}
    final_status = effective.get("status")
    punch_in_time = normalize_utc(effective.get("punch_in"))
    punch_out_time = normalize_utc(effective.get("punch_out"))

    # ABSENT and LEAVE must not have punch times.
    if final_status in {"ABSENT", "LEAVE"}:
        if (
            "punch_in" in changes and changes["punch_in"] is not None
        ) or (
            "punch_out" in changes and changes["punch_out"] is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch times",
            )

        punch_in_time = None
        punch_out_time = None
        updated_fields["punch_in"] = None
        updated_fields["punch_out"] = None

    # Presence statuses require a punch-in.
    elif final_status in {"PRESENT", "WFH", "ON_DUTY"}:
        if punch_in_time is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires a punch-in",
            )

    # 7. Validate punch times.
    if punch_in_time is not None:
        punch_in_utc = (
            punch_in_time.replace(tzinfo=timezone.utc)
            if punch_in_time.tzinfo is None
            else punch_in_time.astimezone(timezone.utc)
        )
        punch_in_ist = punch_in_utc.astimezone(IST)

        # A corrected punch-in must belong to this attendance date.
        expected_date = punch_in_ist.date()

        shift_start_minutes = sum(
            int(part) * multiplier
            for part, multiplier in zip(
                employee["shift_start"].split(":"),
                (60, 1),
            )
        )
        shift_end_minutes = sum(
            int(part) * multiplier
            for part, multiplier in zip(
                employee["shift_end"].split(":"),
                (60, 1),
            )
        )

        if (
            shift_end_minutes <= shift_start_minutes
            and punch_in_ist.hour * 60 + punch_in_ist.minute
            < shift_end_minutes
        ):
            expected_date -= timedelta(days=1)

        if expected_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

    if punch_in_time is not None and punch_out_time is not None:
        if punch_out_time <= punch_in_time:
            raise HTTPException(
                status_code=422,
                detail="Punch-out must be after punch-in",
            )

        if (
            punch_out_time - punch_in_time
        ).total_seconds() > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="Punch duration cannot exceed 24 hours",
            )

    # 8. Recompute derived fields after the correction.
    if punch_in_time is not None:
        updated_fields["late_minutes"] = compute_late_minutes(
            punch_in_time,
            employee["shift_start"],
            date,
        )
    else:
        updated_fields["late_minutes"] = 0

    if punch_in_time is not None and punch_out_time is not None:
        work_hours = compute_work_hours(
            punch_in_time,
            punch_out_time,
        )

        updated_fields["work_hours"] = work_hours
        updated_fields["overtime_minutes"] = compute_overtime(
            punch_out_time,
            employee["shift_start"],
            employee["shift_end"],
            date,
        )
        updated_fields["half_day"] = work_hours < 4.50
    else:
        updated_fields["work_hours"] = None
        updated_fields["overtime_minutes"] = 0
        updated_fields["half_day"] = False

    # 9. Build one audit entry containing only fields that changed.
    audit_changes = {}

    for field, new_value in updated_fields.items():
        old_value = record.get(field)
        if field in ("punch_in", "punch_out"):
            old_value = normalize_utc(old_value)
            new_value = normalize_utc(new_value)

        if old_value != new_value:
            audit_changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    now = datetime.now(timezone.utc).replace(microsecond=0)

    history_entry = {
        "at": now,
        "by": regularized_by,
        "reason": reason,
        "changes": audit_changes,
    }

    # 10. Update the record and append exactly one history entry.
    history_filter = (
        {"history": {"$exists": False}}
        if "history" not in record
        else {"history": record["history"]}
    )
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            **history_filter,
        },
        {
            "$set": updated_fields,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance changed concurrently; please retry",
        )

    # 11. Return timestamps in epoch milliseconds.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    updated["punch_in"] = to_epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = to_epoch_ms(updated.get("punch_out"))

    for entry in updated.get("history", []):
        entry["at"] = to_epoch_ms(entry.get("at"))

        for field_change in entry.get("changes", {}).values():
            for key in ("from", "to"):
                value = field_change.get(key)
                if isinstance(value, datetime):
                    field_change[key] = to_epoch_ms(value)

    return updated




@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate month format: YYYY-MM.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Working days begin on the later of the joining date or month start.
    joined_on = date.fromisoformat(employee["joined_on"])
    first_working_date = max(joined_on, month_start)

    working_days = 0
    current_day = first_working_date

    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    # Summarize attendance in MongoDB, not in Python.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$set": {
                "date_obj": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$set": {
                "is_weekday": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date_obj"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date_obj"}, 6]},
                    ]
                },
                "is_present": {
                    "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_weekday",
                                    "$is_present",
                                ]
                            },
                            {
                                "$cond": [
                                    "$half_day",
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    summary = results[0] if results else {}

    present_days = summary.get("present_days", 0)
    attendance_pct = None

    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal(100)
             / Decimal(working_days)).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": summary.get("leave_days", 0),
        "late_count": summary.get("late_count", 0),
        "total_late_minutes": summary.get("total_late_minutes", 0),
        "total_overtime_minutes": summary.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }




@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    # 1. Validate the requested month.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    # 2. Find employees who had joined by the end of this month.
    employee_query = {
        "joined_on": {"$lte": month_end.isoformat()}
    }

    if department:
        employee_query["department"] = department

    # 3. Calculate attendance statistics in MongoDB.
    pipeline = [
        {"$match": employee_query},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start.isoformat(),
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$date",
                                            month_end.isoformat(),
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "date_obj": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                }
                            },
                            "is_present": {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                        }
                    },
                    {
                        "$set": {
                            "is_weekday": {
                                "$and": [
                                    {
                                        "$gte": [
                                            {"$dayOfWeek": "$date_obj"},
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$dayOfWeek": "$date_obj"},
                                            6,
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,

                            # Count weekday present days.
                            # Half-days contribute 0.5.
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$is_weekday",
                                                "$is_present",
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },

                            # Total valid work hours.
                            "total_work_hours": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$is_present",
                                                {
                                                    "$ne": [
                                                        "$work_hours",
                                                        None,
                                                    ]
                                                },
                                            ]
                                        },
                                        "$work_hours",
                                        0,
                                    ]
                                }
                            },

                            # Number of records contributing work hours.
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$is_present",
                                                {
                                                    "$ne": [
                                                        "$work_hours",
                                                        None,
                                                    ]
                                                },
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },

                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": ["$late_minutes", 0]},
                                        1,
                                        0,
                                    ]
                                }
                            },

                            "total_late_minutes": {
                                "$sum": {
                                    "$ifNull": ["$late_minutes", 0]
                                }
                            },

                            "leave_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "LEAVE"]},
                                        1,
                                        0,
                                    ]
                                }
                            },

                            "on_duty_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "ON_DUTY"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "attendance_summary",
            }
        },

        # 4. Give employees with no attendance records zero totals.
        {
            "$set": {
                "summary": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$attendance_summary", 0]},
                        {
                            "present_days": 0,
                            "total_work_hours": 0,
                            "work_hours_count": 0,
                            "late_count": 0,
                            "total_late_minutes": 0,
                            "leave_count": 0,
                            "on_duty_count": 0,
                        },
                    ]
                }
            }
        },

        # 5. Combine the totals across all employees in each department.
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$summary.present_days"},
                "total_work_hours": {
                    "$sum": "$summary.total_work_hours"
                },
                "work_hours_count": {
                    "$sum": "$summary.work_hours_count"
                },
                "late_count": {"$sum": "$summary.late_count"},
                "total_late_minutes": {
                    "$sum": "$summary.total_late_minutes"
                },
                "leave_count": {"$sum": "$summary.leave_count"},
                "on_duty_count": {"$sum": "$summary.on_duty_count"},
            }
        },

        # 6. Calculate the department-wide average.
        {
            "$set": {
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {"$divide": [
                            {"$floor": {"$add": [
                                {"$multiply": [
                                    {"$divide": ["$total_work_hours", "$work_hours_count"]},
                                    100,
                                ]},
                                0.5,
                            ]}},
                            100,
                        ]},
                        None,
                    ]
                }
            }
        },

        # 7. Return only the fields required in the response.
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },

        # 8. Return departments in alphabetical order.
        {"$sort": {"department": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    # Validate month format.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    # Aggregate late attendance by employee.
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    # Apply the optional department filter.
    if department:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend([
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }



@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    # Validate the requested date range.
    try:
        start_date = date.fromisoformat(from_date)
        end_date = date.fromisoformat(to_date)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if start_date.isoformat() != from_date or end_date.isoformat() != to_date:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from",
        )

    day_count = (end_date - start_date).days + 1
    if day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # Confirm that the department exists.
    if not db.employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt_exclusive = datetime.combine(
        end_date + timedelta(days=1),
        datetime.min.time(),
    )

    # Start with one employee to ensure a seed date exists.
    pipeline = [
        {"$match": {"department": department}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "date": {"$literal": start_dt},
                "status": {"$literal": None},
                "half_day": {"$literal": False},
                "late_minutes": {"$literal": 0},
                "_seed": {"$literal": True},
            }
        },
        # Combine the seed date with actual attendance records.
        {
            "$unionWith": {
                "coll": "attendance_logs",
                "pipeline": [
                    {
                        "$match": {
                            "date": {
                                "$gte": start_date.isoformat(),
                                "$lte": end_date.isoformat(),
                            }
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {"$match": {"employee.department": department}},
                    {
                        "$project": {
                            "_id": 0,
                            "date": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                }
                            },
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                            "_seed": {"$literal": False},
                        }
                    },
                ],
            }
        },
        # Count present employees and late records per date.
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$_seed", True]},
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$_seed", True]},
                                    {"$gt": ["$late_minutes", 0]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        # Generate all requested dates; the upper bound is exclusive.
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt_exclusive],
                },
            }
        },
        {
            "$set": {
                "date_string": {
                    "$dateToString": {
                        "date": "$_id",
                        "format": "%Y-%m-%d",
                        "timezone": "UTC",
                    }
                },
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                    ]
                },
            }
        },
        # Calculate department headcount as of each date.
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$trend_date"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_data",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_data.count", 0]},
                        0,
                    ]
                },
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {
                                    "$gt": [
                                        {
                                            "$ifNull": [
                                                {
                                                    "$arrayElemAt": [
                                                        "$headcount_data.count",
                                                        0,
                                                    ]
                                                },
                                                0,
                                            ]
                                        },
                                        0,
                                    ]
                                },
                            ]
                        },
                        {"$divide": [
                            {"$floor": {"$add": [
                                {"$multiply": [
                                    {"$divide": [
                                        "$present_count",
                                        {"$arrayElemAt": ["$headcount_data.count", 0]},
                                    ]},
                                    10000,
                                ]},
                                0.5,
                            ]}},
                            10000,
                        ]},
                        None,
                    ]
                },
            }
        },
        # Calculate the moving average over up to seven calendar days.
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_avg_7d", None]},
                        {"$divide": [
                            {"$floor": {"$add": [
                                {"$multiply": ["$moving_avg_7d", 10000]},
                                0.5,
                            ]}},
                            10000,
                        ]},
                        None,
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    return {
        "department": department,
        "items": items,
    }



EXPLAIN_ENDPOINTS = {
    "attendance_list",
    "employee_monthly",
    "department_summary",
    "late_leaderboard",
    "department_trend",
}




@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    if endpoint not in EXPLAIN_ENDPOINTS:
        raise HTTPException(
            status_code=404,
            detail="Endpoint not found",
        )

    # Validate the month used by monthly analytics.
    def get_month_range(value):
        if not value or not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", value
        ):
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        year, month_number = map(int, value.split("-"))
        start = date(year, month_number, 1)

        if month_number == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_number + 1, 1)

        return start, next_month - timedelta(days=1)

    # 1. Explain attendance list.
    if endpoint == "attendance_list":
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(
                status_code=422,
                detail="page must be >= 1 and page_size must be between 1 and 100",
            )

        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        allowed_statuses = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}
        if status is not None and status not in allowed_statuses:
            raise HTTPException(status_code=422, detail="Invalid attendance status")
        if status:
            query["status"] = status

        for date_value, field_name in ((date_from, "date_from"), (date_to, "date_to")):
            if date_value is not None:
                try:
                    parsed = date.fromisoformat(date_value)
                    if parsed.isoformat() != date_value:
                        raise ValueError
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=422,
                        detail=f"{field_name} must use YYYY-MM-DD format",
                    )

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from

            if date_to:
                query["date"]["$lte"] = date_to

            if date_from and date_to and date_from > date_to:
                raise HTTPException(
                    status_code=422,
                    detail="date_from must be on or before date_to",
                )

        explain_result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result,
        }

    # 2. Explain employee monthly analytics.
    if endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(
                status_code=422,
                detail="emp_code is required for employee_monthly",
            )

        month_start, month_end = get_month_range(month)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                }
            },
            {
                "$set": {
                    "date_obj": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d",
                        }
                    }
                }
            },
            {
                "$set": {
                    "is_weekday": {
                        "$and": [
                            {"$gte": [{"$dayOfWeek": "$date_obj"}, 2]},
                            {"$lte": [{"$dayOfWeek": "$date_obj"}, 6]},
                        ]
                    },
                    "is_present": {
                        "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                    },
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {"$and": ["$is_weekday", "$is_present"]},
                                {"$cond": ["$half_day", 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$late_minutes", 0]}
                    },
                    "total_overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    },
                }
            },
        ]

        explain_result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result,
        }

    # 3. Explain department summary analytics.
    if endpoint == "department_summary":
        month_start, month_end = get_month_range(month)

        employee_query = {
            "joined_on": {"$lte": month_end.isoformat()}
        }

        if department:
            employee_query["department"] = department

        pipeline = [
            {"$match": employee_query},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$code"]},
                                        {
                                            "$gte": [
                                                "$date",
                                                month_start.isoformat(),
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$date",
                                                month_end.isoformat(),
                                            ]
                                        },
                                    ]
                                }
                            }
                        },
                        {
                            "$set": {
                                "date_obj": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d",
                                    }
                                },
                                "is_present": {
                                    "$in": [
                                        "$status",
                                        ["PRESENT", "WFH", "ON_DUTY"],
                                    ]
                                },
                            }
                        },
                        {
                            "$set": {
                                "is_weekday": {
                                    "$and": [
                                        {
                                            "$gte": [
                                                {"$dayOfWeek": "$date_obj"},
                                                2,
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                {"$dayOfWeek": "$date_obj"},
                                                6,
                                            ]
                                        },
                                    ]
                                }
                            }
                        },
                        {
                            "$group": {
                                "_id": None,
                                "present_days": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    "$is_weekday",
                                                    "$is_present",
                                                ]
                                            },
                                            {
                                                "$cond": [
                                                    {"$eq": ["$half_day", True]},
                                                    0.5,
                                                    1,
                                                ]
                                            },
                                            0,
                                        ]
                                    }
                                },
                                "total_work_hours": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    "$is_present",
                                                    {"$ne": ["$work_hours", None]},
                                                ]
                                            },
                                            "$work_hours",
                                            0,
                                        ]
                                    }
                                },
                                "work_hours_count": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    "$is_present",
                                                    {"$ne": ["$work_hours", None]},
                                                ]
                                            },
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "late_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$gt": ["$late_minutes", 0]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "total_late_minutes": {
                                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                                },
                                "leave_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$status", "LEAVE"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "on_duty_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$status", "ON_DUTY"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        },
                    ],
                    "as": "attendance_summary",
                }
            },
            {
                "$set": {
                    "summary": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$attendance_summary", 0]},
                            {
                                "present_days": 0,
                                "total_work_hours": 0,
                                "work_hours_count": 0,
                                "late_count": 0,
                                "total_late_minutes": 0,
                                "leave_count": 0,
                                "on_duty_count": 0,
                            },
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "headcount": {"$sum": 1},
                    "present_days": {"$sum": "$summary.present_days"},
                    "total_work_hours": {
                        "$sum": "$summary.total_work_hours"
                    },
                    "work_hours_count": {
                        "$sum": "$summary.work_hours_count"
                    },
                    "late_count": {"$sum": "$summary.late_count"},
                    "total_late_minutes": {
                        "$sum": "$summary.total_late_minutes"
                    },
                    "leave_count": {"$sum": "$summary.leave_count"},
                    "on_duty_count": {"$sum": "$summary.on_duty_count"},
                }
            },
            {
                "$set": {
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$work_hours_count", 0]},
                            {"$divide": [
                                {"$floor": {"$add": [
                                    {"$multiply": [
                                        {"$divide": ["$total_work_hours", "$work_hours_count"]},
                                        100,
                                    ]},
                                    0.5,
                                ]}},
                                100,
                            ]},
                            None,
                        ]
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": 1,
                    "present_days": 1,
                    "avg_work_hours": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]

        explain_result = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result,
        }

    # 4. Explain late leaderboard analytics.
    if endpoint == "late_leaderboard":
        if limit < 1 or limit > 50:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 50")
        month_start, month_end = get_month_range(month)

        if limit < 1 or limit > 50:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 50",
            )

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department:
            pipeline.append(
                {"$match": {"employee.department": department}}
            )

        pipeline.extend([
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
            {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
        ])

        explain_result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result,
        }

    # 5. Explain department trend analytics.
    if endpoint == "department_trend":
        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required for department_trend",
            )

        if not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="from and to are required for department_trend",
            )

        try:
            start_date = date.fromisoformat(from_date)
            end_date = date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD format",
            )

        if (
            start_date.isoformat() != from_date
            or end_date.isoformat() != to_date
        ):
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD format",
            )

        if end_date < start_date:
            raise HTTPException(
                status_code=422,
                detail="to must be on or after from",
            )

        if (end_date - start_date).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days",
            )

        if not db.employees.find_one({"department": department}):
            raise HTTPException(
                status_code=404,
                detail="Department not found",
            )

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt_exclusive = datetime.combine(
            end_date + timedelta(days=1),
            datetime.min.time(),
        )

        pipeline = [
            {"$match": {"department": department}},
            {"$limit": 1},
            {
                "$project": {
                    "_id": 0,
                    "date": {"$literal": start_dt},
                    "status": {"$literal": None},
                    "half_day": {"$literal": False},
                    "late_minutes": {"$literal": 0},
                    "_seed": {"$literal": True},
                }
            },
            {
                "$unionWith": {
                    "coll": "attendance_logs",
                    "pipeline": [
                        {
                            "$match": {
                                "date": {
                                    "$gte": start_date.isoformat(),
                                    "$lte": end_date.isoformat(),
                                }
                            }
                        },
                        {
                            "$lookup": {
                                "from": "employees",
                                "localField": "emp_code",
                                "foreignField": "emp_code",
                                "as": "employee",
                            }
                        },
                        {"$unwind": "$employee"},
                        {"$match": {"employee.department": department}},
                        {
                            "$project": {
                                "_id": 0,
                                "date": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d",
                                    }
                                },
                                "status": 1,
                                "half_day": 1,
                                "late_minutes": 1,
                                "_seed": {"$literal": False},
                            }
                        },
                    ],
                }
            },
            {
                "$group": {
                    "_id": "$date",
                    "present_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$ne": ["$_seed", True]},
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                    ]
                                },
                                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$ne": ["$_seed", True]},
                                        {"$gt": ["$late_minutes", 0]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {
                "$densify": {
                    "field": "_id",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start_dt, end_dt_exclusive],
                    },
                }
            },
            {
                "$set": {
                    "date_string": {
                        "$dateToString": {
                            "date": "$_id",
                            "format": "%Y-%m-%d",
                            "timezone": "UTC",
                        }
                    },
                    "present_count": {"$ifNull": ["$present_count", 0]},
                    "late_count": {"$ifNull": ["$late_count", 0]},
                    "is_working_day": {
                        "$and": [
                            {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                            {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                        ]
                    },
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"trend_date": "$date_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$department", department]},
                                        {"$lte": ["$joined_on", "$$trend_date"]},
                                    ]
                                }
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "headcount_data",
                }
            },
            {
                "$set": {
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$headcount_data.count", 0]},
                            0,
                        ]
                    },
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {
                                        "$gt": [
                                            {
                                                "$ifNull": [
                                                    {
                                                        "$arrayElemAt": [
                                                            "$headcount_data.count",
                                                            0,
                                                        ]
                                                    },
                                                    0,
                                                ]
                                            },
                                            0,
                                        ]
                                    },
                                ]
                            },
                            {"$divide": [
                                {"$floor": {"$add": [
                                    {"$multiply": [
                                        {"$divide": [
                                            "$present_count",
                                            {"$arrayElemAt": ["$headcount_data.count", 0]},
                                        ]},
                                        10000,
                                    ]},
                                    0.5,
                                ]}},
                                10000,
                            ]},
                            None,
                        ]
                    },
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"_id": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$date_string",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_avg_7d", None]},
                        {"$divide": [
                            {"$floor": {"$add": [
                                {"$multiply": ["$moving_avg_7d", 10000]},
                                0.5,
                            ]}},
                            10000,
                        ]},
                        None,
                    ]
                },
                }
            },
            {"$sort": {"date": 1}},
        ]

        explain_result = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result,
        }

    raise HTTPException(
        status_code=501,
        detail="Explanation for this endpoint is not implemented yet",
    )


