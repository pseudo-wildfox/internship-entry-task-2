from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()  # Создание приложения

# Модель для заметки (валидация данных)
class Note(BaseModel):
    title: str
    content: str

# Маршрут для главной страницы
@app.get("/")
def read_root():
    return {"message": "Welcome to Notes API!"}

# Маршрут для создания заметки
@app.post("/notes")
def create_note(note: Note):
    return {"note": note.dict(), "status": "created"}