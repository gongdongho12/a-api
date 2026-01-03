from app.db.base import Base
from app.db.session import engine
from app.models import user  # Import all models here so they are registered with Base

def init_db():
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully.")

if __name__ == "__main__":
    init_db()
