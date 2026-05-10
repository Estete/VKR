"""
03_train_ranker.py

Обучает CatBoost на размеченных/авто-скорированных сборках.
Режимы:
  - Авто-скор (по умолчанию)        — не нужна ручная разметка
  - Ручная разметка + обучение       — флаг --label (показывает сборки для оценки,
                                        фильтрует несовместимые, затем обучает)
  - Только обучение на готовых метках — флаг --use-human (если метки уже есть)

Запуск:
    python 03_train_ranker.py                      # авто-скор
    python 03_train_ranker.py --label              # разметить вручную, затем обучить
    python 03_train_ranker.py --label --n 30       # разметить 30 сборок
    python 03_train_ranker.py --label --purpose gaming
    python 03_train_ranker.py --use-human          # обучить на уже имеющихся метках
    python 03_train_ranker.py --demo               # демо-рекомендации после обучения
"""

import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compatibility import validate_build
from build_generator import BuildGenerator

try:
    from catboost import CatBoostRegressor, Pool
    CATBOOST_AVAILABLE = True
except ImportError:
    CATBOOST_AVAILABLE = False
    print("[!] CatBoost не установлен. Установи: pip install catboost")

DATA_DIR  = './processed'
MODEL_DIR = './models'
os.makedirs(MODEL_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# РАЗМЕТКА ВРУЧНУЮ
# ─────────────────────────────────────────────────────────────

def _fmt(p):
    return f"${p:>8.2f}"

def _display_build(row, idx, total):
    print("\n" + "═" * 65)
    print(f"  Сборка #{idx + 1}/{total}  |  {str(row['purpose']).upper()}")
    print(f"  Бюджет: ${row['budget']:.0f}  |  "
          f"Итого: ${row['total_price']:.2f} ({row['budget_utilization'] * 100:.1f}%)")
    print(f"  Форм-фактор: {row['form_factor']}  |  "
          f"Бренды: {row.get('preferred_brands', '') or 'любые'}")
    print("─" * 65)

    components = [
        ("CPU",     row['cpu_name'],     row['cpu_price'],
         f"{row['cpu_cores']}C/{row['cpu_boost_clock']}GHz "
         f"TDP={row['cpu_tdp']}W socket={row['cpu_socket']}"),
        ("MB",      row['mb_name'],      row['mb_price'],
         f"{row['mb_socket']} {row['mb_form_factor']} "
         f"max={row['mb_max_memory']}GB/{row['mb_memory_slots']}слот"),
        ("RAM",     row['ram_name'],     row['ram_price'],
         f"DDR{row['ram_ddr_gen']} {row['ram_speed_mhz']}MHz "
         f"{row['ram_total_gb']}GB ({row['ram_modules_count']}x) "
         f"CL{row.get('ram_cas_latency', '')}"),
        ("GPU",     row['gpu_name'],     row['gpu_price'],
         f"{row['gpu_memory']}GB {row['gpu_boost_clock']}MHz"
         if row['gpu_price'] > 0 else "нет"),
        ("Storage", row['storage_name'], row['storage_price'],
         f"{row['storage_type']} {row['storage_capacity']}GB {row['storage_interface']}"),
        ("PSU",     row['psu_name'],     row['psu_price'],
         f"{row['psu_wattage']}W {row['psu_efficiency']} {row['psu_modular']}"),
        ("Корпус",  row['case_name'],    row['case_price'],  row['case_type']),
        ("Кулер",   row['cooler_name'],  row['cooler_price'],f"{row['cooler_size']}мм"),
    ]
    for lbl, name, price, detail in components:
        if price > 0:
            print(f"  {lbl:<8} {_fmt(price)}  "
                  f"{str(name)[:38]:<38}  {str(detail)[:30]}")

    print("─" * 65)
    print(f"  Авто-скор: {row['total_score']:.1f}/100  "
          f"(бюджет:{row['budget_score']:.0f} баланс:{row['balance_score']:.0f} "
          f"назначение:{row['purpose_score']:.0f} ценность:{row['value_score']:.0f})")

    ok, issues = validate_build(row)
    if not ok:
        print(f"  [!] ПРОБЛЕМЫ СОВМЕСТИМОСТИ ({len(issues)}):")
        for issue in issues:
            print(f"      - {issue}")
    else:
        print(f"  [+] Совместимость: OK")

    if pd.notna(row.get('human_label')):
        print(f"  [*] Уже размечено: {row['human_label']}")
    print("═" * 65)


def _get_label():
    print("\n  1=Плохая  2=Слабая  3=Средняя  4=Хорошая  5=Отличная")
    print("  s=Пропустить  q=Сохранить и выйти к обучению")
    while True:
        inp = input("  Оценка: ").strip().lower()
        if inp == 'q':
            return None, 'quit'
        if inp == 's':
            return None, 'skip'
        if inp in ('1', '2', '3', '4', '5'):
            comment = input("  Комментарий (Enter=пропустить): ").strip()
            return int(inp), comment
        print("  Введи 1-5, s или q")


def run_labeling(n: int = 20, purpose: str = None,
                 only_unlabeled: bool = True, min_score: float = 0.0) -> int:
    """
    Интерактивная разметка сборок с фильтром совместимости.
    Возвращает количество проставленных меток за сессию.
    """
    from app.db import load_synthetic_builds, update_synthetic_build_label
    df = load_synthetic_builds()
    if df.empty:
        print("[Разметка] Нет данных. Сначала запусти: python 01_generate_builds.py")
        return 0
    subset = df.copy()

    if purpose:
        subset = subset[subset['purpose'] == purpose]
    if only_unlabeled:
        subset = subset[subset['human_label'].isna()]
    if min_score > 0:
        subset = subset[subset['total_score'] >= min_score]

    subset = subset.sort_values('total_score', ascending=False)

    # ── Фильтр совместимости ────────────────────────────────
    before = len(subset)
    compat_mask = subset.apply(lambda row: validate_build(row)[0], axis=1)
    n_incompat = (~compat_mask).sum()
    subset = subset[compat_mask]

    print(f"\n[Разметка] Доступно: {len(subset)} совместимых сборок "
          f"(отфильтровано несовместимых: {n_incompat} из {before})")

    if subset.empty:
        print("[Разметка] Нет подходящих сборок для разметки.")
        return 0

    labeled_now = 0
    total = min(n, len(subset))
    print(f"[Разметка] Будет показано: {total}")

    for i, (df_idx, row) in enumerate(subset.head(total).iterrows()):
        _display_build(row, i, total)
        label, action = _get_label()
        if action == 'quit':
            break
        if action == 'skip':
            continue
        update_synthetic_build_label(int(row['id']), label, action)
        labeled_now += 1
        print(f"  Сохранено в БД (размечено сейчас: {labeled_now})")

    print(f"\n[Разметка] Размечено за сессию: {labeled_now}/{min(n, len(subset))}")
    return labeled_now


# ─────────────────────────────────────────────────────────────
# ПОДГОТОВКА ДАННЫХ
# ─────────────────────────────────────────────────────────────

def prepare_data(use_human_labels: bool = False):
    from app.db import load_synthetic_builds
    df = load_synthetic_builds()
    if df.empty:
        print("[ERR] Нет данных в synthetic_builds. Сначала запусти: python 01_generate_builds.py")
        sys.exit(1)

    with open(f'{DATA_DIR}/feature_info.json') as f:
        fi = json.load(f)

    # Выбираем target
    if use_human_labels:
        labeled = df[df['human_label'].notna()].copy()
        if len(labeled) < 10:
            print(f"[ERR] Недостаточно ручных меток: {len(labeled)}. Нужно минимум 10.")
            sys.exit(1)
        print(f"Используем ручную разметку: {len(labeled)} примеров")
        df = labeled
        target_col = 'human_label'
    else:
        print(f"Используем авто-скор: {len(df)} примеров")
        target_col = 'total_score'

    # Признаки
    drop = fi['drop_features'] + ['id', 'created_at', 'total_score',
                                    'budget_score', 'balance_score',
                                    'purpose_score', 'value_score',
                                    'human_label', 'human_comment']
    feature_cols = [c for c in df.columns if c not in drop and c != target_col]

    X = df[feature_cols].copy()
    y = df[target_col].astype(float)

    # Приводим категориальные к строкам
    cat_features_present = [c for c in fi['cat_features'] if c in X.columns]
    for col in cat_features_present:
        X[col] = X[col].fillna('Unknown').astype(str)

    # Числовые — заполняем NaN медианой
    num_features_present = [c for c in feature_cols if c not in cat_features_present]
    for col in num_features_present:
        X[col] = pd.to_numeric(X[col], errors='coerce').fillna(X[col].median())

    cat_indices = [list(X.columns).index(c) for c in cat_features_present]

    return X, y, cat_indices, cat_features_present, target_col


# ─────────────────────────────────────────────────────────────
# ОБУЧЕНИЕ
# ─────────────────────────────────────────────────────────────

def train(X, y, cat_indices, target_col):
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.15, random_state=42
    )

    train_pool = Pool(X_train, y_train, cat_features=cat_indices)
    val_pool   = Pool(X_val,   y_val,   cat_features=cat_indices)

    model = CatBoostRegressor(
        iterations            = 1000,
        learning_rate         = 0.05,
        depth                 = 6,
        l2_leaf_reg           = 2,
        loss_function         = 'RMSE',
        eval_metric           = 'RMSE',
        random_seed           = 42,
        verbose               = 25,
        early_stopping_rounds = 50,
        task_type             = 'CPU',
    )

    print(f"\nОбучение CatBoost на {len(X_train)} примерах (val: {len(X_val)})...")
    model.fit(train_pool, eval_set=val_pool)

    # Feature importance
    fi = pd.Series(
        model.get_feature_importance(),
        index=X.columns
    ).sort_values(ascending=False)

    print(f"\nТоп-10 важных признаков:")
    print(fi.head(10).round(2).to_string())

    # Сохраняем модель и список признаков
    model_path = f'{MODEL_DIR}/build_ranker.cbm'
    model.save_model(model_path)

    meta = {
        'feature_names': list(X.columns),
        'cat_indices':   cat_indices,
        'target':        target_col,
        'best_score':    model.get_best_score(),
    }
    with open(f'{MODEL_DIR}/model_meta.json', 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"\n[OK] Модель сохранена: {model_path}")
    print(f"   Best RMSE: {model.get_best_score()}")

    _plot_learning_curve(model, MODEL_DIR)

    return model


def _plot_learning_curve(model, model_dir):
    history = model.evals_result_
    learn_rmse = history.get('learn',      {}).get('RMSE', [])
    val_rmse   = history.get('validation', {}).get('RMSE', [])

    if not learn_rmse:
        return

    iterations = range(len(learn_rmse))
    best_iter  = model.get_best_iteration()

    plt.figure(figsize=(10, 5))
    plt.plot(iterations, learn_rmse, label='Train RMSE', linewidth=1.5)
    if val_rmse:
        plt.plot(iterations, val_rmse, label='Val RMSE',   linewidth=1.5)
    if best_iter is not None:
        plt.axvline(best_iter, color='red', linestyle='--', linewidth=1,
                    label=f'Best iteration ({best_iter})')

    plt.xlabel('Iteration')
    plt.ylabel('RMSE')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    out = f'{model_dir}/learning_curve.png'
    plt.savefig(out, dpi=150)
    print(f"[OK] График сохранён: {out}")

    try:
        plt.show()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# ИНФЕРЕНС: РЕКОМЕНДАЦИЯ СБОРОК
# ─────────────────────────────────────────────────────────────

def recommend_builds(
    model,
    feature_names: list[str],
    cat_indices:   list[int],
    user_request:  dict,
    generator:     'BuildGenerator',
    top_n:         int = 3,
    n_candidates:  int = 200,
) -> pd.DataFrame:
    """
    Генерирует n_candidates сборок под запрос пользователя в рантайме,
    ранжирует их моделью и возвращает top_n лучших.

    user_request пример:
        {
            'budget': 1500,
            'purpose': 'gaming',
            'preferred_brands': ['AMD', 'Nvidia'],
            'form_factor': 'standard',
        }

    generator : экземпляр BuildGenerator (загружен один раз при старте приложения)
    """
    budget  = user_request['budget']
    purpose = user_request['purpose']
    brands  = user_request.get('preferred_brands', [])
    ff      = user_request.get('form_factor', 'any')

    # ── Генерируем кандидатов под конкретный запрос ──────────
    candidates = generator.generate(
        budget           = budget,
        purpose          = purpose,
        form_factor      = ff,
        preferred_brands = brands,
        n                = n_candidates,
    )

    if candidates.empty:
        print("Нет подходящих сборок. Попробуйте увеличить бюджет.")
        return pd.DataFrame()

    # ── Модель предсказывает скор для каждого кандидата ─────
    # Добираем колонки, которых может не быть в сгенерированном DataFrame
    for col in feature_names:
        if col not in candidates.columns:
            candidates[col] = 0

    X_candidates = candidates[feature_names].copy()
    cat_cols = [feature_names[i] for i in cat_indices]
    for col in cat_cols:
        X_candidates[col] = X_candidates[col].fillna('Unknown').astype(str)
    num_cols = [c for c in feature_names if c not in cat_cols]
    for col in num_cols:
        X_candidates[col] = pd.to_numeric(X_candidates[col], errors='coerce').fillna(0)

    pool = Pool(X_candidates, cat_features=cat_indices)
    candidates = candidates.copy()
    candidates['predicted_score'] = model.predict(pool)

    # ── Топ-N по предсказанному скору ────────────────────────
    return candidates.nlargest(top_n, 'predicted_score')


def print_recommendations(top: pd.DataFrame, user_request: dict):
    """Красиво печатает рекомендованные сборки."""
    print(f"\n{'═'*65}")
    print(f"  Рекомендации для запроса:")
    print(f"  Назначение: {user_request['purpose'].upper()}")
    print(f"  Бюджет: ${user_request['budget']}")
    print(f"  Бренды: {', '.join(user_request.get('preferred_brands', [])) or 'любые'}")
    print(f"  Форм-фактор: {user_request.get('form_factor', 'any')}")
    print(f"{'═'*65}")

    for rank, (_, row) in enumerate(top.iterrows(), 1):
        print(f"\n  #{rank}  Скор: {row['predicted_score']:.1f}  |  "
              f"Цена: ${row['total_price']:.2f} ({row['budget_utilization']*100:.0f}% бюджета)")
        print(f"  ─────────────────────────────────────────────────────────")
        print(f"  CPU:    {row['cpu_name'][:50]}  ${row['cpu_price']:.2f}")
        print(f"  MB:     {row['mb_name'][:50]}  ${row['mb_price']:.2f}")
        if row['gpu_price'] > 0:
            print(f"  GPU:    {row['gpu_name'][:50]}  ${row['gpu_price']:.2f}")
        print(f"  PSU:    {row['psu_name'][:50]}  ${row['psu_price']:.2f}")
        print(f"  Корпус: {row['case_name'][:50]}  ${row['case_price']:.2f}")
        print(f"  Кулер:  {row['cooler_name'][:50]}  ${row['cooler_price']:.2f}")


# ─────────────────────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--use-human', action='store_true',
                        help='Обучить на ручных метках (если уже есть)')
    parser.add_argument('--label', action='store_true',
                        help='Сначала разметить сборки вручную, затем обучить')
    parser.add_argument('--n', default=20, type=int,
                        help='Сколько сборок показать при разметке (по умолч. 20)')
    parser.add_argument('--purpose', default=None,
                        help='Фильтр по назначению при разметке (gaming/office/…)')
    parser.add_argument('--demo', action='store_true',
                        help='Запустить демонстрационные рекомендации после обучения')
    args = parser.parse_args()

    if not CATBOOST_AVAILABLE:
        sys.exit(1)

    # ── Фаза разметки ────────────────────────────────────────
    if args.label:
        labeled = run_labeling(
            n             = args.n,
            purpose       = args.purpose,
            only_unlabeled= True,
            min_score     = 0.0,
        )
        if labeled == 0:
            print("\nНет новых меток — обучение пропущено.")
            sys.exit(0)

    # ── Решаем, на чём обучать ───────────────────────────────
    use_human = args.use_human or args.label
    X, y, cat_indices, cat_cols, target_col = prepare_data(use_human)
    model = train(X, y, cat_indices, target_col)

    if args.demo:
        with open(f'{MODEL_DIR}/model_meta.json') as f:
            meta = json.load(f)

        gen = BuildGenerator()
        gen.load()
        print("Каталоги загружены.")

        demo_requests = [
            {'budget': 1500, 'purpose': 'gaming',       'preferred_brands': ['AMD', 'Nvidia'],  'form_factor': 'standard'},
            {'budget': 900,  'purpose': 'workstation',   'preferred_brands': [],                 'form_factor': 'any'},
            {'budget': 2500, 'purpose': 'rendering',     'preferred_brands': ['Intel', 'Nvidia'],'form_factor': 'standard'},
        ]

        for req in demo_requests:
            top = recommend_builds(
                model         = model,
                feature_names = meta['feature_names'],
                cat_indices   = meta['cat_indices'],
                user_request  = req,
                generator     = gen,
                top_n         = 3,
                n_candidates  = 200,
            )
            if not top.empty:
                print_recommendations(top, req)


if __name__ == '__main__':
    main()
