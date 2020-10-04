import os
from dateutil.relativedelta import relativedelta
from datetime import date
from typing import List
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from enum import Enum
from pydantic import BaseModel
from google.oauth2 import id_token
from google.auth.transport import requests
from jose import jwt, JWTError
from utils import DBService, generate_jwt, OAuth2PasswordBearerCookie
from config import GOOGLE, MONGO, JWT


class CategoryName(str, Enum):
    car = "Car"
    insurance = "Insurance"
    food = "Food"
    hobbies = "Hobbies"
    home = "Home"
    other = "Other"

class ExpenseData(BaseModel):
    title: str
    sum: float
    date: date
    category: CategoryName

class Expense(BaseModel):
    id:  str
    title: str
    sum: float
    date: date
    category: CategoryName

class CategoryHistoryPoint(BaseModel):
    date: date
    total: float

class CategoryHistory(BaseModel):
    categoryname: str
    history: List[CategoryHistoryPoint]

class CategoryStats(BaseModel):
    categoryname: str
    total: float
    expenselist: List[Expense]

class MonthlyStats(BaseModel):
    monthtotal: float
    categorystats: List[CategoryStats]

class IDToken(BaseModel):
    google_id_token: str

MONGODB_URI = os.environ.get("MONGO_URI", MONGO["uri"])
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", GOOGLE["clientID"])
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", JWT["secret"])
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", JWT["algorithm"])
JWT_EXPIRE = os.environ.get("JWT_EXPIRE", 15)
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "localhost")
COOKIE_NAME = os.environ.get("COOKIE_NAME", "ACCESS_TOKEN")

dataservice = DBService(MONGODB_URI)
origins = [
    "http://localhost:8080",
]

app = FastAPI(title="iSpend", description="Backend powered by FastAPI", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
oauth2_scheme = OAuth2PasswordBearerCookie(tokenUrl="/login", cookie_name=COOKIE_NAME)

async def verify_jwt(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Could not validate credentials"
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = dataservice.find_user_by_id(user_id)
    if user is None:
        raise credentials_exception
    return str(user["_id"])

@app.get("/")
async def ready():
    return {"message": "Hello World"}

@app.post("/login")
async def authenticate(token: IDToken):
    #1. validate google's id_token
    token = token.dict()["google_id_token"]
    try:
        google_id_info = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        google_id = google_id_info["sub"]
        google_name = google_id_info["name"]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Could not validate token"
        )
    #2. look up user id in db
    user = dataservice.find_user_by_google_id(google_id)
    if user is None:
        user = dataservice.create_new_user(google_id, google_name)
    #3. store jwt token in httpOnly cookie with samesite=lax
    access_token = generate_jwt({"sub": str(user["_id"]), "iss": "ispend", "scope": "full"}, JWT_SECRET_KEY, JWT_ALGORITHM)
    response = JSONResponse({"login_success": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=f"Bearer {access_token}",
        domain=COOKIE_DOMAIN,
        httponly=True,
        max_age=15*60,
        expires=15*60
    )
    #response.headers["Access-Control-Allow-Origin"] = "http://localhost:8080"
    #response.headers["Access-Control-Allow-Credentials"] = "True"
    response.headers["Access-Control-Allow-Headers"] = "Origin, X-Requested-With, Content-Type, Accept"
    return response

@app.get("/recent", response_model=List[Expense])
async def get_recent_expenses(to_date: date, user_id: str = Depends(verify_jwt)):
    #get list of expenses for specified month
    from_date = to_date.replace(day=1)
    expenses = dataservice.read_expenses(user_id, from_date, to_date)
    return expenses

@app.get("/history", response_model=List[CategoryHistory])
async def get_historic_expenses(to_date: date, months: int, user_id: str = Depends(verify_jwt)):
    #get monthly totals (per category) for the last n months
    from_date = to_date-relativedelta(months=months)
    history = dataservice.read_history(user_id, from_date, to_date)
    return history

@app.get("/monthstats", response_model=MonthlyStats)
async def get_monthly_statistics(to_date: date, top: int, user_id: str = Depends(verify_jwt)):
    # 1) total sum
    # 2) category subtotals
    # 3) top three per category
    from_date = to_date.replace(day=1)
    total, stats = dataservice.read_stats(user_id, from_date, to_date, top)
    return {
        "monthtotal": total,
        "categorystats": stats
    }

@app.post("/add", response_model=Expense)
async def add_new_expense(new_expense: ExpenseData, user_id: str = Depends(verify_jwt)):
    expense = dataservice.add_expense(user_id, new_expense.dict())
    return expense

@app.get("/logout")
async def logout():
    response = JSONResponse({"logout_success": True})
    response.delete_cookie(key=COOKIE_NAME, domain=COOKIE_DOMAIN)
    return response