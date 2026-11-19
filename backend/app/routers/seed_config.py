"""种子管理接口：维护采集种子，覆盖提交校验、标记生效、标记过期等动作。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.seed_config import SeedConfigService
from app.services.seed_dependency_snapshot import (
    SORTABLE_FIELDS,
    STRATEGY_STATUS_FIRST,
    VALID_STRATEGIES,
    snapshot_service,
)

router = APIRouter(prefix="/api/seed_config", tags=["种子管理"])

service = SeedConfigService()

LIST_FIELDS = ["种子编号", "目标URL模式", "列表页XPath", "详情页XPath", "分页参数", "排序字段", "去重键", "种子优先级"]
STATUSES = ["草稿", "待校验", "已生效", "已过期"]
SORTABLE = list(SORTABLE_FIELDS)


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按种子编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已生效、已过期"),
    sort_by: str | None = Query(default=None, description="排序字段：种子编号/种子优先级/排序字段"),
    sort_order: str = Query(default="asc", description="asc 或 desc"),
    strategy: str = Query(default=STRATEGY_STATUS_FIRST, description="快照口径：status_first / reference_first"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、种子编号关键字与状态过滤种子管理列表。

    列表与依赖快照共用筛选边界，返回行附带快照编号与分页边界，
    关联规则数与种子状态来自同一次扫描。
    """
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"未知快照口径策略：{strategy}")
    if sort_by and sort_by not in SORTABLE:
        raise HTTPException(status_code=400, detail=f"排序字段仅支持：{'、'.join(SORTABLE)}")
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status,
        page=page, size=size, sort_by=sort_by, sort_order=sort_order, strategy=strategy)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="seed_config.csv"'})


@router.get("/dependency_snapshots")
def list_dependency_snapshots() -> dict:
    """列出种子管理一侧形成的全部依赖快照（含历史版本迁移结果）。"""
    return {"items": snapshot_service.list_snapshots()}


@router.post("/dependency_snapshots")
def build_dependency_snapshot(request: Request, payload: dict | None = None) -> dict:
    """按当前筛选请求生成（或复用同口径的）一份种子依赖快照。

    同一筛选口径只返回一份可定位快照；force=true 时强制重算。
    """
    payload = payload or {}
    strategy = str(payload.get("strategy") or STRATEGY_STATUS_FIRST)
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"未知快照口径策略：{strategy}")
    sort_by = payload.get("sort_by")
    if sort_by and sort_by not in SORTABLE:
        raise HTTPException(status_code=400, detail=f"排序字段仅支持：{'、'.join(SORTABLE)}")
    filters = {
        key: str(value).strip()
        for key, value in (payload.get("filters") or {}).items()
        if key in LIST_FIELDS and str(value).strip()
    }
    snapshot = snapshot_service.build_snapshot(
        filters=filters,
        keyword=(payload.get("keyword") or None),
        status=(payload.get("status") or None),
        sort_by=sort_by,
        sort_order=str(payload.get("sort_order") or "asc"),
        strategy=strategy,
        page=int(payload.get("page") or 1),
        size=int(payload.get("size") or 20),
        force=bool(payload.get("force")),
    )
    return {"ok": True, "snapshot": snapshot}


@router.get("/dependency_snapshots/{snapshot_id}")
def get_dependency_snapshot(snapshot_id: str) -> dict:
    """读取单份依赖快照；不存在时返回 404。"""
    snapshot = snapshot_service.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"依赖快照 {snapshot_id} 不存在")
    return snapshot


@router.post("/dependency_snapshots/{snapshot_id}/recalc")
def recalc_dependency_snapshot(snapshot_id: str, payload: dict | None = None) -> dict:
    """口径变更后按原筛选条件重新计算快照。"""
    payload = payload or {}
    snapshot = snapshot_service.recalc_snapshot(
        snapshot_id,
        page=payload.get("page"),
        size=payload.get("size"),
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"依赖快照 {snapshot_id} 不存在")
    return {"ok": True, "snapshot": snapshot}


@router.get("/dependency_snapshots/{snapshot_id}/locate")
def locate_seed_in_snapshot(snapshot_id: str, seed_code: str = Query(..., description="按种子编号定位")) -> dict:
    """按种子编号定位快照内关联规则数与分页边界。"""
    located = snapshot_service.locate_seed(snapshot_id, seed_code)
    if located is None:
        raise HTTPException(
            status_code=404,
            detail=f"快照 {snapshot_id} 中未找到种子 {seed_code}")
    return located


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条采集种子明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集种子 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条采集种子，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="采集种子已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条采集种子执行提交校验、标记生效、标记过期；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/parse_rules")
def get_linked_parse_rules(entry_id: int) -> dict:
    """获取种子关联的解析规则。"""
    return {"items": service.get_linked_parse_rules(entry_id)}


@router.post("/{entry_id}/bind_source")
def bind_source(entry_id: int, payload: dict[str, object]) -> dict:
    """绑定种子到采集源。"""
    source_id = payload.get("source_id")
    if source_id is None:
        return {"ok": False, "error": "缺少source_id"}
    return service.bind_source(entry_id, int(source_id))
