"""种子依赖快照专项测试。

覆盖口径：
- 同一边界：种子状态与规则关联数来自同一次筛选扫描；
- 一份快照：同口径请求复用同一快照，分页不混入上一页规则计数；
- 按种子编号定位关联规则数与分页边界；
- 过期但仍被引用的历史种子：status_first 回查补项 / reference_first 保留+排除标注；
- 解析规则跨模块读取快照并回写，只重算快照涉及的字段抽取模板；
- 口径/数据变更触发重算与 stale 标记；
- 存量历史版本（v1）快照迁移兼容。
"""
from __future__ import annotations

import pytest

from app.services.parse_rule import ParseRuleService
from app.services.seed_config import SeedConfigService
from app.services.seed_dependency_snapshot import (
    SNAPSHOT_STATE_KEY,
    SNAPSHOT_VERSION,
    STRATEGY_REFERENCE_FIRST,
    STRATEGY_STATUS_FIRST,
    snapshot_service,
)
from app.store import store


@pytest.fixture(autouse=True)
def reset_snapshots():
    """每个用例前后从种子数据重建 store，保证与测试执行顺序无关。

    test_pipeline.py 的 setup_function 会清空全局 store 且不恢复，
    因此这里不依赖进入时的现场，直接按 SEED_ROWS 重建表与关系索引。
    """
    from app.seed import SEED_ROWS

    def _rebuild() -> None:
        store._tables = {name: [dict(row) for row in rows]
                         for name, rows in SEED_ROWS.items()}
        store._relations = {key: [] for key in list(store._relations.keys())}
        store._build_relations()
        store.set_pipeline_state(SNAPSHOT_STATE_KEY, {})

    _rebuild()
    yield
    _rebuild()


def _snapshot_count() -> int:
    return len(store.pipeline_state(SNAPSHOT_STATE_KEY) or {})


# ----------------------------------------------------------------------
# 同一边界：状态与规则关联数一次扫描同时确定
# ----------------------------------------------------------------------

def test_list_and_snapshot_share_same_boundary() -> None:
    seed_svc = SeedConfigService()
    items, total = seed_svc.list_entries(status="已生效", page=1, size=2)
    assert total >= 3
    snapshot = snapshot_service.list_snapshots()[0]
    # 列表行的关联规则数必须能在同口径快照内逐一对上
    count_by_code = {e["seed_code"]: e["rule_count"] for e in snapshot["entries"]}
    for row in items:
        assert row["_parse_rule_count"] == count_by_code[row["种子编号"]]
        assert row["_snapshot_id"] == snapshot["snapshot_id"]
        assert row["_page_boundary"]["page"] == 1


def test_rule_count_matches_relation_index() -> None:
    snapshot = snapshot_service.build_snapshot(strategy=STRATEGY_STATUS_FIRST)
    for entry in snapshot["entries"]:
        rels = store.get_relations(
            "seed_to_parse_rule", source_module="seed_config", source_id=entry["seed_id"])
        assert entry["rule_count"] == len(rels)
        assert entry["rule_count"] == len(entry["rule_ids"])


# ----------------------------------------------------------------------
# 一份快照：同口径复用，分页边界不串页
# ----------------------------------------------------------------------

def test_same_criteria_returns_single_snapshot() -> None:
    first = snapshot_service.build_snapshot(status="已生效", page=1, size=2)
    second = snapshot_service.build_snapshot(status="已生效", page=2, size=2)
    assert first["snapshot_id"] == second["snapshot_id"]
    assert _snapshot_count() == 1
    # 分页参数只改变分页边界，不改变口径哈希
    assert first["criteria_version"] == second["criteria_version"]
    assert second["boundary"]["page"] == 2
    assert second["boundary"]["page_start"] == 2


def test_different_criteria_creates_distinct_snapshot() -> None:
    a = snapshot_service.build_snapshot(status="已生效")
    b = snapshot_service.build_snapshot(status="草稿")
    assert a["snapshot_id"] != b["snapshot_id"]
    assert _snapshot_count() == 2


def test_pagination_does_not_mix_previous_page_counts() -> None:
    page1 = snapshot_service.build_snapshot(size=2, page=1)
    page2 = snapshot_service.build_snapshot(size=2, page=2)
    # 同一份快照内，按种子编号取规则数，两页结果必须完全一致
    assert page1["snapshot_id"] == page2["snapshot_id"]
    codes_page1 = {e["seed_code"] for e in page1["entries"][:2]}
    codes_page2 = {e["seed_code"] for e in page2["entries"][2:4]}
    assert codes_page1.isdisjoint(codes_page2)


# ----------------------------------------------------------------------
# 字段横向过滤、检索式查询、排序
# ----------------------------------------------------------------------

def test_field_filter_and_keyword_query() -> None:
    snapshot = snapshot_service.build_snapshot(
        filters={"种子优先级": "高"}, keyword="SEED-E001")
    codes = [e["seed_code"] for e in snapshot["entries"]]
    assert codes == ["SEED-E001"]


def test_sort_by_priority_and_order() -> None:
    asc = snapshot_service.build_snapshot(sort_by="种子优先级", sort_order="asc")
    ranks = [e["seed_code"] for e in asc["entries"]]
    # 低优先级种子（SEED-V005）排在高优先级之前
    assert ranks.index("SEED-V005") < ranks.index("SEED-E001")
    desc = snapshot_service.build_snapshot(sort_by="种子优先级", sort_order="desc")
    desc_codes = [e["seed_code"] for e in desc["entries"]]
    assert desc_codes.index("SEED-E001") < desc_codes.index("SEED-V005")


# ----------------------------------------------------------------------
# 按种子编号定位
# ----------------------------------------------------------------------

def test_locate_seed_returns_rule_count_and_page_boundary() -> None:
    snapshot = snapshot_service.build_snapshot(size=2, page=1)
    located = snapshot_service.locate_seed(snapshot["snapshot_id"], "SEED-E001")
    assert located is not None
    assert located["entry"]["rule_count"] == 2  # PARS-E001-A / PARS-E001-B
    assert located["page_index"] == 1
    assert located["boundary"]["size"] == 2


# ----------------------------------------------------------------------
# 版本冲突：过期但仍被引用的历史种子
# ----------------------------------------------------------------------

def test_expired_seed_inside_status_filter_is_marked() -> None:
    # 让过期种子 SEED-F006 仍被一条解析规则引用
    store.add_relation_if_absent(
        "seed_to_parse_rule", "seed_config", 6, "parse_rule", 6)
    snapshot = snapshot_service.build_snapshot(
        strategy=STRATEGY_STATUS_FIRST, status="已过期")
    entry = next(e for e in snapshot["entries"] if e["seed_code"] == "SEED-F006")
    assert entry["is_expired"] is True
    assert entry["rule_count"] == 1
    located = snapshot_service.locate_seed(snapshot["snapshot_id"], "SEED-F006")
    assert located["marked_expired_referenced"] is True


def test_status_first_backfills_referenced_expired_seed() -> None:
    # 仅筛选已生效种子；SEED-F006 已过期但挂着规则引用，必须作为补项出现
    store.add_relation_if_absent(
        "seed_to_parse_rule", "seed_config", 6, "parse_rule", 6)
    snapshot = snapshot_service.build_snapshot(
        strategy=STRATEGY_STATUS_FIRST, status="已生效")
    active_codes = {e["seed_code"] for e in snapshot["entries"]}
    supplement_codes = {e["seed_code"] for e in snapshot["history_supplements"]}
    assert "SEED-F006" not in active_codes
    assert "SEED-F006" in supplement_codes
    supplement = snapshot["history_supplements"][0]
    assert supplement["in_scope"] is False
    assert supplement["is_expired"] is True
    assert "回查补项" in supplement["retain_reason"]
    # 补项同样带可定位的分页边界
    assert supplement["page_index"] >= 1


def test_reference_first_keeps_history_and_excludes_no_rule_seeds() -> None:
    store.add_relation_if_absent(
        "seed_to_parse_rule", "seed_config", 6, "parse_rule", 6)
    snapshot = snapshot_service.build_snapshot(
        strategy=STRATEGY_REFERENCE_FIRST, status="已生效")
    # 先列出全部被引用种子，历史依赖全貌保留
    referenced_codes = {e["seed_code"] for e in snapshot["entries"]}
    assert "SEED-F006" in referenced_codes
    history = next(e for e in snapshot["entries"] if e["seed_code"] == "SEED-F006")
    assert history["in_scope"] is False
    assert "历史依赖保留" in (history["retain_reason"] or "")
    # 无规则种子留到第二轮排除并写明原因（SEED-F006 已有规则，不会被排除）
    excluded_codes = {e["seed_code"] for e in snapshot["excluded_no_rule"]}
    assert "SEED-F006" not in excluded_codes
    for excluded in snapshot["excluded_no_rule"]:
        assert excluded["rule_count"] == 0
        assert "无关联解析规则" in (excluded["exclude_reason"] or "")


def test_no_unreferenced_expired_seed_in_backfill() -> None:
    # SEED-F006 默认无规则引用：状态筛选已生效时不应出现任何补项
    snapshot = snapshot_service.build_snapshot(
        strategy=STRATEGY_STATUS_FIRST, status="已生效")
    assert snapshot["history_supplements"] == []


# ----------------------------------------------------------------------
# 跨模块链路：解析规则读取快照后同步回写
# ----------------------------------------------------------------------

def test_parse_rule_read_snapshot_and_write_back() -> None:
    parse_svc = ParseRuleService()
    snapshot = snapshot_service.build_snapshot(status="已生效")
    result = parse_svc.read_seed_dependency_snapshot(snapshot["snapshot_id"])
    assert result is not None
    assert set(result["rule_id_set"]).issubset({1, 2, 3, 5})
    refreshed = snapshot_service.get_snapshot(snapshot["snapshot_id"])
    assert refreshed["last_read_at"] is not None
    assert refreshed["consumption"]["read_by"][-1]["reader"] == "parse_rule"


def test_recompute_only_templates_in_snapshot_scope() -> None:
    parse_svc = ParseRuleService()
    # 只用 SEED-E001 的口径建快照，规则集合应只有 PARS-E001-A/B
    snapshot = snapshot_service.build_snapshot(keyword="SEED-E001")
    assert set(snapshot["rule_id_set"]) == {1, 2}
    # seed 关系按数字后缀匹配，模板2 默认挂在规则1下；显式补齐规则2的取数链路
    store.add_relation_if_absent(
        "parse_to_field", "parse_rule", 2, "field_extract", 2)
    result = parse_svc.recompute_fields_from_snapshot(snapshot["snapshot_id"])
    assert result["ok"] is True
    assert result["rule_count"] == 2
    # 只触碰快照规则集合内的模板（FIEL-E001-A/B），不重算其它种子模板
    assert set(result["field_template_ids"]) == {1, 2}
    refreshed = snapshot_service.get_snapshot(snapshot["snapshot_id"])
    recomputed_rules = {r["rule_id"] for r in refreshed["consumption"]["field_recompute"]}
    assert recomputed_rules == {1, 2}


def test_read_missing_snapshot_returns_none() -> None:
    assert ParseRuleService().read_seed_dependency_snapshot("not-exist") is None


# ----------------------------------------------------------------------
# 口径/数据变更：stale 与重算
# ----------------------------------------------------------------------

def test_seed_status_action_marks_snapshot_stale() -> None:
    seed_svc = SeedConfigService()
    snapshot = snapshot_service.build_snapshot(status="已生效")
    seed_svc.run_action(1, "标记过期")
    refreshed = snapshot_service.get_snapshot(snapshot["snapshot_id"])
    assert refreshed["stale"] is True


def test_parse_rule_action_marks_snapshot_stale() -> None:
    parse_svc = ParseRuleService()
    snapshot = snapshot_service.build_snapshot()
    parse_svc.run_action(1, "标记废弃")
    refreshed = snapshot_service.get_snapshot(snapshot["snapshot_id"])
    assert refreshed["stale"] is True


def test_recalc_uses_original_criteria_and_new_data() -> None:
    seed_svc = SeedConfigService()
    snapshot = snapshot_service.build_snapshot(status="已生效")
    original_id = snapshot["snapshot_id"]
    seed_svc.run_action(1, "标记过期")
    recalc = snapshot_service.recalc_snapshot(original_id)
    assert recalc["snapshot_id"] == original_id
    assert recalc["criteria_version"] == snapshot["criteria_version"]
    assert recalc["stale"] is False
    codes = {e["seed_code"] for e in recalc["entries"]}
    assert "SEED-E001" not in codes


def test_force_rebuild_same_snapshot_id() -> None:
    snapshot = snapshot_service.build_snapshot(status="草稿")
    rebuilt = snapshot_service.build_snapshot(status="草稿", force=True)
    assert rebuilt["snapshot_id"] == snapshot["snapshot_id"]
    assert rebuilt["recalculated_at"] >= snapshot["recalculated_at"]


# ----------------------------------------------------------------------
# 历史版本迁移兼容
# ----------------------------------------------------------------------

def test_legacy_v1_snapshot_migrated_on_read() -> None:
    legacy = {
        "snapshot_version": 1,
        "snapshot_id": "legacy-case-1",
        "created_at": "2026-10-01T00:00:00Z",
        "scope": {"status": "已生效"},
        "seeds": [{"seed_id": 1, "seed_code": "SEED-E001", "rule_ids": [1, 2]}],
    }
    state = store.pipeline_state(SNAPSHOT_STATE_KEY)
    state["legacy-case-1"] = legacy
    store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)

    migrated = snapshot_service.get_snapshot("legacy-case-1")
    assert migrated["snapshot_version"] == SNAPSHOT_VERSION
    assert migrated["migrated_from"] == 1
    entry = migrated["entries"][0]
    assert entry["rule_count"] == 2
    assert entry["rule_ids"] == [1, 2]
    assert "boundary" in migrated and "criteria_version" in migrated
    # 迁移结果已持久化，再次读取不再重复迁移
    again = snapshot_service.get_snapshot("legacy-case-1")
    assert "migrated_from" in again and again["snapshot_version"] == SNAPSHOT_VERSION


def test_list_snapshots_migrates_all_legacy_versions() -> None:
    state = store.pipeline_state(SNAPSHOT_STATE_KEY)
    state["legacy-list-1"] = {
        "snapshot_version": 1,
        "snapshot_id": "legacy-list-1",
        "seeds": [],
    }
    store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)
    snapshots = snapshot_service.list_snapshots()
    assert all(s["snapshot_version"] == SNAPSHOT_VERSION for s in snapshots)


# ----------------------------------------------------------------------
# 幂等关系写入
# ----------------------------------------------------------------------

def test_add_relation_if_absent_is_idempotent() -> None:
    before = len(store.get_relations("seed_to_parse_rule"))
    created = store.add_relation_if_absent(
        "seed_to_parse_rule", "seed_config", 1, "parse_rule", 1)
    again = store.add_relation_if_absent(
        "seed_to_parse_rule", "seed_config", 1, "parse_rule", 1)
    after = len(store.get_relations("seed_to_parse_rule"))
    assert created is False  # seed 1 -> rule 1 在示例数据里已存在
    assert again is False
    assert before == after
