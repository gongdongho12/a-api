---
description: Guide for setting up the local MySQL database
---

# Local Database Setup

Follow these steps to set up your local MySQL database for development.

1.  **Install MySQL**
    - If you haven't installed MySQL, install it (e.g., `brew install mysql` on macOS).
    - Start the service: `brew services start mysql`.

2.  **Create Database and User**
    - Log in to MySQL: `mysql -u root`
    - Run the following commands:
      ```sql
      CREATE DATABASE db;
      CREATE USER 'user'@'localhost' IDENTIFIED BY 'password';
      GRANT ALL PRIVILEGES ON db.* TO 'user'@'localhost';
      FLUSH PRIVILEGES;
      ```
    - *Note: These credentials match the defaults in `app/core/config.py`. Update them there if you choose different ones.*

3.  **Install Dependencies**
    - Ensure you have the project dependencies installed:
      ```bash
      pip install -r requirements.txt
      ```

4.  **Initialize Tables**
    - Run the initialization script to create tables from models:
      ```bash
      python -m app.db.init_db
      ```

5.  **Verify**
    - Log in to MySQL and check the tables:
      ```sql
      USE db;
      SHOW TABLES;
      ```
