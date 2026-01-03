# A-API

A structured FastAPI project with MySQL integration, SQLAlchemy ORM, and Pydantic schemas.

## Features

- **FastAPI**: Modern, fast (high-performance) web framework for building APIs.
- **SQLAlchemy & PyMySQL**: Database ORM and MySQL driver.
- **Pydantic Settings**: Configuration management.
- **Modular Structure**: Organized into core, api, models, schemas, and db packages.

## Getting Started

### Prerequisites

- Python 3.8+
- MySQL Server

### Installation

1.  **Clone the repository**
2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Setup Database**:
    - Follow the [Database Setup Guide](.agent/workflows/setup-db.md).

### Running the Application

**Using Make (Recommended):**
```bash
make run
```
To stop the server and force clear the port if needed:
```bash
make clean
```

**Manual:**
```bash
uvicorn app.main:app --reload
```

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Development Workflows

- [API Development Workflow](.agent/workflows/work-api.md): How to add new endpoints.
- [Database Setup](.agent/workflows/setup-db.md): How to initialize the database.

## Project Rules

Please refer to [.agent/rules.md](.agent/rules.md) for mandatory project conventions and architecture guidelines.
