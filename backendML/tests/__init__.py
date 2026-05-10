"""
tests — тесты проекта PC Build Recommender.

Структура:
    test_compatibility.py — юнит-тесты правил совместимости (сокеты, DDR, БП)
    test_scoring.py       — юнит-тесты формул скоринга сборок
    test_edge_cases.py    — граничные случаи: None, нулевой бюджет, экстремальные числа
    test_api.py           — интеграционные тесты FastAPI (требует обученной модели)

Запуск всех тестов:
    pytest tests/ -v

Запуск только без модели (юнит):
    pytest tests/ -v --ignore=tests/test_api.py
"""
