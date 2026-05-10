"""
app — FastAPI-приложение PC Build Recommender.

Структура:
    main.py          — точка входа, lifespan, регистрация роутеров
    router.py        — эндпоинты: POST /recommend, GET /health, GET /purposes
    schemas.py       — Pydantic-схемы запросов и ответов
    model_service.py — синглтон загрузки модели CatBoost и инференса
"""
