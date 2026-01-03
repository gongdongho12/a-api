# Project Rules & Context

This document defines the mandatory conventions and architectural context for the A-API project.

## Architecture

- **Framework**: FastAPI
- **Database**: MySQL linked via SQLAlchemy ORM.
- **Pattern**: Repository/Service pattern is optional but recommended for complex logic. For simple CRUD, Controller-Service (Endpoint-CRUD) is sufficient.
- **Configuration**: All environment variables and settings must be managed in `app/core/config.py`.

## Code Conventions

### 1. Database Connectivity
- Always use `app.api.deps.get_db` dependency to inject the database session into endpoints.
- **NEVER** instantiate `SessionLocal` directly inside an endpoint logic without a try/finally block (the dependency handles this).

### 2. Models & Schemas
- **Models** (`app/models/`): SQLAlchemy models inheriting from `app.db.base.Base`.
- **Schemas** (`app/schemas/`): Pydantic models. separate `Create`, `Update`, and `Response` schemas.
- **Circular Imports**: Import all models in `app/db/base.py` to ensure they are registered for migrations/init.

### 3. API & Endpoints
- **Routing**: All routers must be registered in `app/api/v1/api.py`.
- **Versioning**: API versioning (e.g., `/api/v1`) is mandatory.
- **Return Types**: Explicitly define `response_model` in `@router` decorators.

### 4. Workflows
- Refer to `.agent/workflows/` for standard procedures for adding APIs and setting up the DB.
