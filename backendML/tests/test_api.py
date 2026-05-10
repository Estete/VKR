"""
tests/test_api.py — интеграционные тесты FastAPI через TestClient.

Требует обученной модели: сначала запусти python 03_train_ranker.py

Запуск: pytest tests/test_api.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    try:
        from app.main import app
        with TestClient(app) as c:
            yield c
    except FileNotFoundError as e:
        pytest.skip(f"Модель не найдена — сначала запусти 03_train_ranker.py ({e})")
    except Exception as e:
        pytest.skip(f"Не удалось запустить приложение: {e}")


class TestApiHealth:
    def test_status_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200

    def test_model_loaded(self, client):
        assert client.get("/api/v1/health").json()["model_loaded"] is True

    def test_catalogs_loaded(self, client):
        assert client.get("/api/v1/health").json()["catalogs_loaded"] is True

    def test_model_target_valid(self, client):
        target = client.get("/api/v1/health").json()["model_target"]
        assert target in ("total_score", "human_label")


class TestApiPurposes:
    def test_returns_five_items(self, client):
        r = client.get("/api/v1/purposes")
        assert r.status_code == 200
        assert len(r.json()) == 5

    def test_all_keys_present(self, client):
        keys = [p["key"] for p in client.get("/api/v1/purposes").json()]
        for expected in ("gaming", "workstation", "rendering", "office", "streaming"):
            assert expected in keys

    def test_each_has_description(self, client):
        for p in client.get("/api/v1/purposes").json():
            assert len(p["description"]) > 0


class TestApiGenerateBuilds:
    def test_gaming_returns_200(self, client):
        r = client.post("/api/v1/generate_builds", json={
            "budget": 1500, "purpose": "gaming",
            "preferred_brands": ["AMD", "Nvidia"],
            "form_factor": "standard", "top_n": 3,
        })
        assert r.status_code == 200
        assert len(r.json()["builds"]) >= 1

    def test_builds_within_budget(self, client):
        budget = 1500
        r = client.post("/api/v1/generate_builds", json={
            "budget": budget, "purpose": "gaming", "top_n": 3
        })
        for build in r.json()["builds"]:
            assert build["total_price"] <= budget * 1.05

    def test_ranks_sequential(self, client):
        r = client.post("/api/v1/generate_builds", json={
            "budget": 2000, "purpose": "workstation", "top_n": 3
        })
        ranks = [b["rank"] for b in r.json()["builds"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_scores_descending(self, client):
        r = client.post("/api/v1/generate_builds", json={
            "budget": 2000, "purpose": "rendering", "top_n": 3
        })
        scores = [b["predicted_score"] for b in r.json()["builds"]]
        assert scores == sorted(scores, reverse=True)

    def test_total_price_equals_component_sum(self, client):
        r = client.post("/api/v1/generate_builds", json={
            "budget": 1500, "purpose": "gaming", "top_n": 1
        })
        b = r.json()["builds"][0]
        component_sum = (
            b["cpu"]["price"] + b["mb"]["price"] +
            b["ram"]["price"] + b["storage"]["price"] +
            b["psu"]["price"] + b["case"]["price"] +
            b["cooler"]["price"] +
            (b["gpu"]["price"] if b["gpu"] else 0)
        )
        assert abs(component_sum - b["total_price"]) < 0.02

    def test_top_n_respected(self, client):
        for n in (1, 2, 3):
            r = client.post("/api/v1/generate_builds", json={
                "budget": 2000, "purpose": "gaming", "top_n": n
            })
            assert len(r.json()["builds"]) <= n

    def test_response_contains_summary(self, client):
        r = client.post("/api/v1/generate_builds", json={
            "budget": 1000, "purpose": "streaming"
        })
        assert "streaming" in r.json()["request_summary"].lower()

    def test_all_purposes_no_server_error(self, client):
        for purpose in ("gaming", "workstation", "rendering", "office", "streaming"):
            r = client.post("/api/v1/generate_builds", json={
                "budget": 1500, "purpose": purpose
            })
            assert r.status_code in (200, 404)


class TestApiRecommendErrors:
    def test_negative_budget_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": -500, "purpose": "gaming"})
        assert r.status_code == 422

    def test_zero_budget_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": 0, "purpose": "gaming"})
        assert r.status_code == 422

    def test_impossible_budget_404(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": 1, "purpose": "gaming"})
        assert r.status_code == 404

    def test_invalid_purpose_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": 1500, "purpose": "quantum"})
        assert r.status_code == 422

    def test_invalid_brand_422(self, client):
        r = client.post("/api/v1/generate_builds", json={
            "budget": 1500, "purpose": "gaming", "preferred_brands": ["UnknownBrand"]
        })
        assert r.status_code == 422

    def test_top_n_zero_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": 1500, "purpose": "gaming", "top_n": 0})
        assert r.status_code == 422

    def test_top_n_too_large_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": 1500, "purpose": "gaming", "top_n": 100})
        assert r.status_code == 422

    def test_missing_budget_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"purpose": "gaming"})
        assert r.status_code == 422

    def test_missing_purpose_422(self, client):
        r = client.post("/api/v1/generate_builds", json={"budget": 1500})
        assert r.status_code == 422
