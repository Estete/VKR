"""
compatibility.py — правила совместимости компонентов ПК и профили назначения.
"""
import re, ast
import pandas as pd

# ── Парсинг диапазонов ──────────────────────────────────────
def parse_range(val):
    if pd.isna(val): return None
    s = str(val).strip()
    if s.startswith('['):
        try: return ast.literal_eval(s)
        except: return None
    try: return [float(s), float(s)]
    except: return None

# ── CPU ─────────────────────────────────────────────────────
def get_cpu_brand(name):
    n = str(name).upper()
    if n.startswith('AMD'): return 'AMD'
    if n.startswith('INTEL'): return 'Intel'
    return 'Unknown'

def get_cpu_socket(name):
    n = str(name)
    if re.search(r'Ryzen\s+\d+\s+7\d{3}', n):    return 'AM5'
    if re.search(r'Ryzen\s+\d+\s+[1-6]\d{3}', n): return 'AM4'
    if 'Threadripper' in n:                        return 'TRX40'
    if re.search(r'Core\s+i\d-1[234]\d{3}', n):   return 'LGA1700'
    if re.search(r'Core\s+i\d-1[01]\d{3}', n):    return 'LGA1200'
    if re.search(r'Core\s+i\d-[89]\d{3}', n):     return 'LGA1151'
    if 'Xeon' in n:                                return 'LGA1700'
    if 'Athlon' in n:                              return 'AM4'
    return 'Unknown'

# ── RAM ─────────────────────────────────────────────────────
def get_ram_generation(speed_val) -> int:
    """'[4, 3200]' → 4 (DDR4), '[5, 5600]' → 5 (DDR5)"""
    p = parse_range(speed_val)
    if p and int(p[0]) in (4, 5): return int(p[0])
    return 4

def get_ram_speed_mhz(speed_val) -> int:
    p = parse_range(speed_val)
    return int(p[1]) if p and len(p) >= 2 else 3200

def get_ram_total_gb(modules_val) -> int:
    """'[2, 8]' → 2×8 = 16 GB"""
    p = parse_range(modules_val)
    return int(p[0]) * int(p[1]) if p and len(p) >= 2 else 0

def get_ram_modules_count(modules_val) -> int:
    p = parse_range(modules_val)
    return int(p[0]) if p else 2

# DDR совместимость сокетов
DDR5_ONLY = {'AM5'}
DDR4_ONLY = {'AM4', 'LGA1200', 'LGA1151', 'TRX40'}
DDR_BOTH  = {'LGA1700'}

def ram_socket_compatible(ram_gen: int, socket: str) -> bool:
    if socket in DDR5_ONLY: return ram_gen == 5
    if socket in DDR4_ONLY: return ram_gen == 4
    return True  # LGA1700 и неизвестные — пропускаем

def ram_fits_mb(total_gb: int, modules: int, mb_max_mem: int, mb_slots: int) -> bool:
    return total_gb <= mb_max_mem and modules <= mb_slots

# ── Case ↔ MB ───────────────────────────────────────────────
CASE_MB_COMPAT = {
    'ATX Full Tower':      ['ATX', 'Micro ATX', 'Mini ITX', 'EATX'],
    'ATX Mid Tower':       ['ATX', 'Micro ATX', 'Mini ITX'],
    'MicroATX Mini Tower': ['Micro ATX', 'Mini ITX'],
    'Mini ITX Tower':      ['Mini ITX'],
    'Mini ITX Desktop':    ['Mini ITX'],
    'HTPC':                ['Mini ITX'],
}
def case_mb_compatible(case_type, mb_ff):
    return mb_ff in CASE_MB_COMPAT.get(case_type, [])

# ── PSU ─────────────────────────────────────────────────────
def estimate_gpu_tdp(gpu_price):
    if gpu_price < 150:   return 75
    elif gpu_price < 250: return 120
    elif gpu_price < 400: return 160
    elif gpu_price < 600: return 220
    elif gpu_price < 900: return 280
    else:                 return 350

def psu_sufficient(wattage, cpu_tdp, gpu_price, margin=1.25):
    return wattage >= (cpu_tdp + estimate_gpu_tdp(gpu_price)) * margin + 50

# ── Профили назначения ──────────────────────────────────────
PURPOSE_PROFILES = {
    'gaming': {
        'budget_weights': {'gpu':0.35,'cpu':0.20,'mb':0.10,'ram':0.10,
                           'storage':0.08,'psu':0.09,'case':0.05,'cooler':0.03},
        'min_gpu_memory_gb': 8, 'min_ram_gb': 16, 'prefer_gpu': True,
    },
    'workstation': {
        'budget_weights': {'cpu':0.28,'ram':0.18,'mb':0.15,'storage':0.12,
                           'gpu':0.10,'psu':0.10,'case':0.05,'cooler':0.02},
        'min_cpu_cores': 8, 'min_ram_gb': 32, 'prefer_gpu': False,
    },
    'rendering': {
        'budget_weights': {'gpu':0.38,'cpu':0.25,'ram':0.12,'mb':0.10,
                           'storage':0.07,'psu':0.05,'case':0.02,'cooler':0.01},
        'min_gpu_memory_gb': 12, 'min_ram_gb': 32, 'prefer_gpu': True,
    },
    'office': {
        'budget_weights': {'cpu':0.30,'ram':0.18,'mb':0.20,'storage':0.15,
                           'psu':0.10,'case':0.05,'cooler':0.02,'gpu':0.00},
        'min_ram_gb': 8, 'prefer_gpu': False, 'allow_igpu': True,
    },
    'streaming': {
        'budget_weights': {'cpu':0.28,'gpu':0.25,'ram':0.15,'mb':0.12,
                           'storage':0.08,'psu':0.07,'case':0.03,'cooler':0.02},
        'min_cpu_cores': 8, 'min_ram_gb': 16, 'prefer_gpu': True,
    },
}

# ── Форм-фактор ─────────────────────────────────────────────
FORM_FACTOR_CASES = {
    'compact':  ['MicroATX Mini Tower', 'Mini ITX Tower', 'Mini ITX Desktop', 'HTPC'],
    'standard': ['ATX Mid Tower', 'ATX Full Tower'],
    'any':      None,
}
FORM_FACTOR_MB = {
    'compact':  ['Micro ATX', 'Mini ITX'],
    'standard': ['ATX', 'EATX', 'Micro ATX'],
    'any':      None,
}

# ── Максимальная длина GPU по типу корпуса (мм) ─────────────
GPU_MAX_LENGTH_BY_CASE = {
    'HTPC':                200,
    'Mini ITX Desktop':    200,
    'Mini ITX Tower':      330,
    'MicroATX Mini Tower': 360,
    'ATX Mid Tower':       400,
    'ATX Full Tower':      450,
}
GPU_MAX_LENGTH_DEFAULT = 380  # если тип корпуса неизвестен


# ══════════════════════════════════════════════════════════════
# validate_build — полная проверка совместимости одной сборки
# ══════════════════════════════════════════════════════════════
def validate_build(row) -> tuple[bool, list[str]]:
    """
    Принимает строку DataFrame (или dict) с полями сборки.
    Возвращает (is_compatible: bool, issues: list[str]).
    Пустой список issues означает полную совместимость.
    """
    issues = []

    # ── 1. Сокет CPU ↔ MB ────────────────────────────────────
    cpu_socket = str(row.get('cpu_socket', '')).strip()
    mb_socket  = str(row.get('mb_socket',  '')).strip()
    if cpu_socket and mb_socket and cpu_socket != mb_socket:
        issues.append(
            f"Сокет CPU ({cpu_socket}) не совпадает с сокетом MB ({mb_socket})"
        )

    # ── 2. DDR-поколение RAM ↔ сокет ─────────────────────────
    try:
        ddr_gen = int(row.get('ram_ddr_gen', 4))
    except (ValueError, TypeError):
        ddr_gen = 4
    if cpu_socket and not ram_socket_compatible(ddr_gen, cpu_socket):
        expected = 5 if cpu_socket in DDR5_ONLY else 4
        issues.append(
            f"RAM DDR{ddr_gen} несовместима с сокетом {cpu_socket} "
            f"(требуется DDR{expected})"
        )

    # ── 3. RAM вмещается в MB (объём + слоты) ────────────────
    try:
        ram_total    = int(row.get('ram_total_gb', 0))
        ram_modules  = int(row.get('ram_modules_count', 0))
        mb_max_mem   = int(row.get('mb_max_memory', 0))
        mb_slots     = int(row.get('mb_memory_slots', 0))
    except (ValueError, TypeError):
        ram_total = ram_modules = mb_max_mem = mb_slots = 0

    if mb_max_mem > 0 and ram_total > mb_max_mem:
        issues.append(
            f"RAM {ram_total} GB превышает максимум MB ({mb_max_mem} GB)"
        )
    if mb_slots > 0 and ram_modules > mb_slots:
        issues.append(
            f"RAM занимает {ram_modules} слота, у MB только {mb_slots}"
        )

    # ── 4. Корпус ↔ форм-фактор MB ───────────────────────────
    case_type = str(row.get('case_type', '')).strip()
    mb_ff     = str(row.get('mb_form_factor', '')).strip()
    if case_type and mb_ff:
        if not case_mb_compatible(case_type, mb_ff):
            allowed = CASE_MB_COMPAT.get(case_type, [])
            issues.append(
                f"Корпус «{case_type}» не поддерживает плату «{mb_ff}» "
                f"(допустимо: {', '.join(allowed) if allowed else 'неизвестно'})"
            )

    # ── 5. Мощность БП ───────────────────────────────────────
    try:
        psu_w    = float(row.get('psu_wattage', 0))
        cpu_tdp  = float(row.get('cpu_tdp', 65))
        gpu_pr   = float(row.get('gpu_price', 0))
    except (ValueError, TypeError):
        psu_w = cpu_tdp = gpu_pr = 0

    if psu_w > 0 and not psu_sufficient(psu_w, cpu_tdp, gpu_pr):
        needed = (cpu_tdp + estimate_gpu_tdp(gpu_pr)) * 1.25 + 50
        issues.append(
            f"БП {int(psu_w)} W недостаточен "
            f"(требуется ≥ {int(needed)} W для CPU {int(cpu_tdp)} W + GPU)"
        )

    # ── 6. Длина GPU ↔ корпус ────────────────────────────────
    try:
        gpu_length = float(row.get('gpu_length', 0))
    except (ValueError, TypeError):
        gpu_length = 0

    if gpu_length > 0 and case_type:
        max_len = GPU_MAX_LENGTH_BY_CASE.get(case_type, GPU_MAX_LENGTH_DEFAULT)
        if gpu_length > max_len:
            issues.append(
                f"GPU длиной {int(gpu_length)} мм не помещается в корпус "
                f"«{case_type}» (макс. {max_len} мм)"
            )

    # ── 7. Office без GPU — нужна iGPU ───────────────────────
    purpose = str(row.get('purpose', '')).strip()
    if purpose == 'office':
        has_igpu = row.get('cpu_has_igpu', False)
        # В CSV после загрузки может быть строка 'True'/'False'
        if isinstance(has_igpu, str):
            has_igpu = has_igpu.strip().lower() == 'true'
        if not has_igpu and gpu_pr == 0:
            issues.append(
                "Office-сборка без дискретного GPU требует встроенной графики (iGPU)"
            )

    return (len(issues) == 0), issues
