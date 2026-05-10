"""
tests/test_scoring.py — юнит-тесты формул скоринга сборок.
Запуск: pytest tests/test_scoring.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

# Копируем функцию score_build прямо из 01_generate_builds для изоляции
# (либо вынеси её в отдельный модуль scoring.py — рекомендуется)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "gen", os.path.join(os.path.dirname(__file__), '..', '01_generate_builds.py')
)
gen = importlib.util.load_from_spec = None  # fallback: вставляем score_build напрямую

from compatibility import PURPOSE_PROFILES


def score_build(b, purpose, budget):
    """Копия функции из 01_generate_builds.py для тестирования."""
    profile = PURPOSE_PROFILES[purpose]
    w       = profile['budget_weights']
    total   = b['total_price']
    scores  = {}

    util = total / budget
    if 0.85 <= util <= 1.0:
        scores['budget_score'] = 25.0
    elif util < 0.85:
        scores['budget_score'] = 25.0 * (util / 0.85)
    else:
        scores['budget_score'] = max(0, 25.0 - (util - 1.0) * 100)

    comp_prices = {
        'gpu':     b.get('gpu_price', 0),
        'cpu':     b.get('cpu_price', 0),
        'mb':      b.get('mb_price', 0),
        'ram':     b.get('ram_price', 0),
        'storage': b.get('storage_price', 0),
        'psu':     b.get('psu_price', 0),
        'case':    b.get('case_price', 0),
        'cooler':  b.get('cooler_price', 0),
    }
    penalty = sum(abs(comp_prices.get(k, 0) / max(total, 1) - v)
                  for k, v in w.items())
    scores['balance_score'] = max(0, 30.0 * (1 - penalty * 1.5))

    ps = 0
    if purpose in ('gaming', 'rendering', 'streaming'):
        mem    = b.get('gpu_memory', 0)
        min_vr = profile.get('min_gpu_memory_gb', 8)
        ps += 10 * min(1.0, mem / max(min_vr, 1))
        ps += min(8, 8 * b.get('gpu_boost_clock', 1500) / 2000)
        ram_gb  = b.get('ram_total_gb', 0)
        min_ram = profile.get('min_ram_gb', 16)
        ps += 7 * min(1.0, ram_gb / max(min_ram, 1))
    elif purpose == 'workstation':
        cores   = b.get('cpu_cores', 4)
        min_c   = profile.get('min_cpu_cores', 8)
        ram_gb  = b.get('ram_total_gb', 0)
        min_ram = profile.get('min_ram_gb', 32)
        ps += 12 * min(1.0, cores / max(min_c, 1))
        ps += 8  * min(1.0, ram_gb / max(min_ram, 1))
        ps += min(5, 5 * (1 - max(0, b.get('cpu_tdp', 65) - 125) / 155))
    elif purpose == 'office':
        ps += min(12, 12 * (1 - b.get('cpu_tdp', 65) / 200))
        ps += 8 if b.get('cpu_has_igpu', False) else 0
        ps += 5 if 'SSD' in str(b.get('storage_type', '')) else 0
    scores['purpose_score'] = min(25.0, ps)

    cpu_val = (b.get('cpu_cores', 1) * b.get('cpu_boost_clock', 3.0)) \
              / max(b['cpu_price'], 1)
    gpu_val = (b.get('gpu_boost_clock', 1500) * b.get('gpu_memory', 8)) \
              / max(b.get('gpu_price', 1), 1)
    cv = min(1.0, cpu_val / 0.25)
    gv = min(1.0, gpu_val / 50.0)
    if purpose in ('gaming', 'rendering', 'streaming'):
        scores['value_score'] = 20.0 * (0.35 * cv + 0.65 * gv)
    else:
        scores['value_score'] = 20.0 * (0.70 * cv + 0.30 * gv)

    scores['total_score'] = round(min(100.0, sum(scores.values())), 2)
    return scores


# ── Базовая сборка для тестов ────────────────────────────────

def make_build(overrides=None):
    base = {
        'total_price':   1200.0,
        'cpu_price':     250.0,  'cpu_cores': 8, 'cpu_boost_clock': 4.5, 'cpu_tdp': 65,
        'mb_price':      150.0,
        'ram_price':     100.0,  'ram_total_gb': 32,
        'gpu_price':     450.0,  'gpu_memory': 12, 'gpu_boost_clock': 1800,
        'storage_price':  80.0,  'storage_type': 'SSD',
        'psu_price':      90.0,
        'case_price':     60.0,
        'cooler_price':   20.0,
        'cpu_has_igpu':  False,
    }
    if overrides:
        base.update(overrides)
    return base


# ── Тесты budget_score ───────────────────────────────────────

class TestBudgetScore:
    def test_ideal_utilization_gives_max_score(self):
        build = make_build({'total_price': 1190})
        sc = score_build(build, 'gaming', 1200)
        assert sc['budget_score'] == 25.0

    def test_90_percent_utilization_gives_max(self):
        build = make_build({'total_price': 1080})  # 90% от 1200
        sc = score_build(build, 'gaming', 1200)
        assert sc['budget_score'] == 25.0

    def test_underutilization_reduces_score(self):
        build = make_build({'total_price': 600})   # 50% бюджета
        sc = score_build(build, 'gaming', 1200)
        assert sc['budget_score'] < 25.0

    def test_overshoot_reduces_score(self):
        build = make_build({'total_price': 1500})  # перебор на 25%
        sc = score_build(build, 'gaming', 1200)
        assert sc['budget_score'] < 25.0

    def test_large_overshoot_gives_zero(self):
        build = make_build({'total_price': 2400})  # перебор на 100%
        sc = score_build(build, 'gaming', 1200)
        assert sc['budget_score'] == 0.0


# ── Тесты balance_score ──────────────────────────────────────

class TestBalanceScore:
    def test_perfect_balance_gives_high_score(self):
        # Распределяем строго по профилю gaming
        w = PURPOSE_PROFILES['gaming']['budget_weights']
        total = 1200.0
        build = make_build({
            'total_price':   total,
            'gpu_price':     total * w['gpu'],
            'cpu_price':     total * w['cpu'],
            'mb_price':      total * w['mb'],
            'ram_price':     total * w['ram'],
            'storage_price': total * w['storage'],
            'psu_price':     total * w['psu'],
            'case_price':    total * w['case'],
            'cooler_price':  total * w['cooler'],
        })
        sc = score_build(build, 'gaming', 1200)
        assert sc['balance_score'] > 25.0   # близко к максимуму 30

    def test_all_budget_in_cpu_gives_low_balance(self):
        build = make_build({
            'total_price': 1200.0,
            'cpu_price':   1100.0,
            'gpu_price':   0,
            'mb_price':    50.0,
            'ram_price':   20.0,
            'storage_price': 15.0,
            'psu_price':   10.0,
            'case_price':  5.0,
            'cooler_price':0.0,
        })
        sc = score_build(build, 'gaming', 1200)
        assert sc['balance_score'] < 10.0

    def test_balance_score_never_negative(self):
        build = make_build({'gpu_price': 1199, 'cpu_price': 1})
        sc = score_build(build, 'gaming', 1200)
        assert sc['balance_score'] >= 0.0


# ── Тесты total_score ────────────────────────────────────────

class TestTotalScore:
    def test_sum_of_components(self):
        build = make_build()
        sc = score_build(build, 'gaming', 1200)
        expected = round(min(100.0,
            sc['budget_score'] + sc['balance_score'] +
            sc['purpose_score'] + sc['value_score']
        ), 2)
        assert sc['total_score'] == expected

    def test_total_never_exceeds_100(self):
        # Идеальная сборка — скор не должен быть > 100
        w = PURPOSE_PROFILES['gaming']['budget_weights']
        total = 1200.0
        build = {
            'total_price':   total,
            'gpu_price':     total * w['gpu'],     'gpu_memory': 16,  'gpu_boost_clock': 2500,
            'cpu_price':     total * w['cpu'],     'cpu_cores': 16,   'cpu_boost_clock': 5.5,
            'mb_price':      total * w['mb'],      'cpu_tdp': 65,
            'ram_price':     total * w['ram'],     'ram_total_gb': 64,
            'storage_price': total * w['storage'], 'storage_type': 'SSD',
            'psu_price':     total * w['psu'],
            'case_price':    total * w['case'],
            'cooler_price':  total * w['cooler'],
            'cpu_has_igpu':  False,
        }
        sc = score_build(build, 'gaming', total)
        assert sc['total_score'] <= 100.0

    def test_total_never_negative(self):
        build = make_build({'total_price': 10, 'cpu_price': 10,
                            'gpu_price': 0, 'mb_price': 0})
        sc = score_build(build, 'office', 1200)
        assert sc['total_score'] >= 0.0

    def test_all_purposes_run_without_error(self):
        for purpose in PURPOSE_PROFILES.keys():
            build = make_build()
            sc = score_build(build, purpose, 1200)
            assert 'total_score' in sc
            assert 0 <= sc['total_score'] <= 100
