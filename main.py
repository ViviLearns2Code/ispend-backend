import os
from dateutil.relativedelta import relativedelta
from datetime import date
from typing import List
from fastapi import FastAPI
from enum import Enum
from pydantic import BaseModel
from google.oauth2 import id_token
from google.auth.transport import requests
from utils import DBService, generate_jwt
from config import GOOGLE, MONGO, JWT

class CategoryName(str, Enum):
    car = "Car"
    insurance = "Insurance"
    food = "Food"
    hobbies = "Hobbies"
    home = "Home"
    other = "Other"

class Expense(BaseModel):
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

MONGODB_URI = os.environ.get("MONGO_URI", MONGO["uri"])
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", GOOGLE["clientID"])
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", JWT["secret"])
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", JWT["algorithm"])
JWT_EXPIRE = os.environ.get("JWT_EXPIRE", 15)
dataservice = DBService(MONGODB_URI)
app = FastAPI(title="iSpend", description="Backend powered by FastAPI", version="0.0.1")


#oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.get("/")
async def ready():
    return {"message": "Hello World"}

@app.post("/login")
async def authenticate(token: str):
    #1. validate google's id_token
    try:
        google_id_info = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        google_id = google_id_info["sub"]
        google_name = google_id_info["name"]
    except ValueError:
        raise Exception("Invalid token") #TODO
    #2. look up user id in db
    user = dataservice.find_user_by_google_id(google_id)
    if user is None:
        user = dataservice.create_new_user(google_id, google_name)
    user["id"] = str(user.pop("_id"))
    print(user)
    #3. return jwt token
    access_token = generate_jwt(user, JWT_SECRET_KEY, JWT_ALGORITHM)
    # TODO: Cookie?
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/recent", response_model=List[Expense])
async def get_recent_expenses(user_id: str, to_date: date):
    #get list of expenses for specified month
    from_date = to_date.replace(day=1)
    expenses = dataservice.read_expenses(user_id, from_date, to_date)
    return expenses

@app.post("/history", response_model=List[CategoryHistory])
async def get_historic_expenses(user_id: str, to_date: date, months: int):
    #get monthly totals (per category) for the last n months
    from_date = to_date-relativedelta(months=months)
    history = dataservice.read_history(user_id, from_date, to_date)
    return history

@app.post("/monthstats", response_model=MonthlyStats)
async def get_monthly_statistics(user_id: str, to_date: date, top: int):
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
async def add_new_expense(user_id: str, new_expense: Expense):
    expense = dataservice.add_expense(user_id, new_expense.dict())
    return expense

@app.get("/logout")
async def logout():
	# do something to jwt cookie session
    pass