from typing import Optional
from pydantic import BaseModel

# Shared properties
class UserBase(BaseModel):
    email: str
    full_name: Optional[str] = None

# Properties to receive on user creation
class UserCreate(UserBase):
    pass

# Properties to receive on user update
class UserUpdate(UserBase):
    pass

# Properties shared by models stored in DB
class UserInDBBase(UserBase):
    id: int

    class Config:
        from_attributes = True

# Properties to return to client
class User(UserInDBBase):
    pass
