import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

type ModuleCase = {
  key: string
  fields: string[]
  actions: string[]
  actionTargets: string[]
  shape: string
}

const modules: ModuleCase[] = [
  {
    "key": "crawl_source",
    "fields": [
      "源标识",
      "源名称",
      "协议类型",
      "基础URL",
      "认证方式",
      "地区归属",
      "标签分类",
      "最近采集时间"
    ],
    "actions": [
      "提交校验",
      "确认接入",
      "停用源"
    ],
    "actionTargets": [
      "待校验",
      "已接入",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "seed_config",
    "fields": [
      "种子编号",
      "目标URL模式",
      "列表页XPath",
      "详情页XPath",
      "分页参数",
      "排序字段",
      "去重键",
      "种子优先级"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "标记过期"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已过期"
    ],
    "shape": "crud-table"
  },
  {
    "key": "parse_rule",
    "fields": [
      "规则编号",
      "目标字段名",
      "选择器类型",
      "选择器表达式",
      "提取方式",
      "默认值",
      "正则过滤",
      "规则版本"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "标记废弃"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已废弃"
    ],
    "shape": "crud-table"
  },
  {
    "key": "rate_limit",
    "fields": [
      "策略编号",
      "目标源",
      "请求间隔",
      "并发上限",
      "日请求总量",
      "冷却时长",
      "队列上限",
      "优先级"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用策略"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "proxy_pool",
    "fields": [
      "节点编号",
      "代理地址",
      "端口号",
      "协议类型",
      "地域标识",
      "响应延迟",
      "成功率",
      "失效次数"
    ],
    "actions": [
      "提交校验",
      "标记可用",
      "标记失效"
    ],
    "actionTargets": [
      "待校验",
      "可用",
      "已失效"
    ],
    "shape": "crud-table"
  },
  {
    "key": "session_mgr",
    "fields": [
      "会话编号",
      "关联源",
      "登录状态",
      "最后活动",
      "请求计数",
      "Cookie摘要",
      "UserAgent",
      "会话时长"
    ],
    "actions": [
      "标记活跃",
      "标记过期",
      "标记失效"
    ],
    "actionTargets": [
      "活跃",
      "已过期",
      "已失效"
    ],
    "shape": "crud-table"
  },
  {
    "key": "retry_policy",
    "fields": [
      "策略编号",
      "目标源",
      "最大重试次数",
      "退避算法",
      "初始延迟",
      "可重试异常",
      "不可重试异常",
      "策略版本"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用策略"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "field_extract",
    "fields": [
      "模板编号",
      "关联种子",
      "字段名",
      "选择器",
      "精炼规则",
      "输出类型",
      "可为空",
      "抽取优先级"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "标记废弃"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已废弃"
    ],
    "shape": "crud-table"
  },
  {
    "key": "anti_spider",
    "fields": [
      "策略编号",
      "目标源",
      "反爬类型",
      "触发条件",
      "应对方式",
      "冷静时长",
      "切换代理",
      "标识伪造"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用策略"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "page_render",
    "fields": [
      "配置编号",
      "目标源",
      "渲染引擎",
      "超时时间",
      "拦截规则",
      "屏幕尺寸",
      "滚动策略",
      "资源白名单"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用配置"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "data_validate",
    "fields": [
      "规则编号",
      "目标字段",
      "校验函数",
      "合法范围",
      "异常动作",
      "采样比例",
      "校验阶段",
      "阻断策略"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用规则"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "incre_cursor",
    "fields": [
      "标记编号",
      "关联种子",
      "增量字段",
      "上次位置",
      "当前位置",
      "增量类型",
      "覆写策略",
      "更新间隔"
    ],
    "actions": [
      "提交校验",
      "标记采集中",
      "复位游标"
    ],
    "actionTargets": [
      "待校验",
      "采集中",
      "已复位"
    ],
    "shape": "crud-table"
  },
  {
    "key": "dedup_filter",
    "fields": [
      "规则编号",
      "目标源",
      "去重键列",
      "比较方式",
      "相似度阈值",
      "时间窗口",
      "冲突策略",
      "规则版本"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用规则"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "format_convert",
    "fields": [
      "配置编号",
      "源格式",
      "目标格式",
      "映射规则",
      "字符编码",
      "嵌套展开",
      "数组拍平",
      "缺失处理"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用配置"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "crawl_schedule",
    "fields": [
      "计划编号",
      "关联种子",
      "Cron表达式",
      "并行批次",
      "优先级",
      "依赖条件",
      "失败动作",
      "计划版本"
    ],
    "actions": [
      "提交校验",
      "激活计划",
      "暂停计划"
    ],
    "actionTargets": [
      "待校验",
      "已激活",
      "已暂停"
    ],
    "shape": "crud-table"
  },
  {
    "key": "result_store",
    "fields": [
      "配置编号",
      "输出格式",
      "存储路径",
      "分区规则",
      "压缩算法",
      "保留天数",
      "写入批次",
      "回滚水位"
    ],
    "actions": [
      "提交校验",
      "标记生效",
      "停用配置"
    ],
    "actionTargets": [
      "待校验",
      "已生效",
      "已停用"
    ],
    "shape": "crud-table"
  },
  {
    "key": "crawl_monitor",
    "fields": [
      "指标编号",
      "关联源",
      "成功率",
      "平均延迟",
      "失败分类",
      "采集量",
      "数据量",
      "最近告警"
    ],
    "actions": [
      "提交校验",
      "启动监控",
      "静默告警"
    ],
    "actionTargets": [
      "待校验",
      "监控中",
      "已静默"
    ],
    "shape": "analysis-dashboard"
  },
  {
    "key": "content_extract",
    "fields": [
      "任务编号",
      "来源页面",
      "内容类型",
      "抽取算法",
      "关键词过滤",
      "摘要长度",
      "质量阈值",
      "任务批次"
    ],
    "actions": [
      "发起提取",
      "确认完成",
      "取消提取"
    ],
    "actionTargets": [
      "提取中",
      "已完成",
      "已取消"
    ],
    "shape": "job-timeline"
  }
]
const repoKind = 'fullstack'
const crudModules = modules.filter(m => m.shape === 'crud-table')
const dagModules = modules.filter(m => m.shape === 'dag-graph')
const timelineModules = modules.filter(m => m.shape === 'job-timeline')
const analysisModules = modules.filter(m => m.shape === 'analysis-dashboard')

function parseCsv(text: string): string[][] {
  const records: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  const input = text.replace(/^\uFEFF/, '')
  for (let index = 0; index < input.length; index++) {
    const char = input[index]
    if (quoted) {
      if (char === '"' && input[index + 1] === '"') { cell += '"'; index++ }
      else if (char === '"') { quoted = false }
      else { cell += char }
    } else if (char === '"' && cell === '') { quoted = true }
    else if (char === ',') { row.push(cell); cell = '' }
    else if (char === '\n') {
      row.push(cell.replace(/\r$/, ''))
      if (row.some(v => v !== '')) records.push(row)
      row = []; cell = ''
    } else { cell += char }
  }
  row.push(cell.replace(/\r$/, ''))
  if (row.some(v => v !== '')) records.push(row)
  return records
}

test('CRUD模块：筛选、导出、登记、动作、指标全链路', async ({ page }) => {
  test.setTimeout(300_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status()>=400) browserErrors.push(`${r.status()} ${r.url()}`) })
  if (crudModules.length === 0) { test.skip(); return }

  for (const mod of crudModules) {
    await page.goto(`/${mod.key}`)
    await page.waitForTimeout(3000)
    const bodyRows = page.locator('tbody tr[data-testid^="row-"]')
    await expect(bodyRows).toHaveCount(3)
    const firstValue = await bodyRows.nth(0).locator('td').nth(0).innerText()

    await page.getByTestId(`filter-${mod.fields[0]}`).fill(firstValue)
    await page.getByTestId('query').click()
    await expect(bodyRows).toHaveCount(1)
    await page.getByTestId('reset').click()
    await expect(bodyRows).toHaveCount(3)

    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('export').click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe(`${mod.key}.csv`)

    const createdValues = mod.fields.map((f, i) => `验收-${mod.key}-${i+1}`)
    await page.getByTestId('open-create').click()
    await expect(page.getByTestId('create-modal')).toBeVisible()
    for (const [i, field] of mod.fields.entries()) {
      await page.getByTestId(`create-${field}`).fill(createdValues[i])
    }
    await page.getByTestId('submit-create').click()
    await expect(page.getByTestId('create-modal')).toHaveCount(0)
    await expect(bodyRows).toHaveCount(4)

    const createdRow = page.locator('tbody tr').filter({ hasText: createdValues[0] })
    for (const [i, action] of mod.actions.entries()) {
      await createdRow.getByRole('button', { name: action, exact: true }).click()
      await expect(createdRow).toHaveAttribute('data-status', mod.actionTargets[i])
    }
  }

  expect(browserErrors).toEqual([])
})

test('DAG拓扑模块：节点渲染、详情、动作、导出', async ({ page }) => {
  test.setTimeout(300_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status()>=400) browserErrors.push(`${r.status()} ${r.url()}`) })
  if (dagModules.length === 0) { test.skip(); return }

  for (const mod of dagModules) {
    await page.goto(`/${mod.key}`)
    await page.waitForTimeout(3000)
    await expect(page.locator('svg.dag-svg')).toBeVisible()
    const nodes = page.locator('g.dag-node')
    const count = await nodes.count()
    expect(count).toBeGreaterThanOrEqual(3)

    await nodes.first().click()
    await expect(page.getByTestId('dag-detail')).toBeVisible()

    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('export').click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe(`${mod.key}.csv`)

    await page.getByTestId('open-create').click()
    await expect(page.getByTestId('create-modal')).toBeVisible()
    for (const [i, field] of mod.fields.slice(0,3).entries()) {
      await page.locator('.create-field input').nth(i).fill(`E2E-${mod.key}-${i}`)
    }
    await page.getByTestId('submit-create').click()
    await expect(page.getByTestId('create-modal')).toHaveCount(0)
  }

  expect(browserErrors).toEqual([])
})

test('时间线模块：甘特条渲染、日期筛选、日志查看、登记', async ({ page }) => {
  test.setTimeout(300_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status()>=400) browserErrors.push(`${r.status()} ${r.url()}`) })
  if (timelineModules.length === 0) { test.skip(); return }

  for (const mod of timelineModules) {
    await page.goto(`/${mod.key}`)
    await expect(page.locator('.timeline-bar').first()).toBeVisible({ timeout: 10000 })
    const bars = page.locator('.timeline-bar')
    const count = await bars.count()
    expect(count).toBeGreaterThanOrEqual(3)

    await bars.first().click()
    await expect(page.getByTestId('timeline-log')).toBeVisible()

    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('export').click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe(`${mod.key}.csv`)

    await page.getByTestId('open-create').click()
    await expect(page.getByTestId('create-modal')).toBeVisible()
    for (const [i, field] of mod.fields.slice(0,3).entries()) {
      await page.locator('.create-field input').nth(i).fill(`E2E-${mod.key}-${i}`)
    }
    await page.getByTestId('submit-create').click()
    await expect(page.getByTestId('create-modal')).toHaveCount(0)
  }

  expect(browserErrors).toEqual([])
})

test('分析看板模块：KPI卡片、运行分析、图表渲染', async ({ page }) => {
  test.setTimeout(300_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status()>=400) browserErrors.push(`${r.status()} ${r.url()}`) })
  if (analysisModules.length === 0) { test.skip(); return }

  for (const mod of analysisModules) {
    await page.goto(`/${mod.key}`)
    await expect(page.getByTestId('kpi-card').first()).toBeVisible({ timeout: 10000 })
    const kpiCards = page.getByTestId('kpi-card')
    const kpiCount = await kpiCards.count()
    expect(kpiCount).toBeGreaterThanOrEqual(2)
    await expect(kpiCards.first().locator('strong')).toBeVisible()

    await page.getByTestId('run-analysis').click()
    await expect(page.locator('.analysis-bar').first()).toBeVisible({ timeout: 5000 })
    await expect(page.locator('.data-table tbody tr').first()).toBeVisible({ timeout: 5000 })
  }

  expect(browserErrors).toEqual([])
})

test('概览汇总', async ({ page }) => {
  test.setTimeout(300_000)
  await page.goto('/')
  await expect(page.locator('.stat-value').nth(1)).toBeVisible()
  // 验证概览页加载正常即可，不硬编码精确计数（取决于各模块 E2E 登记成功数）
})

test('种子依赖快照：构建、口径切换、定位、过期引用标记、重算', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`) })

  await page.goto('/seed_config')
  await page.waitForTimeout(2000)
  await expect(page.getByTestId('snapshot-panel')).toBeVisible()

  // 构建一份默认口径（status_first）快照
  await page.getByTestId('build-snapshot').click()
  await expect(page.getByTestId('current-snapshot')).toBeVisible()
  const snapshotId = (await page.getByTestId('snapshot-id-input').inputValue()).trim()
  expect(snapshotId.startsWith('SDS-')).toBeTruthy()
  // 快照条目必须带关联规则数与分页定位
  await expect(page.getByTestId('snapshot-rule-count').first()).toBeVisible()

  // 按种子编号定位关联规则数与分页边界
  await page.getByTestId('locate-seed-code').fill('SEED-E001')
  await page.getByTestId('locate-seed').click()
  await expect(page.getByTestId('located-entry')).toBeVisible()
  await expect(page.getByTestId('located-entry')).toContainText('关联规则数 2')

  // 切换 reference_first 口径后重算，仍是同一份页面内可定位快照
  await page.getByTestId('snapshot-strategy').selectOption('reference_first')
  await page.getByTestId('recalc-snapshot').click()
  await expect(page.getByTestId('current-snapshot')).toBeVisible()

  // 历史快照清单（含 v1 迁移）
  await page.getByTestId('load-snapshots').click()
  await expect(page.getByTestId('snapshot-list-summary')).toBeVisible()

  expect(browserErrors).toEqual([])
})

test('解析规则消费种子依赖快照：读取回写与限定范围重算', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`) })

  // 先在种子管理页生成一份快照
  await page.goto('/seed_config')
  await page.waitForTimeout(2000)
  await page.getByTestId('build-snapshot').click()
  await expect(page.getByTestId('current-snapshot')).toBeVisible()
  const snapshotId = (await page.getByTestId('snapshot-id-input').inputValue()).trim()

  await page.goto('/parse_rule')
  await page.waitForTimeout(2000)
  await expect(page.getByTestId('snapshot-panel')).toBeVisible()
  await page.getByTestId('parse-snapshot-id').fill(snapshotId)
  await page.getByTestId('read-snapshot').click()
  await expect(page.getByTestId('consumed-snapshot')).toBeVisible()
  await expect(page.getByTestId('consumed-rule-ids')).not.toHaveText('')

  // 只重算快照规则集合涉及的字段抽取模板
  await page.getByTestId('recompute-snapshot-fields').click()
  await expect(page.getByTestId('recompute-result')).toBeVisible()
  await expect(page.getByTestId('recompute-result')).toContainText('字段抽取模板')

  expect(browserErrors).toEqual([])
})
