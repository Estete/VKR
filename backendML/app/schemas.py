"""
app/schemas.py — Pydantic-схемы запроса и ответа API.
"""
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


PURPOSE_VALUES      = Literal['gaming', 'workstation', 'rendering', 'office', 'streaming']
FORM_FACTOR_VALUES  = Literal['any', 'compact', 'standard']
CATEGORY_VALUES     = Literal[
    'cpu', 'motherboard', 'memory', 'video_card',
    'storage', 'power_supply', 'case', 'cooler',
]


# ══════════════════════════════════════════════════════════════
#  generate_builds
# ══════════════════════════════════════════════════════════════

class GenerateBuildsRequest(BaseModel):
    budget:           float = Field(..., gt=0, le=100_000,
                                   description="Максимальный бюджет в USD")
    purpose:          PURPOSE_VALUES = Field(...,
                                   description="Назначение сборки")
    preferred_brands: Optional[list[str]] = Field(
        default=[],
        description="Предпочитаемые бренды: AMD, Intel, Nvidia")
    form_factor:      FORM_FACTOR_VALUES = Field(
        default='any',
        description="Форм-фактор: any / compact / standard")
    top_n:            int = Field(default=3, ge=1, le=10,
                                  description="Сколько сборок вернуть")
    min_ram_gb:       Optional[int]  = Field(default=None, ge=0)
    prefer_ssd:       Optional[bool] = Field(default=None)

    @field_validator('preferred_brands')
    @classmethod
    def validate_brands(cls, brands):
        allowed = {'AMD', 'Intel', 'Nvidia'}
        normalized = []
        for b in (brands or []):
            matched = next((a for a in allowed if a.lower() == b.strip().lower()), None)
            if not matched:
                raise ValueError(f"Неизвестный бренд: {b}. Допустимо: {', '.join(allowed)}")
            normalized.append(matched)
        return normalized


class ComponentInfo(BaseModel):
    component_id: Optional[int] = None
    name:  str
    price: float


class BuildResult(BaseModel):
    rank:               int
    predicted_score:    float
    total_price:        float
    budget_utilization: float
    purpose:            str
    budget:             float

    cpu:     ComponentInfo
    mb:      ComponentInfo
    ram:     ComponentInfo
    gpu:     Optional[ComponentInfo]
    storage: ComponentInfo
    psu:     ComponentInfo
    case:    ComponentInfo
    cooler:  ComponentInfo

    cpu_cores:       int
    cpu_boost_clock: float
    ram_total_gb:    int
    ram_ddr_gen:     int
    gpu_memory:      Optional[int]
    storage_type:    str
    psu_wattage:     int


class GenerateBuildsResponse(BaseModel):
    request_summary: str
    builds:          list[BuildResult]
    total_candidates: int   # сколько кандидатов прошло совместимость


# ══════════════════════════════════════════════════════════════
#  replace_component
# ══════════════════════════════════════════════════════════════

class ReplaceComponentRequest(BaseModel):
    build_context: BuildResult = Field(..., description="Текущая сборка целиком (из generate_builds)")
    category:      CATEGORY_VALUES = Field(..., description="Категория заменяемого компонента")
    constraints:   Optional[dict[str, Any]] = Field(
        default={},
        description="Ограничения: max_price, min_*, etc.",
        examples=[{"max_price": 400}],
    )
    top_n: int = Field(default=3, ge=1, le=10)


class ReplacementOption(BaseModel):
    component_id:    int
    name:            str
    price:           float
    predicted_score: float = 0.0
    specs:           dict[str, Any]


class ReplaceComponentResponse(BaseModel):
    category: str
    options:  list[ReplacementOption]


# ══════════════════════════════════════════════════════════════
#  get_component_details
# ══════════════════════════════════════════════════════════════

class ComponentDetailsResponse(BaseModel):
    component_id: int
    name:         str
    price:        float
    category:     str
    color:        Optional[str]
    specs:        dict[str, Any]   # все характеристики, специфичные для категории


# ══════════════════════════════════════════════════════════════
#  search_catalog
# ══════════════════════════════════════════════════════════════

class SearchCatalogRequest(BaseModel):
    category:  str = Field(..., description="Категория компонента (cpu, video_card, …)")
    max_price: Optional[float] = Field(default=None, gt=0)
    min_price: Optional[float] = Field(default=None, ge=0)
    filters:   Optional[dict[str, Any]] = Field(
        default={},
        description="Доп. фильтры по характеристикам (min_core_count, chipset, …)",
    )
    limit:     int = Field(default=10, ge=1, le=50)


class CatalogItem(BaseModel):
    component_id: int
    name:         str
    price:        float
    specs:        dict[str, Any]


class SearchCatalogResponse(BaseModel):
    category: str
    total:    int
    items:    list[CatalogItem]


# ══════════════════════════════════════════════════════════════
#  save_build
# ══════════════════════════════════════════════════════════════

class SaveBuildRequest(BaseModel):
    user_id:    int
    chat_id:    Optional[int] = None
    purpose:    str
    budget:     float
    total_price: float
    components: dict[CATEGORY_VALUES, int] = Field(
        ...,
        description="Маппинг категория → component_id",
        examples=[{"cpu": 12, "motherboard": 34, "memory": 56}],
    )


class SaveBuildResponse(BaseModel):
    build_id: int
    message:  str


# ══════════════════════════════════════════════════════════════
#  /health  /purposes
# ══════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status:          Literal['ok', 'degraded']
    model_loaded:    bool
    catalogs_loaded: bool
    model_target:    Optional[str]


class PurposeInfo(BaseModel):
    key:         str
    description: str
    min_ram_gb:  int
    prefer_gpu:  bool
