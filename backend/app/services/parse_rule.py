"""解析规则业务规则：选择器语法校验、版本管理、字段抽取链生成。"""
from __future__ import annotations

import re
from typing import Any

from app.services.seed_dependency_snapshot import snapshot_service
from app.store import store

MODULE = "parse_rule"
LIST_FIELDS = ["规则编号", "目标字段名", "选择器类型", "选择器表达式", "提取方式", "默认值", "正则过滤", "规则版本"]
REQUIRED_FIELDS = ["规则编号", "目标字段名", "选择器类型"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


def _validate_selector(selector_type: str, expression: str) -> tuple[bool, str]:
    """校验选择器表达式与类型是否匹配。"""
    if not expression or not isinstance(expression, str):
        return False, "选择器表达式为空"
    t = (selector_type or "").lower()
    if t == "xpath":
        if not expression.startswith(("/", "//")):
            return False, "XPath必须以/开头"
        if expression.count("(") != expression.count(")"):
            return False, "括号不匹配"
        return True, "XPath合法"
    if t == "css":
        if not re.search(r"[.#\[\]]", expression) and not expression.isalnum():
            return False, "CSS选择器格式异常"
        return True, "CSS选择器合法"
    if t == "jsonpath":
        if not expression.startswith("$"):
            return False, "JSONPath必须以$开头"
        return True, "JSONPath合法"
    if t == "regex":
        try:
            re.compile(expression)
            return True, "正则合法"
        except re.error as exc:
            return False, f"正则错误: {exc}"
    return True, "未知类型跳过校验"


class ParseRuleService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        enriched = []
        for row in rows[start:start + size]:
            d = dict(row)
            d["_field_count"] = len(store.get_relations("parse_to_field", source_module=MODULE, source_id=d["id"]))
            enriched.append(d)
        return enriched, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        d = dict(entry)
        d["_field_count"] = len(store.get_relations("parse_to_field", source_module=MODULE, source_id=entry_id))
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
        # 选择器校验
        ok, msg = _validate_selector(entry.get("选择器类型"), entry.get("选择器表达式") or "")
        entry["_selector_check"] = {"ok": ok, "msg": msg}
        rows.append(entry)
        # 绑定种子（幂等，避免重复登记造成关系重复计数）
        seed_id = values.get("关联种子ID")
        if seed_id:
            try:
                sid = int(seed_id)
                store.add_relation_if_absent("seed_to_parse_rule", "seed_config", sid, MODULE, entry["id"])
            except (ValueError, TypeError):
                pass
        # 规则侧口径变更：既有依赖快照全部标记待重算
        snapshot_service.mark_all_stale("解析规则登记导致关联关系变更")
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"解析规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于解析规则可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "提交校验":
            ok, msg = _validate_selector(entry.get("选择器类型"), entry.get("选择器表达式") or "")
            entry["_selector_check"] = {"ok": ok, "msg": msg}
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 规则口径（状态）变更：关联数口径可能变化，标记依赖快照待重算
        snapshot_service.mark_all_stale(f"解析规则 {entry_id} 状态变更为{target}")
        return entry, f"解析规则已{action}"

    def generate_field_extracts(self, entry_id: int) -> list[dict[str, Any]]:
        """基于解析规则生成字段抽取模板，写入 field_extract 模块。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return []
        from app.services.field_extract import FieldExtractService
        svc = FieldExtractService()
        templates = []
        field_name = str(entry.get("目标字段名") or "")
        selector = str(entry.get("选择器表达式") or "")
        extract_type = str(entry.get("提取方式") or "text")
        payload = {
            "模板编号": f"GEN-{entry.get('规则编号')}-FE",
            "关联种子": entry.get("规则编号"),
            "字段名": field_name,
            "选择器": selector,
            "精炼规则": entry.get("正则过滤") or "",
            "输出类型": extract_type,
            "可为空": "否",
            "抽取优先级": "10",
        }
        new_entry, _ = svc.create_entry(payload)
        if new_entry:
            store.add_relation("parse_to_field", MODULE, entry_id, "field_extract", new_entry["id"])
            templates.append(new_entry)
        return templates

    # ------------------------------------------------------------------
    # 种子依赖快照：解析规则按跨模块链路读取快照，读取后同步回写
    # ------------------------------------------------------------------
    def read_seed_dependency_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """读取种子管理一侧形成的依赖快照，并同步回写读取痕迹。"""
        snapshot = snapshot_service.get_snapshot(snapshot_id)
        if snapshot is None:
            return None
        read_record = snapshot_service.mark_read(snapshot_id, reader=MODULE)
        # 只取本快照涉及的规则编号集合，规则侧不重扫全量规则
        return {
            "snapshot_id": snapshot_id,
            "criteria_version": snapshot["criteria_version"],
            "boundary": snapshot["boundary"],
            "rule_id_set": list(snapshot["rule_id_set"]),
            "rule_index": snapshot["rule_index"],
            "entries": snapshot["entries"],
            "history_supplements": snapshot.get("history_supplements", []),
            "read_record": read_record,
            "stale": snapshot.get("stale", False),
        }

    def recompute_fields_from_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """只重算快照规则编号集合涉及的字段抽取模板，并把结果回写快照。

        沿解析规则取数链路（parse_rule -> field_extract）定位模板，
        快照之外的规则与模板完全不参与重算。
        """
        snapshot = snapshot_service.get_snapshot(snapshot_id)
        if snapshot is None:
            return {"ok": False, "error": f"依赖快照 {snapshot_id} 不存在"}
        rule_ids = [int(rid) for rid in snapshot["rule_id_set"]]
        from app.services.field_extract import FieldExtractService
        fe_svc = FieldExtractService()
        recomputed: list[dict[str, Any]] = []
        touched_template_ids: list[int] = []
        for rule_id in rule_ids:
            rule = store.find(MODULE, rule_id)
            if rule is None:
                continue
            field_rels = store.get_relations(
                "parse_to_field", source_module=MODULE, source_id=rule_id)
            template_ids = [int(rel["target_id"]) for rel in field_rels]
            # 没有既有模板时，按规则补生成（仍限定在快照规则集合内）
            if not template_ids:
                generated = self.generate_field_extracts(rule_id)
                template_ids = [int(item["id"]) for item in generated]
            for template_id in template_ids:
                template = store.find("field_extract", template_id)
                if template is None:
                    continue
                sample_html = (
                    "<div class='item'><h1 class='title'>快照重算示例</h1>"
                    "<span class='price'>188.80</span></div>"
                )
                extract_result = fe_svc.extract_fields(template_id, sample_html)
                recomputed.append({
                    "rule_id": rule_id,
                    "rule_code": rule.get("规则编号"),
                    "template_id": template_id,
                    "template_code": template.get("模板编号"),
                    "field": template.get("字段名"),
                    "extract": extract_result,
                })
                touched_template_ids.append(template_id)
            # 回写每条规则的重算记录
            snapshot_service.record_field_recompute(
                snapshot_id, rule_id=rule_id, field_template_ids=template_ids, reader=MODULE)
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "rule_count": len(rule_ids),
            "rule_ids": rule_ids,
            "field_template_count": len(touched_template_ids),
            "field_template_ids": touched_template_ids,
            "recomputed": recomputed,
        }
