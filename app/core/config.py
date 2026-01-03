from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "A-API"
    API_V1_STR: str = "/api/v1"
    
    # Database
    # Default to a local mysql connection string for development
    SQLALCHEMY_DATABASE_URI: str = "mysql+pymysql://user:password@localhost/db"

    class Config:
        case_sensitive = True

settings = Settings()
