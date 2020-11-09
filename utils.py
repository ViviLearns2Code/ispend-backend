import pymongo as pm
from bson.objectid import ObjectId
from datetime import timedelta, datetime, date, timezone
from jose import jwt, JWTError
from typing import Optional
from fastapi.openapi.models import OAuthFlows as OAuthFlowsModel
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param
from fastapi.exceptions import HTTPException
from fastapi import Request, status


def generate_jwt(content, secret_key, algorithm, expire_minutes=15):
  expire_delta = timedelta(minutes=expire_minutes)
  to_encode = content.copy()
  expire = datetime.utcnow() + expire_delta
  to_encode.update({"exp": expire})
  encoded_jwt = jwt.encode(to_encode, secret_key, algorithm)
  return encoded_jwt


class OAuth2PasswordBearerCookie(OAuth2):

  def __init__(self, tokenUrl:str, scheme_name:str=None, scopes:dict=None, auto_error:bool=True, cookie_name="ACESS_TOKEN"):
    self.cookie_name = cookie_name
    if not scopes:
        scopes = {}
    flows = OAuthFlowsModel(password={"tokenUrl": tokenUrl, "scopes": scopes})
    super().__init__(flows=flows, scheme_name=scheme_name, auto_error=auto_error)

  async def __call__(self, request: Request) -> Optional[str]:
    authorization = request.cookies.get(self.cookie_name)
    scheme, param = get_authorization_scheme_param(authorization)
    if not authorization or scheme.lower() != "bearer":
        if self.auto_error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            return None

    return param


class DBService:
  def __init__(self, uri):
    self.client = pm.MongoClient(uri)
    self.user_db = self.client["users"]["users"]
    self.spend_db = self.client["ispend"]["spends"]

  def create_new_user(self, google_id, google_name):
    user = self.user_db.insert_one({
      "google": {
        "id": google_id,
        "name": google_name
      }
    })
    return user.inserted_id

  def find_user_by_google_id(self, google_id):
    user = self.user_db.find_one({ "google.id": google_id })
    return user

  def find_user_by_id(self, user_id):
    user = self.user_db.find_one({ "_id": ObjectId(user_id) })
    return user

  def add_user(self, user_obj):
    user = self.user_db.insert_one(user_obj)
    return user

  def add_expense(self, user_id: str, user_expense):
    user_expense["date"] = datetime(user_expense["date"].year, user_expense["date"].month, user_expense["date"].day, 0, 0, 0, tzinfo=timezone.utc)

    new_entry = {
     "userId": user_id,
     "expense": user_expense
    }
    new_id = self.spend_db.insert_one(new_entry).inserted_id
    new_entry = self.spend_db.find_one({"_id": new_id})
    return {"id": str(new_entry["_id"]), **new_entry["expense"]}

  def read_expenses(self, user_id: str, from_date: datetime.date, to_date: datetime.date):
    #convert date to datetime for mongodb query
    from_dt = datetime(from_date.year, from_date.month, from_date.day, 0, 0, 0, tzinfo=timezone.utc)
    to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)
    pipeline = [
    {
      "$match": {
        "userId": user_id,
        "expense.date": { "$gte": from_dt, "$lte": to_dt }
      }
    },{
      "$sort": {
        "expense.date": pm.DESCENDING
      }
    },{
      "$project": {
        "userId": 0
      }
    }]
    month_data = self.spend_db.aggregate(pipeline, allowDiskUse=True)
    return [{"id": str(e["_id"]), **e["expense"]} for e in month_data]

  def read_stats(self, user_id: str, from_date: datetime.date, to_date: datetime.date, top: int):
    total = 0
    category_stats = []
    from_dt = datetime(from_date.year, from_date.month, from_date.day, 0, 0, 0, tzinfo=timezone.utc)
    to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)
    pipeline = [
      {
        "$match": {
          "userId": user_id,
          "expense.date": { "$gte": from_dt, "$lte": to_dt }
        }
      },{
        "$sort": {
          "expense.category": pm.ASCENDING,
          "expense.sum": pm.DESCENDING
        }
      },{
        "$group": {
          "_id": "$expense.category",
          "categorytotal": { "$sum": "$expense.sum" },
          "list": {
            "$push": "$expense" #hopefully this keeps the sort order from above...
          }
        }
      },{
        "$project": {
          "categorytotal": "$categorytotal",
          "list": { "$slice": ["$list",  top] }
        }
      }
    ]
    aggr_cat = self.spend_db.aggregate(pipeline, allowDiskUse=True)
    for c in aggr_cat:
      total += c["categorytotal"]
      category_stats.append({
        "categoryname": c["_id"],
        "total": c["categorytotal"],
        "expenselist": c["list"]
      })
    return total, category_stats


  def read_history(self, user_id: str, from_date: datetime.date, to_date: datetime.date):
    #get all expenses from the last n months grouped by date and category
    from_dt = datetime(from_date.year, from_date.month, from_date.day, 0, 0, 0, tzinfo=timezone.utc)
    to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)
    pipeline = [
      {
        "$match": {
          "userId": user_id,
          "expense.date": { "$gte": from_dt, "$lte": to_dt}
        }
      },{
        "$group": {
          "_id": {
            "category": "$expense.category",
            "date": "$expense.date"
          },
          "total": { "$sum": "$expense.sum" }
        }
      },{
        "$sort": {
          "_id.category": pm.ASCENDING,
          "_id.date": pm.ASCENDING
        }
      }, {
        "$group": {
          "_id": "$_id.category",
          "history": {
            "$push": {
              "date": "$_id.date",
              "total": {
                "$sum": "$total"
              }
            }
          }
        }
      }
    ]
    history = self.spend_db.aggregate(pipeline, allowDiskUse=True)
    return [{
      "categoryname": c["_id"],
      "history": c["history"]
    } for c in history]