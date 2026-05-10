"""
tests/test_edge_cases.py — граничные случаи: None-значения,
нулевой бюджет, экстремальные числа, невалидные форматы.

Запуск: pytest tests/test_edge_cases.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from compatibility import (
    get_cpu_socket, get_ram_generation, get_ram_total_gb,
    psu_sufficient, estimate_gpu_tdp, case_mb_compatible,
)


class TestNoneAndMissingValues:
    def test_cpu_socket_none(self):
        assert get_cpu_socket(None) == "Unknown"

    def test_cpu_socket_empty_string(self):
        assert get_cpu_socket("") == "Unknown"

    def test_ram_generation_none(self):
        assert get_ram_generation(None) == 4

    def test_ram_generation_empty_string(self):
        assert get_ram_generation("") == 4

    def test_ram_total_gb_none(self):
        assert get_ram_total_gb(None) == 0

    def test_ram_total_gb_empty_string(self):
        assert get_ram_total_gb("") == 0

    def test_psu_zero_gpu_price_no_crash(self):
        result = psu_sufficient(300, 65, 0)
        assert isinstance(result, bool)

    def test_estimate_gpu_tdp_zero_price(self):
        assert estimate_gpu_tdp(0) == 75


class TestExtremeValues:
    def test_very_large_budget_score_capped(self):
        # total_price намного меньше бюджета — score не уходит < 0
        from tests.test_scoring import score_build, make_build
        build = make_build({"total_price": 100.0})
        sc = score_build(build, "gaming", 100_000)
        assert 0 <= sc["total_score"] <= 100

    def test_very_high_cpu_tdp_no_crash(self):
        from tests.test_scoring import score_build, make_build
        build = make_build({"cpu_tdp": 280})
        sc = score_build(build, "workstation", 1200)
        assert 0 <= sc["total_score"] <= 100

    def test_gpu_price_extreme_tdp_capped(self):
        assert estimate_gpu_tdp(50_000) == 350

    def test_psu_1600w_covers_anything(self):
        assert psu_sufficient(1600, 280, 10_000) is True

    def test_ram_huge_modules_no_crash(self):
        assert get_ram_total_gb("[8, 64]") == 512


class TestInvalidFormats:
    def test_ram_malformed_range_total_gb(self):
        assert get_ram_total_gb("[broken]") == 0

    def test_ram_malformed_range_generation(self):
        assert get_ram_generation("[broken]") == 4

    def test_cpu_socket_numeric_string(self):
        assert get_cpu_socket("12345") == "Unknown"

    def test_case_mb_unknown_case_type(self):
        assert case_mb_compatible("Nonexistent Case Type", "ATX") is False


class TestSchemaValidation:
    """Тесты Pydantic-схем без запуска сервера."""

    def test_valid_request_passes(self):
        from app.schemas import GenerateBuildsRequest
        req = GenerateBuildsRequest(
            budget=1500,
            purpose="gaming",
            preferred_brands=["AMD"],
            form_factor="standard",
        )
        assert req.budget == 1500
        assert req.form_factor == "standard"

    def test_negative_budget_raises(self):
        from app.schemas import GenerateBuildsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateBuildsRequest(budget=-100, purpose="gaming")

    def test_zero_budget_raises(self):
        from app.schemas import GenerateBuildsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateBuildsRequest(budget=0, purpose="gaming")

    def test_invalid_purpose_raises(self):
        from app.schemas import GenerateBuildsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateBuildsRequest(budget=1000, purpose="supercomputer")

    def test_invalid_brand_raises(self):
        from app.schemas import GenerateBuildsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateBuildsRequest(budget=1000, purpose="gaming",
                             preferred_brands=["UnknownBrand"])

    def test_top_n_zero_raises(self):
        from app.schemas import GenerateBuildsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateBuildsRequest(budget=1000, purpose="gaming", top_n=0)

    def test_top_n_over_limit_raises(self):
        from app.schemas import GenerateBuildsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateBuildsRequest(budget=1000, purpose="gaming", top_n=11)
