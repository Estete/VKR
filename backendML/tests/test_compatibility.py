"""
tests/test_compatibility.py — юнит-тесты правил совместимости.
Запуск: pytest tests/test_compatibility.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from compatibility import (
    get_cpu_socket, get_cpu_brand,
    get_ram_generation, get_ram_speed_mhz,
    get_ram_total_gb, get_ram_modules_count,
    ram_socket_compatible, ram_fits_mb,
    case_mb_compatible, psu_sufficient, estimate_gpu_tdp,
)


# ── get_cpu_socket ───────────────────────────────────────────

class TestGetCpuSocket:
    def test_amd_ryzen_5000_am4(self):
        assert get_cpu_socket("AMD Ryzen 5 5600X")  == "AM4"
        assert get_cpu_socket("AMD Ryzen 7 5800X3D") == "AM4"

    def test_amd_ryzen_7000_am5(self):
        assert get_cpu_socket("AMD Ryzen 5 7600X")  == "AM5"
        assert get_cpu_socket("AMD Ryzen 9 7950X")  == "AM5"

    def test_intel_12_13_14_gen_lga1700(self):
        assert get_cpu_socket("Intel Core i5-12400F") == "LGA1700"
        assert get_cpu_socket("Intel Core i7-13700K") == "LGA1700"
        assert get_cpu_socket("Intel Core i9-14900K") == "LGA1700"

    def test_intel_10_11_gen_lga1200(self):
        assert get_cpu_socket("Intel Core i5-10400")  == "LGA1200"
        assert get_cpu_socket("Intel Core i7-11700K") == "LGA1200"

    def test_intel_8_9_gen_lga1151(self):
        assert get_cpu_socket("Intel Core i9-9900K")  == "LGA1151"
        assert get_cpu_socket("Intel Core i7-8700K")  == "LGA1151"

    def test_unknown_returns_unknown(self):
        assert get_cpu_socket("SomeBrand CPU 9999") == "Unknown"

    def test_empty_string(self):
        assert get_cpu_socket("") == "Unknown"


# ── get_cpu_brand ────────────────────────────────────────────

class TestGetCpuBrand:
    def test_amd(self):
        assert get_cpu_brand("AMD Ryzen 5 5600X") == "AMD"

    def test_intel(self):
        assert get_cpu_brand("Intel Core i7-13700K") == "Intel"

    def test_unknown(self):
        assert get_cpu_brand("SomeBrand CPU") == "Unknown"


# ── RAM парсинг ──────────────────────────────────────────────

class TestRamParsing:
    def test_ddr4_generation(self):
        assert get_ram_generation("[4, 3200]") == 4

    def test_ddr5_generation(self):
        assert get_ram_generation("[5, 5600]") == 5

    def test_invalid_generation_defaults_to_4(self):
        assert get_ram_generation("unknown") == 4
        assert get_ram_generation(None) == 4

    def test_speed_mhz(self):
        assert get_ram_speed_mhz("[4, 3200]") == 3200
        assert get_ram_speed_mhz("[5, 5600]") == 5600

    def test_total_gb_two_sticks(self):
        assert get_ram_total_gb("[2, 8]")  == 16   # 2 × 8 = 16 GB
        assert get_ram_total_gb("[2, 16]") == 32   # 2 × 16 = 32 GB
        assert get_ram_total_gb("[4, 8]")  == 32   # 4 × 8 = 32 GB

    def test_modules_count(self):
        assert get_ram_modules_count("[2, 8]")  == 2
        assert get_ram_modules_count("[4, 16]") == 4


# ── Совместимость RAM ↔ Сокет ────────────────────────────────

class TestRamSocketCompatibility:
    def test_am4_requires_ddr4(self):
        assert ram_socket_compatible(4, "AM4") is True
        assert ram_socket_compatible(5, "AM4") is False

    def test_am5_requires_ddr5(self):
        assert ram_socket_compatible(5, "AM5") is True
        assert ram_socket_compatible(4, "AM5") is False

    def test_lga1700_accepts_both(self):
        assert ram_socket_compatible(4, "LGA1700") is True
        assert ram_socket_compatible(5, "LGA1700") is True

    def test_lga1200_requires_ddr4(self):
        assert ram_socket_compatible(4, "LGA1200") is True
        assert ram_socket_compatible(5, "LGA1200") is False

    def test_unknown_socket_passes(self):
        assert ram_socket_compatible(4, "Unknown") is True


# ── Совместимость RAM ↔ MB ───────────────────────────────────

class TestRamFitsMb:
    def test_fits_within_limits(self):
        assert ram_fits_mb(16, 2, 128, 4) is True

    def test_exceeds_max_memory(self):
        assert ram_fits_mb(64, 2, 32, 4) is False

    def test_too_many_modules(self):
        assert ram_fits_mb(16, 4, 128, 2) is False  # 4 планки, 2 слота

    def test_exactly_at_limit(self):
        assert ram_fits_mb(128, 4, 128, 4) is True


# ── Совместимость корпуса ↔ MB ───────────────────────────────

class TestCaseMbCompatibility:
    def test_atx_fits_mid_tower(self):
        assert case_mb_compatible("ATX Mid Tower", "ATX") is True

    def test_eatx_doesnt_fit_mid_tower(self):
        assert case_mb_compatible("ATX Mid Tower", "EATX") is False

    def test_mini_itx_fits_mini_itx_tower(self):
        assert case_mb_compatible("Mini ITX Tower", "Mini ITX") is True

    def test_atx_doesnt_fit_mini_itx_tower(self):
        assert case_mb_compatible("Mini ITX Tower", "ATX") is False

    def test_full_tower_fits_all(self):
        for ff in ["ATX", "Micro ATX", "Mini ITX", "EATX"]:
            assert case_mb_compatible("ATX Full Tower", ff) is True

    def test_unknown_case_type(self):
        assert case_mb_compatible("Unknown Case", "ATX") is False


# ── Мощность БП ──────────────────────────────────────────────

class TestPsuSufficient:
    def test_sufficient_for_low_end(self):
        # CPU 65W TDP + GPU $200 (~120W) → ~231W нужно, 400W хватит
        assert psu_sufficient(400, 65, 200) is True

    def test_insufficient_for_high_end(self):
        # CPU 125W + GPU $900 (~350W) → ~594W нужно, 400W не хватит
        assert psu_sufficient(400, 125, 900) is False

    def test_boundary_with_margin(self):
        cpu_tdp   = 65
        gpu_price = 200
        gpu_tdp   = estimate_gpu_tdp(gpu_price)   # 120
        required  = (cpu_tdp + gpu_tdp) * 1.25 + 50  # 281.25
        # БП ровно на границе — должен пройти
        assert psu_sufficient(int(required) + 1, cpu_tdp, gpu_price) is True
        # Чуть меньше — не должен
        assert psu_sufficient(int(required) - 1, cpu_tdp, gpu_price) is False

    def test_zero_gpu_price(self):
        # Офисный ПК без дискретной GPU
        assert psu_sufficient(300, 65, 0) is True
