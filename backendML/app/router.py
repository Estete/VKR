"""
app/router.py — эндпоинты FastAPI (инструменты для LLM function calling).

POST /api/v1/generate_builds          — генерация + ранжирование сборок
POST /api/v1/replace_component        — замена одного компонента
GET  /api/v1/component/{id}           — детали компонента
POST /api/v1/search_catalog           — поиск по каталогу с фильтрами
POST /api/v1/save_build               — сохранение сборки в БД пользователя
GET  /api/v1/health                   — статус сервиса
GET  /api/v1/purposes                 — список назначений
"""
from fastapi import APIRouter, HTTPException

from app.schemas import (
    GenerateBuildsRequest, GenerateBuildsResponse, BuildResult, ComponentInfo,
    ReplaceComponentRequest, ReplaceComponentResponse, ReplacementOption,
    ComponentDetailsResponse,
    SearchCatalogRequest, SearchCatalogResponse, CatalogItem,
    SaveBuildRequest, SaveBuildResponse,
    HealthResponse, PurposeInfo,
)


def _flatten_build_context(ctx: BuildResult) -> dict:
    """Преобразует BuildResult в плоский dict для model_service.score_candidates."""
    d = ctx.model_dump()
    flat: dict = {}

    comp_map = {
        'cpu':     ('cpu_name',     'cpu_price'),
        'mb':      ('mb_name',      'mb_price'),
        'ram':     ('ram_name',     'ram_price'),
        'gpu':     ('gpu_name',     'gpu_price'),
        'storage': ('storage_name', 'storage_price'),
        'psu':     ('psu_name',     'psu_price'),
        'case':    ('case_name',    'case_price'),
        'cooler':  ('cooler_name',  'cooler_price'),
    }
    for attr, (name_f, price_f) in comp_map.items():
        comp = d.get(attr)
        if comp:
            flat[name_f]  = comp['name']
            flat[price_f] = float(comp['price'] or 0)
        else:
            flat[name_f]  = ''
            flat[price_f] = 0.0

    for field in ('purpose', 'budget', 'total_price', 'budget_utilization',
                  'cpu_cores', 'cpu_boost_clock', 'ram_total_gb', 'ram_ddr_gen',
                  'gpu_memory', 'storage_type', 'psu_wattage'):
        if field in d:
            flat[field] = d[field]

    return flat
from app.model_service import model_service
from app.db import get_component_by_id, search_components, save_build as db_save_build
from compatibility import PURPOSE_PROFILES

router = APIRouter()


# ── POST /generate_builds ────────────────────────────────────

@router.post("/generate_builds", response_model=GenerateBuildsResponse)
def generate_builds(req: GenerateBuildsRequest):
    if not model_service.is_loaded:
        raise HTTPException(status_code=503, detail="Сервис не готов")

    top = model_service.generate_builds(req.model_dump(), top_n=req.top_n)

    if top.empty:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Не удалось подобрать совместимые сборки: "
                f"бюджет=${req.budget:.0f}, назначение={req.purpose}. "
                f"Попробуйте увеличить бюджет или изменить параметры."
            )
        )

    def ci(name_col: str, price_col: str, id_col: str, row) -> ComponentInfo:
        val = row.get(id_col)
        try:
            cid = int(val) if val is not None else None
        except (TypeError, ValueError):
            cid = None
        return ComponentInfo(component_id=cid, name=str(row[name_col]), price=row[price_col])

    builds = []
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        has_gpu = row.get('gpu_price', 0) > 0
        if has_gpu:
            gpu_val = row.get('gpu_id')
            try:
                gpu_cid = int(gpu_val) if gpu_val is not None else None
            except (TypeError, ValueError):
                gpu_cid = None
            gpu_info = ComponentInfo(
                component_id=gpu_cid,
                name=str(row['gpu_name']),
                price=row['gpu_price'],
            )
        else:
            gpu_info = None

        builds.append(BuildResult(
            rank               = rank,
            predicted_score    = round(float(row['predicted_score']), 2),
            total_price        = round(float(row['total_price']), 2),
            budget_utilization = round(float(row['budget_utilization']), 3),
            purpose            = req.purpose,
            budget             = req.budget,

            cpu     = ci('cpu_name',     'cpu_price',     'cpu_id',     row),
            mb      = ci('mb_name',      'mb_price',      'mb_id',      row),
            ram     = ci('ram_name',     'ram_price',     'ram_id',     row),
            gpu     = gpu_info,
            storage = ci('storage_name', 'storage_price', 'storage_id', row),
            psu     = ci('psu_name',     'psu_price',     'psu_id',     row),
            case    = ci('case_name',    'case_price',    'case_id',    row),
            cooler  = ci('cooler_name',  'cooler_price',  'cooler_id',  row),

            cpu_cores       = int(row['cpu_cores']),
            cpu_boost_clock = float(row['cpu_boost_clock']),
            ram_total_gb    = int(row['ram_total_gb']),
            ram_ddr_gen     = int(row['ram_ddr_gen']),
            gpu_memory      = int(row['gpu_memory']) if has_gpu else None,
            storage_type    = str(row['storage_type']),
            psu_wattage     = int(row['psu_wattage']),
        ))

    brands_str = ', '.join(req.preferred_brands) if req.preferred_brands else 'любые'
    summary = (
        f"{req.purpose.upper()} | "
        f"бюджет ${req.budget:.0f} | "
        f"бренды: {brands_str} | "
        f"форм-фактор: {req.form_factor}"
    )

    return GenerateBuildsResponse(
        request_summary   = summary,
        builds            = builds,
        total_candidates  = len(top),
    )


# ── POST /replace_component ──────────────────────────────────

@router.post("/replace_component", response_model=ReplaceComponentResponse)
def replace_component(req: ReplaceComponentRequest):
    # 1. Получаем кандидатов из каталога с учётом ограничений
    try:
        constraints = dict(req.constraints or {})
        candidates  = search_components(req.category, constraints, limit=50)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка поиска: {e}")

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"Компоненты категории '{req.category}' по заданным ограничениям не найдены",
        )

    # 2. Ранжируем кандидатов CatBoost в контексте текущей сборки
    build_ctx = _flatten_build_context(req.build_context)
    try:
        scored = model_service.score_candidates(
            build_context = build_ctx,
            category      = req.category,
            candidates    = candidates,
            top_n         = req.top_n,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка ранжирования: {e}")

    SKIP = {'component_id', 'name', 'price', 'color', 'predicted_score'}
    options = [
        ReplacementOption(
            component_id    = item['component_id'],
            name            = item['name'],
            price           = float(item['price']),
            predicted_score = round(item['predicted_score'], 2),
            specs           = {k: v for k, v in item.items() if k not in SKIP},
        )
        for item in scored
    ]

    return ReplaceComponentResponse(category=req.category, options=options)


# ── GET /component/{component_id} ────────────────────────────

@router.get("/component/{component_id}", response_model=ComponentDetailsResponse)
def component_details(component_id: int):
    try:
        data = get_component_by_id(component_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {e}")

    if data is None:
        raise HTTPException(status_code=404, detail=f"Компонент #{component_id} не найден")

    return ComponentDetailsResponse(
        component_id = data['id'],
        name         = data['name'],
        price        = float(data['price']),
        category     = data['category'],
        color        = data.get('color'),
        specs        = data.get('specs', {}),
    )


# ── POST /search_catalog ─────────────────────────────────────

@router.post("/search_catalog", response_model=SearchCatalogResponse)
def search_catalog(req: SearchCatalogRequest):
    filters = dict(req.filters or {})
    if req.max_price is not None:
        filters['max_price'] = req.max_price
    if req.min_price is not None:
        filters['min_price'] = req.min_price

    try:
        rows = search_components(req.category, filters, limit=req.limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка поиска: {e}")

    items = [
        CatalogItem(
            component_id = r['component_id'],
            name         = r['name'],
            price        = float(r['price']),
            specs        = {k: v for k, v in r.items()
                            if k not in ('component_id', 'name', 'price', 'color')},
        )
        for r in rows
    ]

    return SearchCatalogResponse(category=req.category, total=len(items), items=items)


# ── POST /save_build ─────────────────────────────────────────

@router.post("/save_build", response_model=SaveBuildResponse)
def save_build_endpoint(req: SaveBuildRequest):
    try:
        build_id = db_save_build(
            user_id     = req.user_id,
            chat_id     = req.chat_id,
            purpose     = req.purpose,
            budget      = req.budget,
            total_price = req.total_price,
            components  = req.components,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения: {e}")

    return SaveBuildResponse(
        build_id = build_id,
        message  = f"Сборка #{build_id} сохранена",
    )


# ── GET /health ──────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status          = "ok" if model_service.is_loaded else "degraded",
        model_loaded    = model_service.is_loaded,
        catalogs_loaded = model_service.catalogs_loaded,
        model_target    = model_service.model_target if model_service.is_loaded else None,
    )


# ── GET /purposes ────────────────────────────────────────────

DESCRIPTIONS = {
    'gaming':      'Игровой ПК — упор на видеокарту',
    'workstation': 'Рабочая станция — упор на процессор и RAM',
    'rendering':   'Рендеринг / 3D — баланс GPU и CPU',
    'office':      'Офисный ПК — экономия без дискретной видеокарты',
    'streaming':   'Стриминг — мощный CPU + хорошая видеокарта',
}

@router.get("/purposes", response_model=list[PurposeInfo])
def purposes():
    return [
        PurposeInfo(
            key         = key,
            description = DESCRIPTIONS.get(key, ''),
            min_ram_gb  = profile.get('min_ram_gb', 8),
            prefer_gpu  = profile.get('prefer_gpu', False),
        )
        for key, profile in PURPOSE_PROFILES.items()
    ]
