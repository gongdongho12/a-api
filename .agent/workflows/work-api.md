---
description: Guide for developing new APIs
---

# API Development Workflow

Follow these steps to add a new API endpoint to the project.

1.  **Define the Model**
    - Create or update a model in `app/models/`.
    - Ensure it inherits from `app.db.base.Base`.

2.  **Create Pydantic Schemas**
    - Create or update schemas in `app/schemas/`.
    - Define `Base`, `Create`, `Update`, and `Response` schemas.

3.  **Implement CRUD Operations**
    - Implement CRUD logic in `app/crud/` (Optional, for complex logic) or directly in the endpoint.

4.  **Create API Endpoint**
    - Create a new router file in `app/api/v1/endpoints/`.
    - Define the path operations (GET, POST, PUT, DELETE).
    - Use `Depends` to inject the database session.

5.  **Register Router**
    - Add the new router to `app/api/v1/api.py`.

6.  **Verify**
    - Run the server: `uvicorn app.main:app --reload`
    - Check Swagger UI: `http://localhost:8000/docs`
