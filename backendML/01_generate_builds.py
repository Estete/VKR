"""
01_generate_builds.py
Генерирует синтетические сборки ПК со всеми компонентами и оценивает их.

Компоненты: CPU + MB + RAM + GPU + Storage + PSU + Case + Cooler
Сохраняет: processed/scored_builds.csv + processed/feature_info.json
"""
import os, sys, json, random, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compatibility import (
    get_cpu_socket, get_cpu_brand,
    ram_socket_compatible, ram_fits_mb,
    case_mb_compatible, psu_sufficient, estimate_gpu_tdp,
    PURPOSE_PROFILES, FORM_FACTOR_CASES, FORM_FACTOR_MB,
)
from app.db import load_all_components, save_synthetic_builds

OUTPUT_DIR  = './processed'
os.makedirs(OUTPUT_DIR, exist_ok=True)
N_BUILDS    = 5000
RANDOM_SEED = 42
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)


# ── Загрузка ────────────────────────────────────────────────
def load():
    tables = load_all_components()

    cpu     = tables['cpu']
    mb      = tables['motherboard']
    ram     = tables['memory']
    gpu     = tables['video_card']
    storage = tables['internal_hard_drive']
    psu     = tables['power_supply']
    case    = tables['case_enclosure']
    cool    = tables['cpu_cooler']

    # CPU — определяем сокет и бренд
    cpu['socket'] = cpu['name'].apply(get_cpu_socket)
    cpu['brand']  = cpu['name'].apply(get_cpu_brand)
    cpu = cpu[cpu['socket'] != 'Unknown'].reset_index(drop=True)

    # RAM — ddr_gen/speed_mhz/total_gb/modules_count уже из БД
    ram['color'] = ram['color'].fillna('Unknown')
    ram = ram[ram['total_gb'] > 0].reset_index(drop=True)

    # Storage — оставляем потребительские интерфейсы (убираем SAS серверные)
    consumer_ifaces = storage['interface'].str.contains(
        'SATA|M.2|PCIe|NVMe', case=False, na=False
    )
    storage = storage[consumer_ifaces].reset_index(drop=True)

    # MB — предвычисляем совместимые корпуса
    case = case.reset_index(drop=True)
    mb['compat_cases'] = mb['form_factor'].apply(
        lambda ff: [i for i, ct in enumerate(case['type'])
                    if case_mb_compatible(ct, ff)]
    )

    print(f"Загружено: CPU={len(cpu)} MB={len(mb)} RAM={len(ram)} "
          f"GPU={len(gpu)} Storage={len(storage)} "
          f"PSU={len(psu)} Case={len(case)} Cooler={len(cool)}")

    return cpu, mb, ram, gpu, storage, psu, case, cool


# ── Скоринг ─────────────────────────────────────────────────
def score_build(b, purpose, budget):
    profile = PURPOSE_PROFILES[purpose]
    w       = profile['budget_weights']
    total   = b['total_price']
    scores  = {}

    # 1. Бюджетная эффективность (0-25)
    util = total / budget
    if 0.85 <= util <= 1.0:
        scores['budget_score'] = 25.0
    elif util < 0.85:
        scores['budget_score'] = 25.0 * (util / 0.85)
    else:
        scores['budget_score'] = max(0, 25.0 - (util - 1.0) * 100)

    # 2. Баланс компонентов (0-30)
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

    # 3. Соответствие назначению (0-25)
    ps = 0.0
    if purpose == 'gaming':
        mem    = b.get('gpu_memory', 0)
        min_vr = profile.get('min_gpu_memory_gb', 8)
        ps += 8 * min(1.0, mem / max(min_vr, 1))
        ps += 8 * min(1.0, b.get('gpu_boost_clock', 1500) / 3000)
        ps += 5 * min(1.0, b.get('ram_total_gb', 0) / max(profile.get('min_ram_gb', 16), 1))
        ps += 4 * min(1.0, b.get('cpu_boost_clock', 3.0) / 5.0)
    elif purpose == 'rendering':
        mem    = b.get('gpu_memory', 0)
        min_vr = profile.get('min_gpu_memory_gb', 8)
        ps += 8 * min(1.0, mem / max(min_vr, 1))
        ps += 6 * min(1.0, b.get('gpu_boost_clock', 1500) / 3000)
        cores  = b.get('cpu_cores', 4)
        min_c  = profile.get('min_cpu_cores', 8)
        ps += 7 * min(1.0, cores / max(min_c, 1))
        ps += 4 * min(1.0, b.get('ram_total_gb', 0) / max(profile.get('min_ram_gb', 16), 1))
    elif purpose == 'streaming':
        mem    = b.get('gpu_memory', 0)
        min_vr = profile.get('min_gpu_memory_gb', 6)
        ps += 6 * min(1.0, mem / max(min_vr, 1))
        ps += 6 * min(1.0, b.get('gpu_boost_clock', 1500) / 3000)
        ps += 7 * min(1.0, b.get('cpu_boost_clock', 3.0) / 5.0)
        ps += 6 * min(1.0, b.get('ram_total_gb', 0) / max(profile.get('min_ram_gb', 16), 1))
    elif purpose == 'workstation':
        cores   = b.get('cpu_cores', 4)
        min_c   = profile.get('min_cpu_cores', 8)
        ram_gb  = b.get('ram_total_gb', 0)
        min_ram = profile.get('min_ram_gb', 32)
        ps += 12 * min(1.0, cores / max(min_c, 1))
        ps += 8  * min(1.0, ram_gb / max(min_ram, 1))
        ps += 5  * (1 - min(1.0, max(0, b.get('cpu_tdp', 65) - 125) / 155))
    elif purpose == 'office':
        ps += min(12, 12 * (1 - b.get('cpu_tdp', 65) / 200))
        ps += 8 if b.get('cpu_has_igpu', False) else 0
        ps += 5 if 'SSD' in str(b.get('storage_type', '')).upper() else 0
    scores['purpose_score'] = min(25.0, ps)

    # 4. Ценность — смесь абсолютной производительности и соотношения цена/качество (0-20)
    cpu_perf_abs = min(1.0, (b.get('cpu_cores', 1) * b.get('cpu_boost_clock', 3.0)) / 80)
    cpu_per_usd  = min(1.0, (b.get('cpu_cores', 1) * b.get('cpu_boost_clock', 3.0))
                       / max(b['cpu_price'], 1) / 0.25)
    cv = 0.5 * cpu_perf_abs + 0.5 * cpu_per_usd

    gpu_price = b.get('gpu_price', 0)
    if gpu_price > 0:
        gpu_perf_abs = min(1.0, (b.get('gpu_boost_clock', 0) * b.get('gpu_memory', 0)) / 40000)
        gpu_per_usd  = min(1.0, (b.get('gpu_boost_clock', 0) * b.get('gpu_memory', 0))
                           / max(gpu_price, 1) / 50)
        gv = 0.5 * gpu_perf_abs + 0.5 * gpu_per_usd
    else:
        gv = cv  # нет GPU — оцениваем только по CPU

    if purpose in ('gaming', 'rendering', 'streaming'):
        scores['value_score'] = 20.0 * (0.35 * cv + 0.65 * gv)
    else:
        scores['value_score'] = 20.0 * (0.70 * cv + 0.30 * gv)

    scores['total_score'] = round(min(100.0, sum(scores.values())), 2)
    return scores


# ── Генерация ────────────────────────────────────────────────
def main():
    t_start = time.perf_counter()
    print("Загрузка компонентов...")
    cpu, mb, ram, gpu, storage, psu, case, cool = load()

    # Индексы по сокету для быстрого выбора
    cpu_by_socket = {}
    for sock in cpu['socket'].unique():
        cpu_by_socket[sock] = cpu[cpu['socket'] == sock].index.tolist()

    mb_by_socket = {}
    for sock in mb['socket'].unique():
        mb_by_socket[sock] = mb[mb['socket'] == sock].index.tolist()

    # RAM по поколению
    ram_by_gen = {4: ram[ram['ddr_gen'] == 4].index.tolist(),
                  5: ram[ram['ddr_gen'] == 5].index.tolist()}

    purposes = list(PURPOSE_PROFILES.keys())
    budgets  = [500, 700, 900, 1200, 1500, 2000, 2500, 3000, 4000, 5000]
    ffs      = ['any', 'compact', 'standard']
    brands   = [[], ['AMD'], ['Intel'], ['AMD', 'Nvidia'], ['Intel', 'Nvidia']]

    builds   = []
    attempts = 0

    print(f"Генерация {N_BUILDS} сборок...")

    while len(builds) < N_BUILDS and attempts < N_BUILDS * 10:
        attempts += 1

        purpose = random.choice(purposes)
        budget  = random.choice(budgets) * random.uniform(0.9, 1.1)
        ff      = random.choice(ffs)
        brand   = random.choice(brands)
        profile = PURPOSE_PROFILES[purpose]

        # ── CPU ─────────────────────────────────────────────
        socket = random.choice(list(cpu_by_socket.keys()))
        if socket not in mb_by_socket or not mb_by_socket[socket]:
            continue

        cpu_pool = cpu_by_socket[socket]
        if brand:
            bp = [i for i in cpu_pool if cpu.at[i, 'brand'] in brand]
            if bp: cpu_pool = bp
        cpu_idx = random.choice(cpu_pool)
        c = cpu.iloc[cpu_idx]

        # ── MB ───────────────────────────────────────────────
        mb_pool = mb_by_socket[socket]
        if ff != 'any':
            ff_mbs = FORM_FACTOR_MB.get(ff, [])
            mb_ff  = [i for i in mb_pool if mb.at[i, 'form_factor'] in ff_mbs]
            if mb_ff: mb_pool = mb_ff
        mb_idx = random.choice(mb_pool)
        m = mb.iloc[mb_idx]

        # ── RAM (DDR-совместима с сокетом и влезает в MB) ───
        ddr_gen   = 5 if socket in {'AM5'} else 4
        ram_pool  = ram_by_gen.get(ddr_gen, [])
        if not ram_pool:
            continue
        # Фильтруем по ёмкости MB (случайная выборка + проверка)
        ram_candidates = [i for i in random.sample(ram_pool, min(30, len(ram_pool)))
                          if ram_fits_mb(ram.at[i, 'total_gb'],
                                         ram.at[i, 'modules_count'],
                                         m['max_memory'], m['memory_slots'])]
        if not ram_candidates:
            continue
        ram_idx = random.choice(ram_candidates)
        r = ram.iloc[ram_idx]

        # ── Case совместимый с MB ────────────────────────────
        compat_cases = m['compat_cases']
        if ff != 'any':
            ff_cases = FORM_FACTOR_CASES.get(ff, [])
            compat_cases = [i for i in compat_cases if case.at[i, 'type'] in ff_cases]
        if not compat_cases:
            continue
        cs = case.iloc[random.choice(compat_cases)]

        # ── GPU ──────────────────────────────────────────────
        need_gpu  = profile['budget_weights'].get('gpu', 0) > 0
        gp        = None
        gpu_price = 0.0
        if need_gpu:
            w_gpu    = profile['budget_weights'].get('gpu', 0.3)
            gpu_max  = budget * w_gpu * 1.5
            gpu_pool = gpu[gpu['price'] <= max(gpu_max, 80)]
            if gpu_pool.empty:
                gpu_pool = gpu
            gp        = gpu_pool.sample(1).iloc[0]
            gpu_price = float(gp['price'])

        # ── Storage ──────────────────────────────────────────
        if purpose in ('gaming', 'workstation', 'rendering', 'streaming'):
            ssd_pool = storage[storage['type'].str.upper().str.contains('SSD|NVME', na=False)]
            st = ssd_pool.sample(1).iloc[0] if not ssd_pool.empty else storage.sample(1).iloc[0]
        else:
            st = storage.sample(1).iloc[0]

        # ── PSU ──────────────────────────────────────────────
        cpu_tdp = float(c['tdp']) if pd.notna(c.get('tdp')) else 65.0
        psu_ok = psu[psu['wattage'].apply(
            lambda w: psu_sufficient(w, cpu_tdp, gpu_price)
        )]
        if psu_ok.empty:
            continue
        ps = psu_ok.sample(1).iloc[0]

        # ── Cooler ───────────────────────────────────────────
        cl = cool.sample(1).iloc[0]

        # ── Итоговая цена ────────────────────────────────────
        total = (c['price'] + m['price'] + r['price'] + st['price'] +
                 ps['price'] + cs['price'] + cl['price'] + gpu_price)
        if total > budget * 1.05:
            continue

        build = {
            # Мета
            'purpose':          purpose,
            'budget':           round(budget, 2),
            'form_factor':      ff,
            'preferred_brands': ','.join(brand) if brand else '',
            'total_price':      round(total, 2),
            'budget_utilization': round(total / budget, 3),

            # CPU
            'cpu_name':         c['name'],
            'cpu_price':        c['price'],
            'cpu_cores':        c['core_count'],
            'cpu_base_clock':   c['core_clock'],
            'cpu_boost_clock':  c['boost_clock'],
            'cpu_tdp':          cpu_tdp,
            'cpu_socket':       socket,
            'cpu_brand':        c['brand'],
            'cpu_smt':          int(c.get('smt', False)),
            'cpu_has_igpu':     bool(pd.notna(c.get('graphics')) and c.get('graphics') != ''),

            # MB
            'mb_name':          m['name'],
            'mb_price':         m['price'],
            'mb_socket':        m['socket'],
            'mb_form_factor':   m['form_factor'],
            'mb_max_memory':    m['max_memory'],
            'mb_memory_slots':  m['memory_slots'],

            # RAM
            'ram_name':         r['name'],
            'ram_price':        r['price'],
            'ram_total_gb':     r['total_gb'],
            'ram_speed_mhz':    r['speed_mhz'],
            'ram_ddr_gen':      r['ddr_gen'],
            'ram_modules_count':r['modules_count'],
            'ram_cas_latency':  r.get('cas_latency', 16),
            'ram_color':        r.get('color', 'Unknown'),

            # GPU
            'gpu_name':         gp['name']        if gp is not None else '',
            'gpu_price':        gpu_price,
            'gpu_chipset':      gp['chipset']      if gp is not None else '',
            'gpu_memory':       gp['memory']       if gp is not None else 0,
            'gpu_boost_clock':  gp['boost_clock']  if gp is not None else 0,
            'gpu_length':       gp['length']       if gp is not None else 0,

            # Storage
            'storage_name':      st['name'],
            'storage_price':     st['price'],
            'storage_capacity':  st['capacity'],
            'storage_type':      st['type'],
            'storage_interface': st['interface'],
            'storage_form_factor': st['form_factor'],
            'storage_price_per_gb': st.get('price_per_gb', 0),

            # PSU
            'psu_name':         ps['name'],
            'psu_price':        ps['price'],
            'psu_wattage':      ps['wattage'],
            'psu_efficiency':   ps['efficiency'],
            'psu_modular':      ps['modular'],

            # Case
            'case_name':        cs['name'],
            'case_price':       cs['price'],
            'case_type':        cs['type'],

            # Cooler
            'cooler_name':      cl['name'],
            'cooler_price':     cl['price'],
            'cooler_size':      cl['size'],
        }

        sc = score_build(build, purpose, budget)
        build.update(sc)
        build['human_label']   = None
        build['human_comment'] = None
        builds.append(build)

        if len(builds) % 500 == 0:
            print(f"  {len(builds)}/{N_BUILDS}...")

    df = pd.DataFrame(builds)
    save_synthetic_builds(df)

    print(f"\n   Попыток: {attempts}, успех: {100*len(df)/max(attempts,1):.1f}%")
    print(f"\nРаспределение total_score:\n{df['total_score'].describe().round(2)}")
    print(f"\nПо назначению:\n{df['purpose'].value_counts()}")

    # feature_info для CatBoost
    feature_info = {
        'target': 'total_score',
        'cat_features': [
            'purpose', 'form_factor', 'preferred_brands',
            'cpu_brand', 'cpu_socket',
            'mb_form_factor', 'mb_socket',
            'ram_color',
            'gpu_chipset',
            'storage_type', 'storage_interface', 'storage_form_factor',
            'psu_efficiency', 'psu_modular',
            'case_type',
        ],
        'num_features': [
            'budget', 'total_price', 'budget_utilization',
            'cpu_price', 'cpu_cores', 'cpu_base_clock', 'cpu_boost_clock', 'cpu_tdp', 'cpu_smt',
            'mb_price', 'mb_max_memory', 'mb_memory_slots',
            'ram_price', 'ram_total_gb', 'ram_speed_mhz', 'ram_ddr_gen',
            'ram_modules_count', 'ram_cas_latency',
            'gpu_price', 'gpu_memory', 'gpu_boost_clock', 'gpu_length',
            'storage_price', 'storage_capacity', 'storage_price_per_gb',
            'psu_price', 'psu_wattage',
            'case_price', 'cooler_price', 'cooler_size',
        ],
        'drop_features': [
            'cpu_name', 'mb_name', 'ram_name', 'gpu_name',
            'storage_name', 'psu_name', 'case_name', 'cooler_name',
            'cpu_has_igpu', 'human_label', 'human_comment',
            'budget_score', 'balance_score', 'purpose_score', 'value_score',
        ],
    }
    with open(f'{OUTPUT_DIR}/feature_info.json', 'w') as f:
        json.dump(feature_info, f, indent=2, ensure_ascii=False)
    print(f"[OK] feature_info.json сохранён")

    elapsed = time.perf_counter() - t_start
    print(f"\n{'─'*40}")
    print(f"Сгенерировано сборок : {len(df)}")
    print(f"Время выполнения     : {elapsed:.1f} сек ({elapsed/60:.1f} мин)")
    print(f"{'─'*40}")


if __name__ == '__main__':
    main()
