"""种子管理业务规则：URL模板校验、XPath合法性检查、跨模块绑定。"""
from __future__ import annotations

import re
from typing import Any

from app.services.seed_dependency_snapshot import (
    STRATEGY_STATUS_FIRST,
    select_boundary_rows,
    snapshot_service,
)
from app.store import store

MODULE = "seed_config"
LIST_FIELDS = ["种子编号", "目标URL模式", "列表页XPath", "详情页XPath", "分页参数", "排序字段", "去重键", "种子优先级"]
REQUIRED_FIELDS = ["种子编号", "目标URL模式", "列表页XPath"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已过期"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记过期": "已过期"}
NEGATIVE_ACTIONS = []


def _validate_xpath(xpath: str) -> tuple[bool, str]:
    """基础XPath语法校验：检查是否以/或//开头，括号是否匹配。"""
    if not xpath or not isinstance(xpath, str):
        return False, "XPath为空"
    stripped = xpath.strip()
    if not stripped.startswith(("/", "//")):
        return False, "XPath必须以/或//开头"
    if stripped.count("(") != stripped.count(")"):
        return False, "XPath括号不匹配"
    return True, "语法合法"


def _validate_url_pattern(pattern: str) -> tuple[bool, str]:
    """校验URL模式是否包含通配符或分页参数占位。"""
    if not pattern or not isinstance(pattern, str):
        return False, "URL模式为空"
    if not pattern.startswith(("http://", "https://")):
        return False, "URL模式必须以http://或https://开头"
    # 检查是否包含至少一个可替换参数（{page}、{id}、{}等）
    has_param = bool(re.search(r"\{[^}]*\}", pattern))
    return True, "包含分页参数" if has_param else "固定URL"


class SeedConfigService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        sort_by: str | None = None,
        sort_order: str = "asc",
        strategy: str = STRATEGY_STATUS_FIRST,
    ) -> tuple[list[dict[str, Any]], int]:
        # 列表与种子依赖快照共用同一筛选边界：状态与规则关联数一次扫描同时确定，
        # 不再逐页扫描全部解析规则，也不会让上一页的规则计数混入当前页。
        snapshot = snapshot_service.build_snapshot(
            filters=filters, keyword=keyword, status=status,
            sort_by=sort_by, sort_order=sort_order, strategy=strategy,
            page=page, size=size)
        # 快照条目按同一索引给出规则关联数，直接按 id 取用，避免重复扫描关系表。
        count_by_id = {
            entry["seed_id"]: entry["rule_count"]
            for entry in snapshot["entries"] + snapshot.get("history_supplements", [])
        }
        start = (page - 1) * size
        rows = select_boundary_rows(
            filters=filters, keyword=keyword, status=status,
            sort_by=sort_by, sort_order=sort_order)
        total = len(rows)
        enriched = []
        for row in rows[start:start + size]:
            d = dict(row)
            d["_parse_rule_count"] = count_by_id.get(int(d["id"]), 0)
            d["_cursor_count"] = len(store.get_relations("seed_to_cursor", source_module=MODULE, source_id=d["id"]))
            d["_snapshot_id"] = snapshot["snapshot_id"]
            d["_snapshot_version"] = snapshot["snapshot_version"]
            d["_page_boundary"] = snapshot["boundary"]
            enriched.append(d)
        return enriched, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        d = dict(entry)
        d["_parse_rule_count"] = len(store.get_relations("seed_to_parse_rule", source_module=MODULE, source_id=entry_id))
        d["_cursor_count"] = len(store.get_relations("seed_to_cursor", source_module=MODULE, source_id=entry_id))
        return d

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 自动校验
        url_ok, url_msg = _validate_url_pattern(str(entry.get("目标URL模式") or ""))
        xpath_ok, xpath_msg = _validate_xpath(str(entry.get("列表页XPath") or ""))
        entry["_validation"] = {"url_ok": url_ok, "url_msg": url_msg, "xpath_ok": xpath_ok, "xpath_msg": xpath_msg}
        rows.append(entry)
        # 如果指定了关联采集源，建立关系
        source_id = values.get("关联采集源ID")
        if source_id:
            try:
                sid = int(source_id)
                store.add_relation("source_to_seed", "crawl_source", sid, MODULE, entry["id"])
            except (ValueError, TypeError):
                pass
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集种子 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于种子管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "提交校验":
            url_ok, url_msg = _validate_url_pattern(str(entry.get("目标URL模式") or ""))
            xpath_ok, xpath_msg = _validate_xpath(str(entry.get("列表页XPath") or ""))
            entry["_validation"] = {"url_ok": url_ok, "url_msg": url_msg, "xpath_ok": xpath_ok, "xpath_msg": xpath_msg}
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 状态口径变更：涉及该种子的依赖快照标记为待重算，避免过期种子与关联数错位。
        snapshot_service.mark_stale_for_seed(entry_id)
        return entry, f"采集种子已{action}"

    def get_linked_parse_rules(self, entry_id: int) -> list[dict[str, Any]]:
        rels = store.get_relations("seed_to_parse_rule", source_module=MODULE, source_id=entry_id)
        rules = []
        for rel in rels:
            r = store.find("parse_rule", rel["target_id"])
            if r:
                rules.append(dict(r))
        return rules

    def bind_source(self, entry_id: int, source_id: int) -> dict[str, Any]:
        """将种子绑定到采集源。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {"ok": False, "error": "种子不存在"}
        store.add_relation("source_to_seed", "crawl_source", source_id, MODULE, entry_id)
        return {"ok": True, "seed_id": entry_id, "source_id": source_id}
