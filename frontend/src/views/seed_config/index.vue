<template>
  <section class="page" data-module="seed_config">
    <header class="page-head">
      <div>
        <h2>种子管理管理</h2>
        <p class="page-desc">维护采集种子URL模板与页面结构描述，为种子管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记采集种子</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出种子管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>种子编号检索</span>
        <input v-model="keyword" data-testid="filter-keyword" placeholder="检索式查询：按种子编号" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="statusFilter" data-testid="filter-status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>排序字段</span>
        <select v-model="sortBy" data-testid="sort-by">
          <option value="">默认排序</option>
          <option v-for="f in sortableFields" :key="f" :value="f">{{ f }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>排序方向</span>
        <select v-model="sortOrder" data-testid="sort-order">
          <option value="asc">升序</option>
          <option value="desc">降序</option>
        </select>
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>关联解析规则数</th>
          <th>状态标记</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td data-testid="rule-count-cell">{{ row._parse_rule_count ?? 0 }}</td>
          <td>
            <span v-if="row.status === '已过期' && Number(row._parse_rule_count) > 0"
                  class="tag tag-warn" data-testid="expired-referenced-tag">过期仍被引用</span>
            <span v-else>{{ row.status }}</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无种子管理数据，可先登记采集种子</td>
        </tr>
      </tbody>
    </table>

    <section class="snapshot-panel" data-testid="snapshot-panel">
      <header class="snapshot-head">
        <h3>种子依赖快照</h3>
        <p class="page-desc">
          种子状态与规则关联数来自同一筛选边界；快照可按种子编号定位关联规则数与分页边界。
          采集种子优先处理顺序：<strong>{{ priorityDecision }}</strong>
        </p>
        <div class="page-actions">
          <label class="filter-item">
            <span>冲突口径</span>
            <select v-model="snapshotStrategy" data-testid="snapshot-strategy">
              <option value="status_first">先筛状态+回查历史种子</option>
              <option value="reference_first">先聚合引用+二轮排除无规则种子</option>
            </select>
          </label>
          <button class="btn primary" data-testid="build-snapshot" type="button" @click="buildSnapshot(false)">生成依赖快照</button>
          <button class="btn" data-testid="recalc-snapshot" type="button" @click="buildSnapshot(true)">口径变更重算</button>
          <button class="btn ghost" data-testid="load-snapshots" type="button" @click="loadSnapshots">查看全部快照</button>
        </div>
      </header>

      <form class="filter-bar" @submit.prevent="locateSeed">
        <label class="filter-item">
          <span>快照编号</span>
          <input v-model="snapshotId" data-testid="snapshot-id-input" placeholder="如 SDS-xxxx" />
        </label>
        <label class="filter-item">
          <span>种子编号定位</span>
          <input v-model="locateCode" data-testid="locate-seed-code" placeholder="如 SEED-E001" />
        </label>
        <button class="btn" data-testid="locate-seed" type="submit">定位关联规则与分页边界</button>
      </form>

      <div v-if="snapshotMessage" class="error-text" data-testid="snapshot-message">{{ snapshotMessage }}</div>

      <div v-if="currentSnapshot" data-testid="current-snapshot">
        <p>
          快照 <strong>{{ currentSnapshot.snapshot_id }}</strong>
          （v{{ currentSnapshot.snapshot_version }}，口径 {{ currentSnapshot.criteria_version }}）
          <span v-if="currentSnapshot.stale" class="tag tag-warn" data-testid="snapshot-stale">待重算</span>
          ｜分页边界：第 {{ currentSnapshot.boundary.page }} / {{ currentSnapshot.boundary.pages }} 页，
          区间 [{{ currentSnapshot.boundary.page_start }}, {{ currentSnapshot.boundary.page_end }})，
          共 {{ currentSnapshot.boundary.total }} 条
        </p>
        <table class="data-table">
          <thead>
            <tr>
              <th>种子编号</th><th>状态</th><th>类型</th><th>关联规则数</th>
              <th>规则编号集合</th><th>分页定位</th><th>标记/原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in snapshotEntries" :key="`${entry.bucket}-${entry.seed_id}`"
                :data-testid="`snapshot-row-${entry.seed_id}`">
              <td>{{ entry.seed_code }}</td>
              <td>{{ entry.status }}</td>
              <td>{{ entryLabel(entry) }}</td>
              <td data-testid="snapshot-rule-count">{{ entry.rule_count }}</td>
              <td>{{ entry.rule_codes.join('、') || '—' }}</td>
              <td>第{{ entry.page_index }}页 / 偏移{{ entry.page_offset }}</td>
              <td>
                <span v-if="entry.is_expired && entry.rule_count > 0" class="tag tag-warn">过期但仍被引用</span>
                <span v-else-if="entry.exclude_reason" class="tag">{{ entry.exclude_reason }}</span>
                <span v-else-if="entry.retain_reason" class="tag">{{ entry.retain_reason }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="locatedEntry" data-testid="located-entry">
        <h4>定位结果：{{ locatedEntry.entry.seed_code }}</h4>
        <p>
          关联规则数 {{ locatedEntry.entry.rule_count }}，
          位于第 {{ locatedEntry.page_index }} 页（偏移 {{ locatedEntry.page_offset }}），
          过期仍被引用：{{ locatedEntry.marked_expired_referenced ? '是' : '否' }}
        </p>
      </div>

      <details v-if="snapshotList.length">
        <summary data-testid="snapshot-list-summary">历史快照清单（{{ snapshotList.length }} 份，含迁移版本）</summary>
        <table class="data-table">
          <thead><tr><th>快照编号</th><th>版本</th><th>口径</th><th>条目数</th><th>最近读取</th><th>待重算</th></tr></thead>
          <tbody>
            <tr v-for="s in snapshotList" :key="s.snapshot_id" data-testid="snapshot-list-row">
              <td>{{ s.snapshot_id }}</td>
              <td>v{{ s.snapshot_version }}<template v-if="s.migrated_from">（自 v{{ s.migrated_from }} 迁移）</template></td>
              <td>{{ s.criteria_version }}</td>
              <td>{{ s.boundary?.total ?? s.entries?.length ?? 0 }}</td>
              <td>{{ s.last_read_at || '未读取' }}</td>
              <td>{{ s.stale ? '是' : '否' }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记采集种子</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条种子管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type SnapshotEntry = Record<string, any>
type Snapshot = Record<string, any>

const ENDPOINT = '/api/seed_config'
const columns = ["种子编号", "目标URL模式", "列表页XPath", "详情页XPath", "分页参数", "排序字段", "去重键", "种子优先级"]
const actions = ["提交校验", "标记生效", "标记过期"]
const statuses = ["草稿", "待校验", "已生效", "已过期"]
const sortableFields = ["种子编号", "种子优先级", "排序字段"]
const metricNames = ["采集种子总数", "待校验种子", "已生效种子"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const keyword = ref('')
const statusFilter = ref('')
const sortBy = ref('')
const sortOrder = ref('asc')

const snapshotStrategy = ref('status_first')
const snapshotId = ref('')
const locateCode = ref('')
const currentSnapshot = ref<Snapshot | null>(null)
const locatedEntry = ref<Snapshot | null>(null)
const snapshotList = ref<Snapshot[]>([])
const snapshotMessage = ref('')
const priorityDecision = ref('待判定')

const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

const snapshotEntries = computed<SnapshotEntry[]>(() => {
  if (!currentSnapshot.value) return []
  const entries = (currentSnapshot.value.entries ?? []).map((e: SnapshotEntry) => ({ ...e, bucket: 'entry' }))
  const supplements = (currentSnapshot.value.history_supplements ?? [])
    .map((e: SnapshotEntry) => ({ ...e, bucket: 'supplement' }))
  const excluded = (currentSnapshot.value.excluded_no_rule ?? [])
    .map((e: SnapshotEntry) => ({ ...e, bucket: 'excluded' }))
  return [...entries, ...supplements, ...excluded]
})

function entryLabel(entry: SnapshotEntry): string {
  if (entry.bucket === 'supplement') return '历史补项'
  if (entry.bucket === 'excluded') return '二轮排除'
  return entry.in_scope === false ? '历史依赖保留' : '当前有效'
}

function resetFilters() {
  filters.value = {}
  keyword.value = ''
  statusFilter.value = ''
  sortBy.value = ''
  sortOrder.value = 'asc'
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'seed_config.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('种子管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '种子管理操作失败'
  }
}

function buildQuery(): URLSearchParams {
  const params = new URLSearchParams(filters.value as Record<string, string>)
  if (keyword.value) params.set('keyword', keyword.value)
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (sortBy.value) {
    params.set('sort_by', sortBy.value)
    params.set('sort_order', sortOrder.value)
  }
  return params
}

async function reload() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}?${buildQuery().toString()}`)
    if (!response.ok) {
      throw new Error('采集种子列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (rows.value[0]?._snapshot_id) {
      snapshotId.value = String(rows.value[0]._snapshot_id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集种子列表读取失败'
  }
}

async function buildSnapshot(force: boolean) {
  snapshotMessage.value = ''
  try {
    const payload: Record<string, any> = {
      filters: { ...filters.value },
      keyword: keyword.value || null,
      status: statusFilter.value || null,
      sort_by: sortBy.value || null,
      sort_order: sortOrder.value,
      strategy: snapshotStrategy.value,
      force,
    }
    const response = await request(`${ENDPOINT}/dependency_snapshots`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.detail || '快照生成失败')
    currentSnapshot.value = data.snapshot
    snapshotId.value = data.snapshot.snapshot_id
    priorityDecision.value = data.snapshot.priority_decision ?? '待判定'
    locatedEntry.value = null
  } catch (error) {
    snapshotMessage.value = error instanceof Error ? error.message : '快照生成失败'
  }
}

async function locateSeed() {
  snapshotMessage.value = ''
  if (!snapshotId.value || !locateCode.value) {
    snapshotMessage.value = '请填写快照编号与种子编号'
    return
  }
  try {
    const query = new URLSearchParams({ seed_code: locateCode.value }).toString()
    const response = await request(`${ENDPOINT}/dependency_snapshots/${snapshotId.value}/locate?${query}`)
    if (!response.ok) {
      const data = await response.json().catch(() => ({ detail: '定位失败' }))
      throw new Error(data.detail || '定位失败')
    }
    locatedEntry.value = await response.json()
  } catch (error) {
    locatedEntry.value = null
    snapshotMessage.value = error instanceof Error ? error.message : '定位失败'
  }
}

async function loadSnapshots() {
  snapshotMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/dependency_snapshots`)
    if (!response.ok) throw new Error('快照清单读取失败')
    const data = await response.json()
    snapshotList.value = data.items ?? []
  } catch (error) {
    snapshotMessage.value = error instanceof Error ? error.message : '快照清单读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.snapshot-panel {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--border-color, #d9d9d9);
  border-radius: 8px;
}
.snapshot-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  background: #f0f0f0;
  font-size: 12px;
}
.tag-warn {
  background: #fff3e0;
  color: #d46b08;
}
</style>
