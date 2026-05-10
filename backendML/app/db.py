"""
app/db.py — подключение к MySQL и загрузка компонентов.

Возвращает DataFrame с именами колонок, совместимыми с остальным кодом:
  max_memory_gb → max_memory, memory_gb → memory, length_mm → length,
  capacity_gb → capacity, cache_mb → cache, size_mm → size.
  RAM: speed_generation → ddr_gen, total_gb = modules_count * module_size_gb.
"""
import os

import pandas as pd
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", "3307")),
    "database": os.getenv("DB_NAME",     "pc_parts"),
    "user":     os.getenv("DB_USER",     "claude_ro"),
    "password": os.getenv("DB_PASSWORD", ""),
    "charset":  "utf8mb4",
}

# Пользователь с правами на запись (нужен для save_build)
DB_CONFIG_WRITE = {
    **DB_CONFIG,
    "user":     os.getenv("DB_USER_WRITE",     "pc_app"),
    "password": os.getenv("DB_PASSWORD_WRITE", ""),
}

# ── Запросы для загрузки каталогов ──────────────────────────

_QUERIES = {
    "cpu": """
        SELECT c.id AS component_id, c.name, c.price,
               cpu.core_count, cpu.core_clock, cpu.boost_clock,
               cpu.tdp, cpu.graphics, cpu.smt
        FROM cpu
        JOIN component c ON cpu.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
    "motherboard": """
        SELECT c.id AS component_id, c.name, c.price, c.color,
               mb.socket, mb.form_factor,
               mb.max_memory_gb AS max_memory,
               mb.memory_slots
        FROM motherboard mb
        JOIN component c ON mb.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
    "memory": """
        SELECT c.id AS component_id, c.name, c.price, c.color,
               mem.speed_generation AS ddr_gen,
               mem.speed_mhz,
               mem.modules_count,
               mem.modules_count * mem.module_size_gb AS total_gb,
               mem.price_per_gb,
               mem.first_word_latency,
               mem.cas_latency
        FROM memory mem
        JOIN component c ON mem.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
          AND mem.modules_count > 0
          AND mem.module_size_gb > 0
    """,
    "video_card": """
        SELECT c.id AS component_id, c.name, c.price, c.color,
               vc.chipset,
               vc.memory_gb AS memory,
               vc.core_clock,
               vc.boost_clock,
               vc.length_mm AS length
        FROM video_card vc
        JOIN component c ON vc.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
    "internal_hard_drive": """
        SELECT c.id AS component_id, c.name, c.price,
               ihd.capacity_gb AS capacity,
               ihd.price_per_gb,
               ihd.type,
               ihd.cache_mb AS cache,
               ihd.form_factor,
               ihd.interface
        FROM internal_hard_drive ihd
        JOIN component c ON ihd.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
    "power_supply": """
        SELECT c.id AS component_id, c.name, c.price, c.color,
               ps.type, ps.efficiency,
               ps.wattage, ps.modular
        FROM power_supply ps
        JOIN component c ON ps.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
    "case_enclosure": """
        SELECT c.id AS component_id, c.name, c.price, c.color,
               ce.type, ce.psu, ce.side_panel,
               ce.external_525_bays, ce.internal_35_bays
        FROM case_enclosure ce
        JOIN component c ON ce.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
    "cpu_cooler": """
        SELECT c.id AS component_id, c.name, c.price, c.color,
               cc.rpm_min, cc.rpm_max,
               cc.noise_min, cc.noise_max,
               cc.size_mm AS size
        FROM cpu_cooler cc
        JOIN component c ON cc.component_id = c.id
        WHERE c.price IS NOT NULL AND c.price > 0
    """,
}

# Маппинг category → (таблица, специфичные колонки)
_CATEGORY_TABLE = {
    "cpu":          ("cpu",                 "core_count, core_clock, boost_clock, tdp, graphics, smt"),
    "motherboard":  ("motherboard",         "socket, form_factor, max_memory_gb, memory_slots"),
    "memory":       ("memory",              "speed_generation, speed_mhz, modules_count, module_size_gb, price_per_gb, cas_latency"),
    "video_card":   ("video_card",          "chipset, memory_gb, core_clock, boost_clock, length_mm"),
    "storage":      ("internal_hard_drive", "capacity_gb, price_per_gb, type, cache_mb, form_factor, interface"),
    "power_supply": ("power_supply",        "type, efficiency, wattage, modular"),
    "case":         ("case_enclosure",      "type, psu, side_panel, external_525_bays, internal_35_bays"),
    "cooler":       ("cpu_cooler",          "rpm_min, rpm_max, noise_min, noise_max, size_mm"),
}


def get_connection(write: bool = False) -> pymysql.Connection:
    cfg = DB_CONFIG_WRITE if write else DB_CONFIG
    return pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor)


# ── Загрузка каталогов для BuildGenerator ───────────────────

def load_table(table_name: str) -> pd.DataFrame:
    query = _QUERIES.get(table_name)
    if query is None:
        raise ValueError(
            f"Таблица '{table_name}' не описана. Доступные: {list(_QUERIES)}"
        )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # pymysql возвращает DECIMAL как Python Decimal (object dtype) — конвертируем в float
        for col in df.select_dtypes(include='object').columns:
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass
        return df
    finally:
        conn.close()


def load_all_components() -> dict:
    """Загружает все таблицы компонентов из БД. Возвращает {имя: DataFrame}."""
    result = {}
    for table in _QUERIES:
        try:
            result[table] = load_table(table)
        except Exception as e:
            print(f"  [{table}] ошибка загрузки: {e}")
            result[table] = pd.DataFrame()
    return result


# ── Пакетный поиск ID компонентов по именам ─────────────────

def get_component_ids_by_names(names: list[str]) -> dict[str, int]:
    """Возвращает {name: id} для переданных имён компонентов."""
    if not names:
        return {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            placeholders = ', '.join(['%s'] * len(names))
            cur.execute(
                f"SELECT id, name FROM component WHERE name IN ({placeholders})",
                names,
            )
            return {row['name']: row['id'] for row in cur.fetchall()}
    finally:
        conn.close()


# ── Получение деталей компонента ─────────────────────────────

def get_component_by_id(component_id: int) -> dict | None:
    """
    Возвращает полные данные компонента: общие поля + специфичные для категории.
    None если компонент не найден.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.id, c.name, c.price, c.color, cat.name AS category "
                "FROM component c "
                "JOIN category cat ON c.category_id = cat.id "
                "WHERE c.id = %s",
                (component_id,)
            )
            base = cur.fetchone()
            if base is None:
                return None

            category = base['category']
            if category not in _CATEGORY_TABLE:
                return {**base, 'specs': {}}

            table, spec_cols = _CATEGORY_TABLE[category]
            cur.execute(
                f"SELECT {spec_cols} FROM `{table}` WHERE component_id = %s",
                (component_id,)
            )
            specs = cur.fetchone() or {}
            return {**base, 'specs': specs}
    finally:
        conn.close()


# ── Поиск по каталогу ────────────────────────────────────────

def search_components(category: str, filters: dict, limit: int = 10) -> list[dict]:
    """
    Ищет компоненты в каталоге с фильтрами.

    filters поддерживает: max_price, min_price и любые числовые поля таблицы
    в виде min_<поле> / max_<поле> (например, min_core_count=8).
    """
    if category not in _CATEGORY_TABLE:
        raise ValueError(f"Неизвестная категория: {category}. Доступные: {list(_CATEGORY_TABLE)}")

    table, spec_cols = _CATEGORY_TABLE[category]

    conditions = ["c.price IS NOT NULL", "c.price > 0"]
    params: list = []

    if 'max_price' in filters:
        conditions.append("c.price <= %s")
        params.append(filters['max_price'])
    if 'min_price' in filters:
        conditions.append("c.price >= %s")
        params.append(filters['min_price'])

    spec_col_list = [s.strip() for s in spec_cols.split(',')]
    for col in spec_col_list:
        col_clean = col.strip().split()[-1]   # убираем алиасы если есть
        if f'min_{col_clean}' in filters:
            conditions.append(f"t.{col_clean} >= %s")
            params.append(filters[f'min_{col_clean}'])
        if f'max_{col_clean}' in filters:
            conditions.append(f"t.{col_clean} <= %s")
            params.append(filters[f'max_{col_clean}'])

    where = " AND ".join(conditions)
    query = (
        f"SELECT c.id AS component_id, c.name, c.price, c.color, {spec_cols} "
        f"FROM `{table}` t "
        f"JOIN component c ON t.component_id = c.id "
        f"WHERE {where} "
        f"ORDER BY c.price ASC "
        f"LIMIT %s"
    )
    params.append(limit)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Сохранение сборки ────────────────────────────────────────

def save_build(user_id: int, chat_id: int | None, purpose: str,
               budget: float, total_price: float,
               components: dict[str, int]) -> int:
    """
    Записывает сборку в таблицы builds + build_components.
    Возвращает build_id новой записи.
    Требует пользователя с правами INSERT.
    """
    conn = get_connection(write=True)
    try:
        with conn.cursor() as cur:
            if chat_id is not None:
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM builds "
                    "WHERE user_id = %s AND chat_id = %s",
                    (user_id, chat_id)
                )
                version = cur.fetchone()['COALESCE(MAX(version), 0) + 1']
            else:
                version = 1

            cur.execute(
                "INSERT INTO builds (user_id, chat_id, version, purpose, budget, total_price) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, chat_id, version, purpose, budget, total_price)
            )
            build_id = cur.lastrowid

            for category, component_id in components.items():
                cur.execute(
                    "INSERT INTO build_components (build_id, category, component_id) "
                    "VALUES (%s, %s, %s)",
                    (build_id, category, component_id)
                )

        conn.commit()
        return build_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Синтетические сборки (обучающие данные ML) ──────────────

def save_synthetic_builds(df: pd.DataFrame) -> None:
    """Очищает таблицу synthetic_builds и записывает DataFrame целиком."""
    cols = [c for c in df.columns]
    placeholders = ', '.join(['%s'] * len(cols))
    col_names = ', '.join(f'`{c}`' for c in cols)
    insert_sql = f'INSERT INTO synthetic_builds ({col_names}) VALUES ({placeholders})'

    rows = []
    for _, row in df.iterrows():
        rows.append([None if pd.isna(v) else v for v in row[cols].tolist()])

    conn = get_connection(write=True)
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM synthetic_builds')
            if rows:
                cur.executemany(insert_sql, rows)
        conn.commit()
        print(f"[DB] {len(rows)} сборок сохранено в synthetic_builds")
    finally:
        conn.close()


def load_synthetic_builds() -> pd.DataFrame:
    """Загружает все синтетические сборки из БД."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM synthetic_builds')
            rows = cur.fetchall()
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    finally:
        conn.close()


def update_synthetic_build_label(build_id: int, label: float, comment: str) -> None:
    """Обновляет ручную метку конкретной сборки."""
    conn = get_connection(write=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE synthetic_builds SET human_label = %s, human_comment = %s WHERE id = %s',
                (label, comment or None, build_id)
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    print("Проверка подключения к БД...")
    tables = load_all_components()
    for name, df in tables.items():
        print(f"  {name:<25} {len(df):>6} строк")
