import os
import sys
from dotenv import load_dotenv
# add source dir
# file_dir = os.path.dirname(__file__)
# sys.path.append(file_dir)
BASE_DIR= os.path.dirname(os.path.abspath(__file__))
# add env
load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.path.append(BASE_DIR)

# import jwt
import shutil
import tweepy
import uvicorn
import logging
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request, Response, responses, Depends, status
# from fastapi_sqlalchemy import DBSessionMiddleware, db
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("fans")
app = FastAPI()

origins = ("http://localhost:8000", "http://localhost", "*")

app.add_middleware(CORSMiddleware,
                   allow_origins=origins,
                   allow_methods=['*'],
                   allow_headers=['*'])

# to avoid csrftokenError
# app.add_middleware(DBSessionMiddleware, db_url=os.environ['DATABASE_URL'])

twt_api = None
# twt_login_callback = "http://127.0.0.1:8000/login_callback"
twt_login_callback = "https://fans3-server-46szvdni7q-uc.a.run.app/login_callback"
cookie_key = "fans-cookie"
cookie_cache = {}
user_table = {}
oauth_cache = {}

def get_twt_auth():
    print("secret env", os.environ["CONSUMER_KEY"], os.environ["CONSUMER_SECRET"])
    return tweepy.OAuth1UserHandler(os.environ["CONSUMER_KEY"],
                                    os.environ["CONSUMER_SECRET"],
                                    callback=twt_login_callback)

# -------------- models & sechmas --------------------
class User(BaseModel):
    name: str
    t_id: int
    ak: str = None
    sk: str = None
    address: str = None


class FollowReq(BaseModel):
    address: str = None
    subject: str = None
    subject_tid: str = None # for test
    subject_name:str = None # for test

class UserReq(BaseModel):
    address: str = None

class UserResp(BaseModel):
    address: str = None
    name: str = None
    t_id : int

# ------------------ helper funcs ------------------------

def _get_user(
    req: UserReq,
    # address: str = None
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="subject user not found",
        # headers={"WWW-Authenticate": "Bearer"},
    )
    if req.address:
        for user in user_table:
            if user_table[user].address == req.address:
                return user_table[user]
        return None
    else:
        subject_user = user_table.get(req.subject)
        if subject_user is None:
            raise credentials_exception
        return subject_user


def get_current_user(request: Request):
    cookie = request.cookies.get(cookie_key)
    if cookie is None:
        pass
    user = user_table.get(cookie)
    if user is None:
        pass
    return user

# ------------------ routers & apis ------------------------
# router and models examples
@app.get("/")
async def root():
    return {"message": "hello world"}

@app.get('/login', response_class=responses.RedirectResponse)
async def login(address : str, request: Request):
    cookie = request.cookies.get(cookie_key)
    if cookie and cookie_cache.get(cookie) is not None:
        print("find cookie" , cookie)
        return responses.RedirectResponse("/")
    else:
        twt_auth = get_twt_auth()
        redirect_url = twt_auth.get_authorization_url()
        oauth_tokens = twt_auth.oauth.token.get("oauth_token")
        oauth_cache[oauth_tokens] = twt_auth.request_token
        oauth_cache[oauth_tokens]["address"] = address
        return responses.RedirectResponse(redirect_url)

# called by twitter
# has to be set in twitter setting
@app.get('/login_callback')
async def login_callback(request: Request, response:Response, oauth_token:str = None, oauth_verifier : str = None ):
    print("----------------callback receive----------------")
    print("oauth_token",oauth_token)
    print("oauth_verifier", oauth_verifier)

    twt_auth = get_twt_auth()
    request_token = oauth_cache.get(oauth_token, None)
    if request_token is None:
        return "failed"
    twt_auth.request_token = request_token
    user_address = request_token["address"]
    oauth_cache.pop(oauth_token)

    ak, sk = twt_auth.get_access_token(oauth_verifier)
    api = tweepy.API(twt_auth)
    t_user: tweepy.User = api.verify_credentials()# To run locally
    user = User(name=t_user.screen_name, t_id=t_user.id, ak=ak, sk=sk, address=user_address)
    cookie_cache[user.name] = user
    user_table[user.name] = user
    response.set_cookie(key=cookie_key, value=user.name)

    return f"get authentication of user: {user.name}, {user.t_id}, {user.address}"

@app.get("/users", response_model=list[UserResp])
async def get_users():
    resp = [user_table[user] for user in user_table]
    return resp

@app.get("/user", response_model=UserResp | None)
async def get_user(address:str):
    user = _get_user(UserReq(address=address))
    return user


@app.post("/follow")
async def follow(
    request: Request,
    user: User = Depends(get_current_user),
    subject_user: User = Depends(_get_user)
):
    print(user, subject_user)
    cookie = request.cookies.get(cookie_key)
    if cookie is None or cookie_cache.get(cookie) is None:
        return responses.RedirectResponse("/login")

    user: User = cookie_cache.get(cookie)

    twt_auth = get_twt_auth()
    twt_auth.set_access_token(user.ak, user.sk)
    api = tweepy.API(twt_auth)

    # resp_user = api.create_friendship(screen_name=subject_user.name, subject_user.t_id)
    resp_user = api.create_friendship(screen_name=subject_user.name)
    print(resp_user)


@app.post("/unfollow")
async def unfollow(request: Request, subject: str):
    pass

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)