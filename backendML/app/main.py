"""
app/main.py — точка входа FastAPI-приложения.

Запуск:
    uvicorn app.main:app --reload --port 8000

Swagger UI: http://localhost:8000/docs
"""
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.router import router
from app.model_service import model_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        model_service.load()
    except FileNotFoundError as e:
        print(f"[WARN] Модель не найдена: {e}", file=sys.stderr)
        print("[WARN] Сервис запущен в деградированном режиме.", file=sys.stderr)
    except ConnectionError as e:
        print(f"[WARN] БД недоступна при старте: {e}", file=sys.stderr)
        print("[WARN] Сервис запущен в деградированном режиме.", file=sys.stderr)
    yield


app = FastAPI(
    title       = "PC Build Recommender API",
    description = "ML-бэкенд: генерация, ранжирование и поиск компонентов для LLM function calling",
    version     = "2.0.0",
    lifespan    = lifespan,
)

app.include_router(router, prefix="/api/v1")
