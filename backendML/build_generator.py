"""
build_generator.py

Генерация сборок ПК под конкретный запрос пользователя в рантайме.
BuildGenerator загружает каталоги один раз из БД, затем generate() вызывается
для каждого запроса без повторного обращения к БД.
"""

import random
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compatibility import (
    get_cpu_socket, get_cpu_brand,
    ram_fits_mb, case_mb_compatible,
    psu_sufficient, estimate_gpu_tdp,
    DDR5_ONLY,
    PURPOSE_PROFILES, FORM_FACTOR_CASES, FORM_FACTOR_MB,
)


class BuildGenerator:
    """
    Загружает каталоги компонентов один раз из БД при инициализации.
    generate() вызывается на каждый запрос пользователя.
    """

    def __init__(self):
        self._loaded   = False
        # Каталоги заполняются в load()
        self.cpu     = None
        self.mb      = None
        self.ram     = None
        self.gpu     = None
        self.storage = None
        self.psu     = None
        self.case    = None
        self.cool    = None
        # Индексы для быстрой выборки
        self._cpu_by_socket = {}
        self._mb_by_socket  = {}
        self._ram_by_gen    = {}

    # ──────────────────────────────────────────────────────────
    # Загрузка каталогов
    # ──────────────────────────────────────────────────────────

    def load(self):
        """Загружает все каталоги из БД. Вызывается один раз при старте."""
        from app.db import load_all_components
        tables = load_all_components()

        cpu     = tables['cpu']
        mb      = tables['motherboard']
        ram     = tables['memory']
        gpu     = tables['video_card']
        storage = tables['internal_hard_drive']
        psu     = tables['power_supply']
        case    = tables['case_enclosure']
        cool    = tables['cpu_cooler']

        # CPU — сокет и бренд из имени
        cpu['socket'] = cpu['name'].apply(get_cpu_socket)
        cpu['brand']  = cpu['name'].apply(get_cpu_brand)
        cpu = cpu[cpu['socket'] != 'Unknown'].reset_index(drop=True)

        # RAM — ddr_gen/speed_mhz/total_gb/modules_count уже из БД
        ram['color'] = ram['color'].fillna('Unknown')
        ram = ram[ram['total_gb'] > 0].reset_index(drop=True)

        # Storage — только потребительские интерфейсы
        consumer = storage['interface'].str.contains(
            'SATA|M.2|PCIe|NVMe', case=False, na=False
        )
        storage = storage[consumer].reset_index(drop=True)

        # MB — предвычисляем список совместимых корпусов
        case = case.reset_index(drop=True)
        mb['compat_cases'] = mb['form_factor'].apply(
            lambda ff: [i for i, ct in enumerate(case['type'])
                        if case_mb_compatible(ct, ff)]
        )

        self.cpu     = cpu
        self.mb      = mb
        self.ram     = ram
        self.gpu     = gpu
        self.storage = storage
        self.psu     = psu
        self.case    = case
        self.cool    = cool

        # Индексы для быстрой выборки
        for sock in cpu['socket'].unique():
            self._cpu_by_socket[sock] = cpu[cpu['socket'] == sock].index.tolist()
        for sock in mb['socket'].unique():
            self._mb_by_socket[sock] = mb[mb['socket'] == sock].index.tolist()
        self._ram_by_gen = {
            4: ram[ram['ddr_gen'] == 4].index.tolist(),
            5: ram[ram['ddr_gen'] == 5].index.tolist(),
        }

        self._loaded = True

    # ──────────────────────────────────────────────────────────
    # Генерация кандидатов под запрос
    # ──────────────────────────────────────────────────────────

    def generate(
        self,
        budget:           float,
        purpose:          str,
        form_factor:      str  = 'any',
        preferred_brands: list = None,
        n:                int  = 200,
        max_attempts:     int  = None,
        seed:             int  = None,
    ) -> pd.DataFrame:
        """
        Генерирует n совместимых сборок под запрос пользователя.

        Параметры
        ---------
        budget           : верхний предел стоимости сборки (USD)
        purpose          : 'gaming' | 'workstation' | 'rendering' | 'office' | 'streaming'
        form_factor      : 'any' | 'compact' | 'standard'
        preferred_brands : список брендов, например ['AMD', 'Nvidia']
        n                : сколько сборок сгенерировать (рекомендуется 100–300)
        max_attempts     : максимум попыток (по умолч. n * 15)
        seed             : для воспроизводимости (None = случайный)

        Возвращает
        ----------
        pd.DataFrame с теми же колонками, что scored_builds.csv
        """
        if not self._loaded:
            self.load()

        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        if max_attempts is None:
            max_attempts = n * 15

        if purpose not in PURPOSE_PROFILES:
            raise ValueError(f"Неизвестное назначение: {purpose}. "
                             f"Допустимы: {list(PURPOSE_PROFILES)}")

        preferred_brands = preferred_brands or []
        profile          = PURPOSE_PROFILES[purpose]
        builds           = []
        attempts         = 0

        while len(builds) < n and attempts < max_attempts:
            attempts += 1

            build = self._try_build(budget, purpose, form_factor,
                                    preferred_brands, profile)
            if build is not None:
                builds.append(build)

        return pd.DataFrame(builds)

    # ──────────────────────────────────────────────────────────
    # Одна попытка собрать сборку
    # ──────────────────────────────────────────────────────────

    def _try_build(self, budget, purpose, ff, preferred_brands, profile) -> dict | None:
        cpu, mb, ram = self.cpu, self.mb, self.ram
        gpu, storage = self.gpu, self.storage
        psu, case, cool = self.psu, self.case, self.cool

        # ── CPU ─────────────────────────────────────────────
        available_sockets = [s for s in self._cpu_by_socket
                             if s in self._mb_by_socket and self._mb_by_socket[s]]
        if not available_sockets:
            return None

        # Ограничиваем сокеты теми, где есть CPU нужного бренда — иначе
        # фильтр по бренду тихо падает обратно на полный пул (другой бренд)
        if preferred_brands:
            brand_sockets = [s for s in available_sockets
                             if any(cpu.at[i, 'brand'] in preferred_brands
                                    for i in self._cpu_by_socket[s])]
            if brand_sockets:
                available_sockets = brand_sockets

        socket   = random.choice(available_sockets)
        cpu_pool = [i for i in self._cpu_by_socket[socket]
                    if not preferred_brands or cpu.at[i, 'brand'] in preferred_brands]
        if not cpu_pool:
            return None

        cpu_idx = random.choice(cpu_pool)
        c = cpu.iloc[cpu_idx]

        # ── MB ───────────────────────────────────────────────
        mb_pool = list(self._mb_by_socket[socket])
        if ff != 'any':
            ff_mbs = FORM_FACTOR_MB.get(ff, [])
            filtered = [i for i in mb_pool if mb.at[i, 'form_factor'] in ff_mbs]
            if filtered:
                mb_pool = filtered
        mb_idx = random.choice(mb_pool)
        m = mb.iloc[mb_idx]

        # ── RAM ──────────────────────────────────────────────
        ddr_gen  = 5 if socket in DDR5_ONLY else 4
        ram_pool = self._ram_by_gen.get(ddr_gen, [])
        if not ram_pool:
            return None

        ram_candidates = [
            i for i in random.sample(ram_pool, min(30, len(ram_pool)))
            if ram_fits_mb(ram.at[i, 'total_gb'],
                           ram.at[i, 'modules_count'],
                           m['max_memory'], m['memory_slots'])
        ]
        if not ram_candidates:
            return None
        r = ram.iloc[random.choice(ram_candidates)]

        # ── Case совместимый с MB ────────────────────────────
        compat_cases = list(m['compat_cases'])
        if ff != 'any':
            ff_cases = FORM_FACTOR_CASES.get(ff, [])
            compat_cases = [i for i in compat_cases
                            if case.at[i, 'type'] in ff_cases]
        if not compat_cases:
            return None
        cs = case.iloc[random.choice(compat_cases)]

        # ── GPU ──────────────────────────────────────────────
        w         = profile['budget_weights']
        need_gpu  = w.get('gpu', 0) > 0
        gp        = None
        gpu_price = 0.0
        if need_gpu:
            # Ограничиваем GPU бюджетом профиля с запасом ×1.5
            gpu_max   = budget * w.get('gpu', 0.3) * 1.5
            gpu_pool  = gpu[gpu['price'] <= max(gpu_max, 80)]
            if gpu_pool.empty:
                gpu_pool = gpu
            gp        = gpu_pool.sample(1).iloc[0]
            gpu_price = float(gp['price'])

        # ── Storage ──────────────────────────────────────────
        # Для производительных сборок предпочитаем SSD
        if purpose in ('gaming', 'workstation', 'rendering', 'streaming'):
            ssd_pool = storage[storage['type'].str.upper().str.contains('SSD|NVME', na=False)]
            st = ssd_pool.sample(1).iloc[0] if not ssd_pool.empty else storage.sample(1).iloc[0]
        else:
            st = storage.sample(1).iloc[0]

        # ── PSU ──────────────────────────────────────────────
        cpu_tdp  = float(c['tdp']) if pd.notna(c.get('tdp')) else 65.0
        psu_pool = psu[psu['wattage'].apply(
            lambda w: psu_sufficient(float(w), cpu_tdp, gpu_price)
        )]
        if psu_pool.empty:
            return None
        ps = psu_pool.sample(1).iloc[0]

        # ── Cooler ───────────────────────────────────────────
        cl = cool.sample(1).iloc[0]

        # ── Бюджет ───────────────────────────────────────────
        total = (float(c['price']) + float(m['price']) + float(r['price']) +
                 float(st['price']) + float(ps['price']) +
                 float(cs['price']) + float(cl['price']) + gpu_price)
        if total > budget * 1.05:
            return None

        return {
            # Мета
            'purpose':            purpose,
            'budget':             round(budget, 2),
            'form_factor':        ff,
            'preferred_brands':   ','.join(preferred_brands),
            'total_price':        round(total, 2),
            'budget_utilization': round(total / budget, 3),

            # CPU
            'cpu_id':            int(c['component_id']),
            'cpu_name':          c['name'],
            'cpu_price':         float(c['price']),
            'cpu_cores':         int(c['core_count']),
            'cpu_base_clock':    float(c['core_clock']),
            'cpu_boost_clock':   float(c['boost_clock']),
            'cpu_tdp':           cpu_tdp,
            'cpu_socket':        socket,
            'cpu_brand':         c['brand'],
            'cpu_smt':           int(c.get('smt', False)),
            'cpu_has_igpu':      bool(pd.notna(c.get('graphics')) and c.get('graphics') != ''),

            # MB
            'mb_id':            int(m['component_id']),
            'mb_name':          m['name'],
            'mb_price':         float(m['price']),
            'mb_socket':        m['socket'],
            'mb_form_factor':   m['form_factor'],
            'mb_max_memory':    int(m['max_memory']),
            'mb_memory_slots':  int(m['memory_slots']),

            # RAM
            'ram_id':            int(r['component_id']),
            'ram_name':          r['name'],
            'ram_price':         float(r['price']),
            'ram_total_gb':      int(r['total_gb']),
            'ram_speed_mhz':     int(r['speed_mhz']),
            'ram_ddr_gen':       int(r['ddr_gen']),
            'ram_modules_count': int(r['modules_count']),
            'ram_cas_latency':   float(r.get('cas_latency', 16)),
            'ram_color':         str(r.get('color', 'Unknown')),

            # GPU
            'gpu_id':           int(gp['component_id']) if gp is not None else None,
            'gpu_name':         gp['name']        if gp is not None else '',
            'gpu_price':        gpu_price,
            'gpu_chipset':      gp['chipset']      if gp is not None else '',
            'gpu_memory':       float(gp['memory']) if gp is not None else 0.0,
            'gpu_boost_clock':  float(gp['boost_clock']) if gp is not None else 0.0,
            'gpu_length':       float(gp['length']) if gp is not None else 0.0,

            # Storage
            'storage_id':            int(st['component_id']),
            'storage_name':          st['name'],
            'storage_price':         float(st['price']),
            'storage_capacity':      float(st['capacity']),
            'storage_type':          st['type'],
            'storage_interface':     st['interface'],
            'storage_form_factor':   st['form_factor'],
            'storage_price_per_gb':  float(st.get('price_per_gb', 0)),

            # PSU
            'psu_id':         int(ps['component_id']),
            'psu_name':       ps['name'],
            'psu_price':      float(ps['price']),
            'psu_wattage':    float(ps['wattage']),
            'psu_efficiency': ps['efficiency'],
            'psu_modular':    ps['modular'],

            # Case
            'case_id':    int(cs['component_id']),
            'case_name':  cs['name'],
            'case_price': float(cs['price']),
            'case_type':  cs['type'],

            # Cooler
            'cooler_id':    int(cl['component_id']),
            'cooler_name':  cl['name'],
            'cooler_price': float(cl['price']),
            'cooler_size':  float(cl['size']),
        }
