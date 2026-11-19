"""种子依赖快照服务（种子管理一侧形成，解析规则跨模块读取）。

设计要点（对应需求口径）：

1. 同一边界：种子状态筛选与规则关联数在同一次筛选扫描内完成，共用
   ``select_boundary_rows`` 与一次性构建的 ``seed -> 规则`` 索引，
   避免逐页扫描全部解析规则，也杜绝两边口径不一致漏掉刚过期的规则。
2. 一份快照：筛选条件（字段过滤/关键字/状态/排序）决定口径并参与哈希，
   同一筛选请求只返回一份可按种子编号定位的快照；分页参数只决定分页边界，
   不会让上一页的规则计数混进当前页。
3. 版本冲突两种口径：
   - ``status_first``（默认）：先按状态筛选缩小扫描范围，再回查仍被规则
     引用的历史（过期）种子作为补项；
   - ``reference_first``：先聚合引用保留历史依赖全貌，无规则种子留到
     第二轮排除并写明排除原因。
4. 口径变更：条件哈希变化即生成新快照，已存在的快照可强制重算；种子/规则
   侧的数据变更会把相关快照标记为 stale。
5. 历史兼容：读取时自动把 v1 存量快照迁移到当前 SNAPSHOT_VERSION。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.store import store

SNAPSHOT_STATE_KEY = "seed_dependency_snapshots"
SNAPSHOT_VERSION = 2
SEED_MODULE = "seed_config"
PARSE_MODULE = "parse_rule"
RELATION = "seed_to_parse_rule"
EXPIRED_SEED_STATUS = "已过期"

# 采集种子优先处理顺序待判定：当前仅给出口头映射，快照中明确标注待判定，
# 业务侧确认顺序后只需调整本常量与排序映射。
PRIORITY_DECISION = "待判定"
PRIORITY_RANK = {"高": 3, "中": 2, "低": 1}

# 版本冲突处理口径
STRATEGY_STATUS_FIRST = "status_first"
STRATEGY_REFERENCE_FIRST = "reference_first"
VALID_STRATEGIES = (STRATEGY_STATUS_FIRST, STRATEGY_REFERENCE_FIRST)

SORTABLE_FIELDS = ("种子编号", "种子优先级", "排序字段")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def select_boundary_rows(
    *,
    filters: dict[str, str] | None = None,
    keyword: str | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
) -> list[dict[str, Any]]:
    """统一的种子筛选边界：字段横向过滤、检索式查询、状态过滤与排序。

    种子管理列表与依赖快照都必须经过本函数，保证状态口径完全一致。
    分页不在此处处理——分页属于快照的分页边界，不属于筛选口径。
    """
    rows = [dict(row) for row in store.rows(SEED_MODULE)]
    for field, expected in (filters or {}).items():
        rows = [row for row in rows
                if expected.casefold() in str(row.get(field, "")).casefold()]
    if keyword:
        kw = keyword.casefold()
        rows = [row for row in rows
                if kw in str(row.get("种子编号", "")).casefold()]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return _sort_rows(rows, sort_by, sort_order)


def _sort_rows(rows: list[dict[str, Any]], sort_by: str | None, sort_order: str) -> list[dict[str, Any]]:
    reverse = (sort_order or "asc").lower() == "desc"
    if not sort_by:
        return sorted(rows, key=lambda r: int(r.get("id", 0)), reverse=reverse)
    if sort_by == "种子优先级":
        return sorted(
            rows,
            key=lambda r: PRIORITY_RANK.get(str(r.get("种子优先级") or ""), 0),
            reverse=reverse,
        )

    def _key(row: dict[str, Any]) -> str:
        return str(row.get(sort_by) or "")

    return sorted(rows, key=_key, reverse=reverse)


class SeedDependencySnapshotService:
    """种子依赖快照的构建、定位、迁移与跨模块回写。"""

    # ------------------------------------------------------------------
    # 存储与迁移
    # ------------------------------------------------------------------
    def _state(self) -> dict[str, dict[str, Any]]:
        state = store.pipeline_state(SNAPSHOT_STATE_KEY)
        if not isinstance(state, dict):
            state = {}
            store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)
        return state

    def list_snapshots(self) -> list[dict[str, Any]]:
        """列出全部快照，读取时顺带完成存量历史版本迁移并持久化。"""
        state = self._state()
        migrated = []
        changed = False
        for snapshot_id, raw in list(state.items()):
            new_raw, did_migrate = self._migrate_if_needed(raw)
            if did_migrate:
                state[snapshot_id] = new_raw
                changed = True
            migrated.append(new_raw)
        if changed:
            store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)
        return sorted(migrated, key=lambda s: str(s.get("created_at", "")), reverse=True)

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        state = self._state()
        raw = state.get(snapshot_id)
        if raw is None:
            return None
        new_raw, did_migrate = self._migrate_if_needed(raw)
        if did_migrate:
            state[snapshot_id] = new_raw
            store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)
        return new_raw

    def _save(self, snapshot: dict[str, Any]) -> None:
        state = self._state()
        state[snapshot["snapshot_id"]] = snapshot
        store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)

    def _migrate_if_needed(self, raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """把历史版本快照迁移到当前版本；已是当前版本时原样返回。"""
        version = int(raw.get("snapshot_version", 1) or 1)
        if version == SNAPSHOT_VERSION:
            return raw, False
        if version > SNAPSHOT_VERSION:
            # 未来版本不破坏，原样保留，交由上层决定。
            return raw, False

        # v1 -> v2：v1 只记录 seed_id/seed_code/rule_ids，缺少口径与分页边界。
        migrated_entries: list[dict[str, Any]] = []
        rule_id_set: set[int] = set()
        for idx, old in enumerate(raw.get("seeds", [])):
            seed_id = int(old["seed_id"])
            seed = store.find(SEED_MODULE, seed_id)
            rule_ids = [int(rid) for rid in old.get("rule_ids", [])]
            rule_id_set.update(rule_ids)
            migrated_entries.append({
                "seed_id": seed_id,
                "seed_code": old.get("seed_code") or (seed or {}).get("种子编号", ""),
                "status": (seed or {}).get("status"),
                "is_expired": (seed or {}).get("status") == EXPIRED_SEED_STATUS,
                "entry_type": "active",
                "in_scope": True,
                "rule_ids": rule_ids,
                "rule_count": len(rule_ids),
                "rule_codes": self._rule_codes(rule_ids),
                "page_index": idx // 20 + 1,
                "page_offset": idx % 20,
                "exclude_reason": None,
                "retain_reason": None,
            })
        scope = raw.get("scope") or {}
        snapshot = {
            "snapshot_id": str(raw.get("snapshot_id") or f"legacy-v{version}"),
            "snapshot_version": SNAPSHOT_VERSION,
            "created_at": raw.get("created_at") or _now_iso(),
            "recalculated_at": _now_iso(),
            "criteria_version": self._criteria_hash({
                "filters": {}, "keyword": None, "status": scope.get("status"),
                "sort_by": None, "sort_order": "asc",
                "strategy": STRATEGY_STATUS_FIRST,
            }),
            "criteria": {
                "filters": {},
                "keyword": None,
                "status": scope.get("status"),
                "sort_by": None,
                "sort_order": "asc",
                "strategy": STRATEGY_STATUS_FIRST,
            },
            "boundary": {
                "page": 1, "size": 20, "total": len(migrated_entries),
                "pages": max(1, (len(migrated_entries) + 19) // 20),
                "page_start": 0, "page_end": min(20, len(migrated_entries)),
            },
            "entries": migrated_entries,
            "history_supplements": [],
            "excluded_no_rule": [],
            "rule_index": self._build_rule_index_view(rule_id_set),
            "rule_id_set": sorted(rule_id_set),
            "consumption": {"read_by": [], "field_recompute": []},
            "last_read_at": None,
            "priority_decision": PRIORITY_DECISION,
            "stale": False,
            "migrated_from": version,
        }
        return snapshot, True

    # ------------------------------------------------------------------
    # 规则索引：一次扫描替代逐页全量规则扫描
    # ------------------------------------------------------------------
    def _build_seed_rule_index(self) -> dict[int, list[dict[str, Any]]]:
        """单次扫描 seed_to_parse_rule 关系，构建 seed_id -> 规则摘要索引。"""
        index: dict[int, list[dict[str, Any]]] = {}
        for rel in store.get_relations(RELATION, source_module=SEED_MODULE):
            rule = store.find(PARSE_MODULE, int(rel["target_id"]))
            if rule is None:
                continue
            index.setdefault(int(rel["source_id"]), []).append({
                "rule_id": int(rule["id"]),
                "rule_code": str(rule.get("规则编号") or ""),
                "rule_status": rule.get("status"),
            })
        for items in index.values():
            items.sort(key=lambda item: item["rule_id"])
        return index

    def _rule_codes(self, rule_ids: list[int]) -> list[str]:
        codes = []
        for rule_id in rule_ids:
            rule = store.find(PARSE_MODULE, rule_id)
            if rule:
                codes.append(str(rule.get("规则编号") or ""))
        return codes

    def _build_rule_index_view(self, rule_id_set: set[int]) -> dict[str, dict[str, Any]]:
        view: dict[str, dict[str, Any]] = {}
        for rule_id in sorted(rule_id_set):
            rule = store.find(PARSE_MODULE, rule_id)
            if rule is None:
                continue
            seed_rel = store.get_relations(
                RELATION, source_module=SEED_MODULE, target_module=PARSE_MODULE, target_id=rule_id)
            seed_id = int(seed_rel[0]["source_id"]) if seed_rel else None
            seed = store.find(SEED_MODULE, seed_id) if seed_id is not None else None
            view[str(rule_id)] = {
                "rule_id": rule_id,
                "rule_code": str(rule.get("规则编号") or ""),
                "rule_status": rule.get("status"),
                "seed_id": seed_id,
                "seed_code": (seed or {}).get("种子编号"),
            }
        return view

    # ------------------------------------------------------------------
    # 口径哈希与快照构建
    # ------------------------------------------------------------------
    def _criteria_hash(self, criteria: dict[str, Any]) -> str:
        payload = json.dumps(criteria, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]

    def build_snapshot(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        strategy: str = STRATEGY_STATUS_FIRST,
        page: int = 1,
        size: int = 20,
        force: bool = False,
    ) -> dict[str, Any]:
        """按筛选口径构建（或复用）一份种子依赖快照。

        同口径重复请求直接返回已存在的同一份快照；``force=True`` 时重算。
        分页参数仅写入分页边界，不参与口径哈希。
        """
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"未知快照口径策略：{strategy}")
        criteria = {
            "filters": {k: v for k, v in sorted((filters or {}).items())},
            "keyword": keyword or None,
            "status": status or None,
            "sort_by": sort_by or None,
            "sort_order": (sort_order or "asc").lower(),
            "strategy": strategy,
        }
        criteria_version = self._criteria_hash(criteria)

        state = self._state()
        existing = next(
            (snap for snap in state.values()
             if int(snap.get("snapshot_version", 1) or 1) >= SNAPSHOT_VERSION
             and snap.get("criteria_version") == criteria_version),
            None,
        )
        if existing is not None and not force:
            # 同一筛选请求只能返回一份可定位快照：复用既有快照，仅刷新分页边界。
            existing["boundary"] = self._page_boundary(
                len(existing["entries"]) + len(existing.get("history_supplements", [])),
                page, size)
            return existing

        seed_rule_index = self._build_seed_rule_index()
        if strategy == STRATEGY_STATUS_FIRST:
            entries, supplements, excluded = self._build_status_first(
                criteria, seed_rule_index)
        else:
            entries, supplements, excluded = self._build_reference_first(
                criteria, seed_rule_index)

        rule_id_set = {rid for entry in entries + supplements for rid in entry["rule_ids"]}
        total_entries = len(entries) + len(supplements)
        snapshot = {
            "snapshot_id": f"SDS-{criteria_version}",
            "snapshot_version": SNAPSHOT_VERSION,
            "created_at": existing.get("created_at") if existing else _now_iso(),
            "recalculated_at": _now_iso(),
            "criteria_version": criteria_version,
            "criteria": criteria,
            "boundary": self._page_boundary(total_entries, page, size),
            "entries": entries,
            "history_supplements": supplements,
            "excluded_no_rule": excluded,
            "rule_index": self._build_rule_index_view(rule_id_set),
            "rule_id_set": sorted(rule_id_set),
            "consumption": existing.get("consumption", {"read_by": [], "field_recompute": []})
            if existing else {"read_by": [], "field_recompute": []},
            "last_read_at": existing.get("last_read_at") if existing else None,
            "priority_decision": PRIORITY_DECISION,
            "stale": False,
            "migrated_from": existing.get("migrated_from") if existing else None,
        }
        self._save(snapshot)
        return snapshot

    def _page_boundary(self, total: int, page: int, size: int) -> dict[str, int]:
        pages = max(1, (total + size - 1) // size)
        start = (page - 1) * size
        return {
            "page": page,
            "size": size,
            "total": total,
            "pages": pages,
            "page_start": start,
            "page_end": min(start + size, total),
        }

    def _entry_payload(
        self,
        seed: dict[str, Any],
        seed_rule_index: dict[int, list[dict[str, Any]]],
        *,
        offset: int,
        size: int,
        entry_type: str,
        in_scope: bool,
        exclude_reason: str | None = None,
        retain_reason: str | None = None,
    ) -> dict[str, Any]:
        rule_ids = [item["rule_id"] for item in seed_rule_index.get(int(seed["id"]), [])]
        return {
            "seed_id": int(seed["id"]),
            "seed_code": str(seed.get("种子编号") or ""),
            "status": seed.get("status"),
            "is_expired": seed.get("status") == EXPIRED_SEED_STATUS,
            "entry_type": entry_type,  # active / history_supplement
            "in_scope": in_scope,
            # 关联规则数与种子状态来自同一次筛选扫描的同一个索引
            "rule_ids": rule_ids,
            "rule_count": len(rule_ids),
            "rule_codes": [item["rule_code"] for item in seed_rule_index.get(int(seed["id"]), [])],
            "page_index": offset // size + 1,
            "page_offset": offset % size,
            "exclude_reason": exclude_reason,
            "retain_reason": retain_reason,
        }

    def _build_status_first(
        self,
        criteria: dict[str, Any],
        seed_rule_index: dict[int, list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """口径一：先筛状态缩小范围，再回查仍被引用的历史（过期）种子。"""
        size = 20
        scoped_rows = select_boundary_rows(
            filters=criteria["filters"], keyword=criteria["keyword"],
            status=criteria["status"], sort_by=criteria["sort_by"],
            sort_order=criteria["sort_order"])
        scoped_ids = {int(row["id"]) for row in scoped_rows}

        entries = [
            self._entry_payload(row, seed_rule_index, offset=i, size=size,
                                entry_type="active", in_scope=True)
            for i, row in enumerate(scoped_rows)
        ]

        # 回查：不在筛选边界内、但仍被解析规则引用的过期历史种子 -> 补项
        supplements: list[dict[str, Any]] = []
        referenced_seed_ids = set(seed_rule_index.keys())
        for seed in store.rows(SEED_MODULE):
            seed_id = int(seed["id"])
            if seed_id in scoped_ids:
                continue
            if seed_id not in referenced_seed_ids:
                continue
            if seed.get("status") != EXPIRED_SEED_STATUS:
                continue
            supplements.append(self._entry_payload(
                seed, seed_rule_index, offset=len(entries) + len(supplements), size=size,
                entry_type="history_supplement", in_scope=False,
                retain_reason="种子已过期但仍被解析规则引用，回查补项"))
        return entries, supplements, []

    def _build_reference_first(
        self,
        criteria: dict[str, Any],
        seed_rule_index: dict[int, list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """口径二：先聚合引用保留历史依赖全貌，无规则种子第二轮排除。"""
        size = 20
        scoped_rows = select_boundary_rows(
            filters=criteria["filters"], keyword=criteria["keyword"],
            status=criteria["status"], sort_by=criteria["sort_by"],
            sort_order=criteria["sort_order"])
        scoped_ids = {int(row["id"]) for row in scoped_rows}
        referenced_seed_ids = set(seed_rule_index.keys())

        # 先列出全部被引用种子（保留历史依赖全貌，含边界外/过期种子）
        referenced_rows = _sort_rows(
            [row for row in store.rows(SEED_MODULE) if int(row["id"]) in referenced_seed_ids],
            criteria["sort_by"], criteria["sort_order"])
        entries: list[dict[str, Any]] = []
        for i, row in enumerate(referenced_rows):
            in_scope = int(row["id"]) in scoped_ids
            retain = None if in_scope else "历史依赖保留：该种子仍被解析规则引用"
            entries.append(self._entry_payload(
                row, seed_rule_index, offset=i, size=size,
                entry_type="active", in_scope=in_scope, retain_reason=retain))

        # 第二轮：筛选边界内没有任何规则引用的种子，标出排除原因
        excluded: list[dict[str, Any]] = []
        for row in scoped_rows:
            if int(row["id"]) in referenced_seed_ids:
                continue
            excluded.append(self._entry_payload(
                row, seed_rule_index, offset=0, size=size,
                entry_type="excluded_no_rule", in_scope=True,
                exclude_reason="筛选边界内无关联解析规则，第二轮排除"))
        return entries, [], excluded

    # ------------------------------------------------------------------
    # 按种子编号定位
    # ------------------------------------------------------------------
    def locate_seed(self, snapshot_id: str, seed_code: str) -> dict[str, Any] | None:
        """按种子编号在快照内定位关联规则数与分页边界。"""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return None
        kw = seed_code.casefold()
        for bucket, label in (
            ("entries", "entry"),
            ("history_supplements", "history_supplement"),
            ("excluded_no_rule", "excluded_no_rule"),
        ):
            for entry in snapshot.get(bucket, []):
                if kw in str(entry.get("seed_code", "")).casefold():
                    return {
                        "snapshot_id": snapshot_id,
                        "bucket": label,
                        "entry": entry,
                        "page_index": entry.get("page_index"),
                        "page_offset": entry.get("page_offset"),
                        "boundary": snapshot["boundary"],
                        "marked_expired_referenced":
                            entry.get("is_expired") and entry.get("rule_count", 0) > 0,
                    }
        return None

    # ------------------------------------------------------------------
    # 口径/数据变更
    # ------------------------------------------------------------------
    def recalc_snapshot(self, snapshot_id: str, *, page: int | None = None,
                        size: int | None = None) -> dict[str, Any] | None:
        """口径或数据变更后按原筛选条件重新计算快照。"""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return None
        criteria = snapshot["criteria"]
        boundary = snapshot["boundary"]
        return self.build_snapshot(
            filters=criteria.get("filters"),
            keyword=criteria.get("keyword"),
            status=criteria.get("status"),
            sort_by=criteria.get("sort_by"),
            sort_order=criteria.get("sort_order"),
            strategy=criteria.get("strategy", STRATEGY_STATUS_FIRST),
            page=page or boundary["page"],
            size=size or boundary["size"],
            force=True,
        )

    def mark_stale_for_seed(self, seed_id: int) -> list[str]:
        """种子侧数据（状态等）变更后，把涉及该种子的快照标记为待重算。"""
        state = self._state()
        touched: list[str] = []
        for snapshot_id, snapshot in state.items():
            if int(snapshot.get("snapshot_version", 1) or 1) < SNAPSHOT_VERSION:
                continue
            seed_ids = {entry["seed_id"] for entry in snapshot.get("entries", [])}
            seed_ids |= {entry["seed_id"] for entry in snapshot.get("history_supplements", [])}
            if seed_id in seed_ids:
                snapshot["stale"] = True
                touched.append(snapshot_id)
        if touched:
            store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)
        return touched

    def mark_all_stale(self, reason: str = "解析规则或关系口径变更") -> list[str]:
        """规则/关系侧变更（新增规则、状态动作、重绑种子）后全量标记待重算。"""
        state = self._state()
        touched: list[str] = []
        for snapshot_id, snapshot in state.items():
            if int(snapshot.get("snapshot_version", 1) or 1) < SNAPSHOT_VERSION:
                continue
            snapshot["stale"] = True
            snapshot["stale_reason"] = reason
            touched.append(snapshot_id)
        if touched:
            store.set_pipeline_state(SNAPSHOT_STATE_KEY, state)
        return touched

    # ------------------------------------------------------------------
    # 跨模块链路：解析规则读取快照后同步回写
    # ------------------------------------------------------------------
    def mark_read(self, snapshot_id: str, reader: str = PARSE_MODULE) -> dict[str, Any] | None:
        """解析规则按跨模块链路读取快照，读取后同步回写读取痕迹。"""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return None
        consumption = snapshot.setdefault("consumption", {"read_by": [], "field_recompute": []})
        record = {"reader": reader, "at": _now_iso()}
        consumption["read_by"].append(record)
        snapshot["last_read_at"] = record["at"]
        self._save(snapshot)
        return record

    def record_field_recompute(
        self,
        snapshot_id: str,
        *,
        rule_id: int,
        field_template_ids: list[int],
        reader: str = PARSE_MODULE,
    ) -> dict[str, Any] | None:
        """回写：解析规则只重算本快照规则编号集合涉及的字段抽取模板。"""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return None
        consumption = snapshot.setdefault("consumption", {"read_by": [], "field_recompute": []})
        record = {
            "reader": reader,
            "rule_id": rule_id,
            "field_template_ids": list(field_template_ids),
            "at": _now_iso(),
        }
        consumption["field_recompute"].append(record)
        self._save(snapshot)
        return record


snapshot_service = SeedDependencySnapshotService()
