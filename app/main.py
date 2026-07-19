from fastapi import FastAPI

from app.api.payments import router

app = FastAPI()

app.include_router(router=router)


