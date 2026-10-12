# Employee Attendance & Analytics API

A FastAPI application backed by MongoDB for employee management, attendance punch-in/punch-out, and attendance analytics.

## Tech Stack

- **Backend:** Python, FastAPI
- **Database:** MongoDB 6.0+
- **Driver:** PyMongo
- **Validation:** Pydantic

## Prerequisites

- Python 3.10+
- MongoDB 6.0+ running locally or through MongoDB Atlas
- Git

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/Shantanuku9258/Hr-one-Assignment.git
cd Hr-one-Assignment
```

### 2. Create and activate a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the repository root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Update these values if your MongoDB instance uses a different connection string or database name. Do not commit `.env` or any credentials.

### 5. Start the API

```bash
uvicorn app.main:app --port 8000
```

The API will be available at:

- **Base URL:** http://127.0.0.1:8000
- **Swagger UI:** http://127.0.0.1:8000/docs
- **ReDoc:** http://127.0.0.1:8000/redoc

## API Documentation

Use the Swagger UI at `/docs` to inspect available endpoints and send test requests.

## Project Structure

```text
Hr-one-Assignment/
├── app/
│   ├── __init__.py
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
├── DECISIONS.md
└── .gitignore
```

## Submission Notes

This repository contains the application entry point, dependencies, and assignment documentation. Keep environment files, credentials, and other secrets out of version control.