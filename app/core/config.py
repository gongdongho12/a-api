from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "A-API"
    API_V1_STR: str = "/api/v1"
    
    # Database
    MYSQL_USER: Optional[str] = None
    MYSQL_PASSWORD: Optional[str] = None
    MYSQL_SERVER: Optional[str] = "127.0.0.1"
    MYSQL_PORT: Optional[str] = "3306"
    MYSQL_DB: Optional[str] = "db"
    
    SQLALCHEMY_DATABASE_URI: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore other extra vars if any

    @property
    def assemble_db_connection(self) -> str:
        if self.SQLALCHEMY_DATABASE_URI:
            return self.SQLALCHEMY_DATABASE_URI
            
        if self.MYSQL_USER and self.MYSQL_PASSWORD:
            return f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
            
        return "mysql+pymysql://user:password@localhost/db"

settings = Settings()
if not settings.SQLALCHEMY_DATABASE_URI:
    settings.SQLALCHEMY_DATABASE_URI = settings.assemble_db_connection

