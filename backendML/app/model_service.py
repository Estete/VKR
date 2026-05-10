"""
app/model_service.py — синглтон для загрузки модели и инференса.
Загружается один раз при старте приложения.

Флоу generate_builds():
  1. BuildGenerator.generate() — 200 кандидатов из БД
  2. validate_build() — фильтрация несовместимых
  3. CatBoost.predict() — ранжирование
  4. Возврат top-N
"""
import json
import os
import sys
import pandas as pd
from catboost import CatBoostRegressor, Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from compatibility import validate_build
from build_generator import BuildGenerator

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models')

N_CANDIDATES = 75

# Маппинг: категория → {колонка каталога → колонка feature-вектора}
_CAT_TO_ROW: dict[str, dict[str, str]] = {
    'cpu': {
        'name': 'cpu_name', 'price': 'cpu_price',
        'core_count': 'cpu_cores', 'core_clock': 'cpu_base_clock',
        'boost_clock': 'cpu_boost_clock', 'tdp': 'cpu_tdp', 'smt': 'cpu_smt',
    },
    'motherboard': {
        'name': 'mb_name', 'price': 'mb_price',
        'socket': 'mb_socket', 'form_factor': 'mb_form_factor',
        'max_memory_gb': 'mb_max_memory', 'memory_slots': 'mb_memory_slots',
    },
    'memory': {
        'name': 'ram_name', 'price': 'ram_price',
        'speed_generation': 'ram_ddr_gen', 'speed_mhz': 'ram_speed_mhz',
        'modules_count': 'ram_modules_count', 'cas_latency': 'ram_cas_latency',
    },
    'video_card': {
        'name': 'gpu_name', 'price': 'gpu_price',
        'memory_gb': 'gpu_memory', 'chipset': 'gpu_chipset',
        'boost_clock': 'gpu_boost_clock', 'length_mm': 'gpu_length',
    },
    'storage': {
        'name': 'storage_name', 'price': 'storage_price',
        'type': 'storage_type', 'capacity_gb': 'storage_capacity',
        'interface': 'storage_interface', 'price_per_gb': 'storage_price_per_gb',
    },
    'power_supply': {
        'name': 'psu_name', 'price': 'psu_price',
        'wattage': 'psu_wattage', 'efficiency': 'psu_efficiency',
        'modular': 'psu_modular',
    },
    'case': {
        'name': 'case_name', 'price': 'case_price', 'type': 'case_type',
    },
    'cooler': {
        'name': 'cooler_name', 'price': 'cooler_price', 'size_mm': 'cooler_size',
    },
}


class ModelService:
    def __init__(self):
        self.model:         CatBoostRegressor | None = None
        self.feature_names: list[str] = []
        self.cat_indices:   list[int] = []
        self.model_target:  str = ''
        self.generator:     BuildGenerator = BuildGenerator()
        self._loaded        = False

    def load(self):
        """Загружает модель CatBoost и каталоги компонентов из БД. Вызывается один раз при старте."""
        model_path = os.path.join(MODELS_DIR, 'build_ranker.cbm')
        meta_path  = os.path.join(MODELS_DIR, 'model_meta.json')

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Модель не найдена: {model_path}\n"
                "Сначала запусти: python 03_train_ranker.py"
            )

        self.model = CatBoostRegressor()
        self.model.load_model(model_path)

        with open(meta_path) as f:
            meta = json.load(f)
        self.feature_names = meta['feature_names']
        self.cat_indices   = meta['cat_indices']
        self.model_target  = meta.get('target', 'total_score')

        print("Загрузка каталогов из БД...")
        self.generator.load()

        self._loaded = True
        print("[OK] Сервис готов.")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def catalogs_loaded(self) -> bool:
        return self.generator._loaded

    def generate_builds(self, request: dict, top_n: int = 3) -> pd.DataFrame:
        """
        Генерирует и ранжирует сборки под запрос пользователя.

        request = {
            'budget': 1500,
            'purpose': 'gaming',
            'preferred_brands': ['AMD', 'Nvidia'],
            'form_factor': 'standard',
            'min_ram_gb': 16,          # опционально
            'prefer_ssd': True,        # опционально
        }

        Возвращает DataFrame с top_n лучшими сборками (колонка predicted_score).
        Пустой DataFrame — если не удалось найти совместимых кандидатов.
        """
        if not self._loaded:
            raise RuntimeError("Сервис не загружен. Вызови load() при старте.")

        budget  = float(request['budget'])
        purpose = request['purpose']
        brands  = list(request.get('preferred_brands', []))
        ff      = request.get('form_factor', 'any')

        # ── 1. Генерация кандидатов ──────────────────────────
        candidates = self.generator.generate(
            budget           = budget,
            purpose          = purpose,
            form_factor      = ff,
            preferred_brands = brands,
            n                = N_CANDIDATES,
        )

        if candidates.empty:
            return pd.DataFrame()

        # ── 2. Фильтрация несовместимых ──────────────────────
        compat_mask = candidates.apply(lambda row: validate_build(row)[0], axis=1)
        candidates  = candidates[compat_mask].reset_index(drop=True)

        if candidates.empty:
            return pd.DataFrame()

        # ── 2а. Пост-фильтры по доп. требованиям ────────────
        min_ram = request.get('min_ram_gb')
        if min_ram:
            mask = candidates['ram_total_gb'] >= min_ram
            if mask.sum() >= top_n:
                candidates = candidates[mask].reset_index(drop=True)

        if request.get('prefer_ssd'):
            mask = candidates['storage_type'].str.upper().str.contains('SSD', na=False)
            if mask.sum() >= top_n:
                candidates = candidates[mask].reset_index(drop=True)

        if candidates.empty:
            return pd.DataFrame()

        # ── 3. Предсказание скора CatBoost ───────────────────
        for col in self.feature_names:
            if col not in candidates.columns:
                candidates[col] = 0

        X        = candidates[self.feature_names].copy()
        cat_cols = [self.feature_names[i] for i in self.cat_indices]
        for col in cat_cols:
            X[col] = X[col].fillna('Unknown').astype(str)
        for col in [c for c in self.feature_names if c not in cat_cols]:
            X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)

        candidates = candidates.copy()
        candidates['predicted_score'] = self.model.predict(
            Pool(X, cat_features=self.cat_indices)
        )

        return candidates.nlargest(top_n, 'predicted_score').reset_index(drop=True)

    def score_candidates(
        self,
        build_context: dict,
        category: str,
        candidates: list[dict],
        top_n: int = 3,
    ) -> list[dict]:
        """
        Ранжирует кандидатов на замену компонента в контексте существующей сборки.

        build_context : плоский dict с feature-полями сборки (из _flatten_build_context)
        category      : заменяемая категория ('cpu', 'video_card', ...)
        candidates    : список dict из search_components
        """
        if not self._loaded:
            raise RuntimeError("Сервис не загружен.")
        if not candidates:
            return []

        field_map    = _CAT_TO_ROW.get(category, {})
        cat_features = {self.feature_names[i] for i in self.cat_indices}

        # Базовая строка: дефолты, затем заполняем из build_context
        base: dict = {
            f: ('Unknown' if f in cat_features else 0)
            for f in self.feature_names
        }
        for feat, val in build_context.items():
            if feat in base:
                base[feat] = val

        # Исходная цена заменяемого компонента (для пересчёта total_price)
        price_row_col = field_map.get('price', '')
        orig_price = float(build_context.get(price_row_col, 0) or 0)
        orig_total  = float(build_context.get('total_price', 0) or 0)
        budget      = float(build_context.get('budget', 1) or 1)

        rows: list[dict] = []
        for cand in candidates:
            row = dict(base)
            # Применяем поля кандидата
            for cat_col, row_col in field_map.items():
                if cat_col in cand and row_col in row:
                    row[row_col] = cand[cat_col]
            # RAM: вычисляем total_gb
            if category == 'memory':
                mc = int(cand.get('modules_count', 0) or 0)
                ms = int(cand.get('module_size_gb', 0) or 0)
                if mc and ms and 'ram_total_gb' in row:
                    row['ram_total_gb'] = mc * ms
            # Пересчёт total_price / budget_utilization
            cand_price = float(cand.get('price', 0) or 0)
            new_total  = orig_total - orig_price + cand_price
            if 'total_price'        in row: row['total_price']        = new_total
            if 'budget_utilization' in row: row['budget_utilization'] = new_total / budget
            rows.append(row)

        X = pd.DataFrame(rows)[self.feature_names].copy()
        for col in cat_features:
            X[col] = X[col].fillna('Unknown').astype(str)
        for col in [c for c in self.feature_names if c not in cat_features]:
            X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)

        scores = self.model.predict(Pool(X, cat_features=self.cat_indices))

        result = [{**c, 'predicted_score': float(s)} for c, s in zip(candidates, scores)]
        result.sort(key=lambda x: x['predicted_score'], reverse=True)
        return result[:top_n]


# Синглтон — один объект на всё приложение
model_service = ModelService()
