<template>
  <section class="page" data-module="parse_rule">
    <header class="page-head">
      <div>
        <h2>解析规则管理</h2>
        <p class="page-desc">维护HTML/JSON内容解析规则的创建、版本与生效管理，为内容解析提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记解析规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出解析规则清单</button>
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
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无解析规则数据，可先登记解析规则</td>
        </tr>
      </tbody>
    </table>

    <section class="snapshot-panel" data-testid="snapshot-panel">
      <header class="snapshot-head">
        <h3>种子依赖快照消费（跨模块链路）</h3>
        <p class="page-desc">
          读取种子管理一侧形成的依赖快照，读取后同步回写；只重算本快照规则编号集合涉及的字段抽取模板。
        </p>
      </header>
      <form class="filter-bar" @submit.prevent="readSnapshot">
        <label class="filter-item">
          <span>快照编号</span>
          <input v-model="snapshotId" data-testid="parse-snapshot-id" placeholder="如 SDS-xxxx" />
        </label>
        <button class="btn primary" data-testid="read-snapshot" type="submit">读取快照并回写</button>
        <button class="btn" data-testid="recompute-snapshot-fields" type="button" @click="recomputeFields">
          只重算快照涉及模板
        </button>
      </form>
      <div v-if="snapshotMessage" class="error-text" data-testid="snapshot-message">{{ snapshotMessage }}</div>

      <div v-if="consumedSnapshot" data-testid="consumed-snapshot">
        <p>
          快照 <strong>{{ consumedSnapshot.snapshot_id }}</strong>
          （口径 {{ consumedSnapshot.criteria_version }}）｜
          分页边界：第 {{ consumedSnapshot.boundary.page }} / {{ consumedSnapshot.boundary.pages }} 页，
          区间 [{{ consumedSnapshot.boundary.page_start }}, {{ consumedSnapshot.boundary.page_end }})
          <span v-if="consumedSnapshot.stale" class="tag tag-warn">快照待重算</span>
        </p>
        <p>本快照规则编号集合（{{ consumedSnapshot.rule_id_set.length }} 条）：
          <strong data-testid="consumed-rule-ids">{{ consumedSnapshot.rule_id_set.join('、') }}</strong>
        </p>
        <p>读取回写：{{ consumedSnapshot.read_record?.reader }} @ {{ consumedSnapshot.read_record?.at }}</p>
        <table class="data-table">
          <thead>
            <tr><th>种子编号</th><th>状态</th><th>关联规则数</th><th>规则编号</th><th>标记</th></tr>
          </thead>
          <tbody>
            <tr v-for="entry in consumedSnapshot.entries" :key="entry.seed_id"
                :data-testid="`consumed-row-${entry.seed_id}`">
              <td>{{ entry.seed_code }}</td>
              <td>{{ entry.status }}</td>
              <td>{{ entry.rule_count }}</td>
              <td>{{ entry.rule_codes.join('、') || '—' }}</td>
              <td>
                <span v-if="entry.is_expired && entry.rule_count > 0" class="tag tag-warn">过期但仍被引用</span>
                <span v-else-if="entry.retain_reason" class="tag">{{ entry.retain_reason }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="recomputeResult" data-testid="recompute-result">
        <h4>重算结果</h4>
        <p>
          涉及规则 {{ recomputeResult.rule_count }} 条，
          字段抽取模板 {{ recomputeResult.field_template_count }} 个：
          {{ recomputeResult.field_template_ids.join('、') }}
        </p>
      </div>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记解析规则</h3>
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
      <span>共 {{ total }} 条解析规则记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/parse_rule'
const columns = ["规则编号", "目标字段名", "选择器类型", "选择器表达式", "提取方式", "默认值", "正则过滤", "规则版本"]
const actions = ["提交校验", "标记生效", "标记废弃"]
const statuses = ["草稿", "待校验", "已生效", "已废弃"]
const metricNames = ["解析规则总数", "待校验规则", "已生效规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

const snapshotId = ref('')
const consumedSnapshot = ref<Record<string, any> | null>(null)
const recomputeResult = ref<Record<string, any> | null>(null)
const snapshotMessage = ref('')
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
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
    link.download = 'parse_rule.csv'
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
      throw new Error('解析规则动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析规则操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('解析规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析规则列表读取失败'
  }
}

async function readSnapshot() {
  snapshotMessage.value = ''
  if (!snapshotId.value) {
    snapshotMessage.value = '请填写种子依赖快照编号'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/seed_dependency_snapshots/${snapshotId.value}`)
    if (!response.ok) {
      const data = await response.json().catch(() => ({ detail: '快照读取失败' }))
      throw new Error(data.detail || '快照读取失败')
    }
    consumedSnapshot.value = await response.json()
    recomputeResult.value = null
  } catch (error) {
    consumedSnapshot.value = null
    snapshotMessage.value = error instanceof Error ? error.message : '快照读取失败'
  }
}

async function recomputeFields() {
  snapshotMessage.value = ''
  if (!snapshotId.value) {
    snapshotMessage.value = '请填写种子依赖快照编号'
    return
  }
  try {
    const response = await request(
      `${ENDPOINT}/seed_dependency_snapshots/${snapshotId.value}/recompute_fields`,
      { method: 'POST' },
    )
    if (!response.ok) {
      const data = await response.json().catch(() => ({ detail: '模板重算失败' }))
      throw new Error(data.detail || '模板重算失败')
    }
    recomputeResult.value = await response.json()
  } catch (error) {
    snapshotMessage.value = error instanceof Error ? error.message : '模板重算失败'
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
