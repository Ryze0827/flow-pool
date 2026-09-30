<script setup>
import AccountQuota from './AccountQuota.vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Activity, ArrowDownToLine, ArrowRight, Check, CheckCheck, ChevronRight, CircleHelp, Clock3, CloudUpload, Database, FileJson, Gauge, Layers3, LayoutDashboard, ListFilter, LoaderCircle, LogOut, Mail, Pause, Play, Plus, RefreshCw, Search, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Unplug, X, Zap } from 'lucide-vue-next'
import { api } from './api'
import ImportOptions from './ImportOptions.vue'
import LoginImport from './LoginImport.vue'
import ReloginAccount from './ReloginAccount.vue'
import ImportHistory from './ImportHistory.vue'
import MailNotifications from './MailNotifications.vue'
import InvoiceManagement from './InvoiceManagement.vue'
import SystemUpgrade from './SystemUpgrade.vue'
import AdminLogin from './AdminLogin.vue'
import RateInspection from './RateInspection.vue'
import PostgresSettings from './PostgresSettings.vue'
import PerformanceMetrics from './PerformanceMetrics.vue'
import RankingSidebar from './RankingSidebar.vue'

const clone = value => JSON.parse(JSON.stringify(value))
const nav = [
  { id: 'overview', name: '调度总览', icon: LayoutDashboard },
  { id: 'accounts', name: '账号号池', icon: Layers3 },
  { id: 'usage', name: '账号调用记录', icon: ListFilter },
  { id: 'import', name: '快速上号', icon: CloudUpload },
  { id: 'rates', name: '用户倍率巡检', icon: Gauge },
  { id: 'rules', name: '调度规则', icon: SlidersHorizontal },
  { id: 'events', name: '操作日志', icon: Activity },
  { id: 'mail', name: '邮件通知', icon: Mail },
  { id: 'invoices', name: '发票管理', icon: FileJson }
]
const pools = [{ id: 'priority', name: '高权重组', desc: '核心账号，稳定交付', icon: Zap, color: 'purple' }, { id: 'risk', name: '风控组', desc: '独立归档，持续观察', icon: ShieldCheck, color: 'teal' }, { id: 'third_party', name: '三方账号组', desc: '外部资源，统一管理', icon: Layers3, color: 'blue' }]
const routePaths = { overview: '/overview', accounts: '/accounts', usage: '/usage', import: '/import', rates: '/rate-inspection', rules: '/rules', events: '/events', mail: '/mail', invoices: '/invoices', settings: '/settings' }
const pageFromPath = path => Object.entries(routePaths).find(([, route]) => route === path)?.[0] || 'overview'
const page = ref(pageFromPath(globalThis.location?.pathname || '/'))
const auth = ref({ ready: false, configured: false, authenticated: false, username: '' })
const authStatusError = ref('')
const syncingRoute = ref(false)
const sidebarCollapsed = ref(globalThis.localStorage?.getItem('flowpool.sidebar.collapsed') === '1')
const refreshIntervalSeconds = 10
const settings = ref(null)
const data = ref({ accounts: [], events: [], inspections: [], batches: [], worker: {}, configured: false })
const usagePoolRecords = ref({ priority: [], risk: [], third_party: [] })
const usageTotal = ref(0)
const usageActiveCounts = computed(() => Object.fromEntries(pools.map(pool => [pool.id, data.value.accounts.filter(account => account.pool === pool.id && ['active', 'probation'].includes(account.state) && account.remote?.schedulable === true && !availabilityIssue(account)).length])))
const usageLoading = ref(false)
const ranking = ref([])
// 占比每 30 秒更新，状态始终取账号列表的最新快照，避免把本地 active 当成上游可用。
const rankingAccounts = computed(() => {
  const accounts = new Map(data.value.accounts.map(account => [account.id, account]))
  return ranking.value.flatMap(item => {
    const account = accounts.get(item.account_id)
    return account ? [{ ...item, account, poolName: poolName(account.pool), scheduleLabel: scheduleLabel(account) }] : []
  })
})
const rankingSampleSize = ref(0)
const rankingUpdatedAt = ref(0)
const rankingLoading = ref(false)
const usageColumnsOpen = ref(false)
const usageColumnLabels = { model: '模型', reasoning_effort: '推理强度', group: '分组' }
const metadata = ref({ groups: [], proxies: [] })
const busy = ref(false)
const toast = ref(null)
const loadError = ref('')
const poolFilter = ref('all')
const search = ref('')
const stateFilter = ref('all')
const modal = ref(false)
const remoteAccounts = ref([])
const remoteSearch = ref('')
const remoteGroup = ref('')
const selected = ref([])
const enrollPool = ref('priority')
const remoteLoaded = ref(false)
const detail = ref(null)
const loginLoading = ref(false)
const loginForm = ref(null)
const relogin = ref(null)
const importMode = ref('login')
const batchPanel = ref(null)
const fileName = ref('')
const filePayload = ref(null)
const batch = ref(null)
const historyRevision = ref(0)
const fileInput = ref(null)
const importOptions = ref(null)
const importSettingsDialog = ref(null)
const importSettingsError = ref('')
const now = ref(Date.now() / 1000)
let polling, rankingPolling, clock, toastTimer
const statuses = { active: '调度中', cooldown: '冷却中', probation: '恢复观察', manual: '手动暂停', pausing: '暂停待确认', resuming: '恢复待确认' }
const poolName = id => pools.find(p => p.id === id)?.name || id
const isPoolGuarded = pool => (settings.value?.rule.guarded_pools || ['risk']).includes(pool)
const isGuarded = account => isPoolGuarded(account.pool) && (account.guard_enabled ?? true)
const poolGuardCount = pool => data.value.accounts.filter(account => account.pool === pool && isGuarded(account)).length
const poolAccountCount = pool => data.value.accounts.filter(account => account.pool === pool).length
const poolGuardEnabled = pool => isPoolGuarded(pool)
const poolGuardMixed = pool => poolGuardCount(pool) > 0 && poolGuardCount(pool) < poolAccountCount(pool)
const guardedPoolNames = computed(() => pools.filter(pool => poolGuardCount(pool.id) > 0).map(pool => pool.name).join('、') || '未开启账号风控')
const accountName = id => data.value.accounts.find(account => account.id === id)?.name || `#${id}`
const timeText = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未执行'
const usageTimeText = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const latencyText = value => value === null || value === undefined ? '—' : `${(value / 1000).toFixed(2)}s`
const shortTime = value => value ? new Date(value * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '等待首次检查'
const remaining = value => { const seconds = Math.max(0, Math.ceil(value - now.value)); return `${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2, '0')}秒` }
const rankingRefreshText = computed(() => rankingUpdatedAt.value ? (rankingUpdatedAt.value + 30 > now.value ? `${remaining(rankingUpdatedAt.value + 30)}后刷新` : '即将刷新') : '等待首次刷新')
const sampleStatus = account => {
  if (account.auto_relogin_running) return '凭据失效 · 自动重登中'
  if (account.pool === 'third_party' && account.relogin_failures > 3) return '自动重登 · 已放弃'
  if (account.pool === 'third_party' && account.remote.status === 'error') return !account.auto_relogin_available ? '账号异常 · 需手动处理' : '账号异常 · 等待恢复'
  if (account.remote.image_only) return '仅生图 · 无需评估'
  if (!settings.value.rule.enabled) return '守护关闭 · 暂不评估'
  if (!isGuarded(account)) return '未纳入守护 · 暂不评估'
  if (account.state === 'cooldown') return account.cooldown_mode === 'image' ? '仅生图 · 无需评估' : '冷却中 · 暂不评估'
  if (account.state === 'manual') return '手动暂停 · 暂不评估'
  if (account.state === 'pausing') return '暂停中 · 等待确认'
  if (account.state === 'resuming') return '恢复中 · 等待确认'
  if (account.state === 'probation') return `恢复观察 · ${account.sample.length} 条新样本`
  const total = settings.value.rule.sample_size
  if (!account.sample.length) return `等待调用 · 0/${total} 条`
  if (account.sample.length < total) return `采样不足 · ${account.sample.length}/${total} 条`
  return `样本齐全 · ${account.sample.length}/${total} 条`
}
const nextCheckText = account => {
  if (account.state === 'cooldown') return '冷却到期后恢复'
  if (account.remote.image_only) return '无需首字评估'
  if (!settings.value.rule.enabled || !isGuarded(account)) return '未安排巡检'
  if (['manual', 'pausing', 'resuming'].includes(account.state) || (account.remote.schedulable === false && account.state !== 'cooldown')) return '不在调度中'
  // 下一轮倒计时从整轮巡检结束开始；账号的 last_checked 可能发生在本轮中途。
  const last = data.value.worker.last_tick
  if (!last) return '等待首次检查'
  if (data.value.worker.running) return '正在巡检'
  const due = last + settings.value.rule.poll_seconds
  return due > now.value ? `${remaining(due)}后检查` : '即将检查'
}
const lastCheckText = account => {
  if (account.remote.image_only) return '无需首字评估'
  if (!settings.value.rule.enabled || !isGuarded(account) || ['manual', 'pausing', 'resuming'].includes(account.state) || (account.remote.schedulable === false && account.state !== 'cooldown')) return '未巡检'
  return shortTime(account.last_checked)
}
const accountSortRank = account => {
  const remote = account.remote || {}
  const limited = ['rate_limit_reset_at', 'overload_until', 'temp_unschedulable_until'].some(field => {
    const value = remote[field]
    if (!value) return false
    const timestamp = typeof value === 'number' ? value : Date.parse(value) / 1000
    return Number.isFinite(timestamp) && timestamp > now.value
  })
  const scheduling = ['active', 'probation'].includes(account.state) && remote.status === 'active' && remote.schedulable === true && !limited && !account.error
  if (scheduling) return 0
  if (remote.status === 'error' || account.error) return 1
  if (account.state === 'manual' || account.state === 'cooldown' || remote.schedulable === false) return 2
  if (limited) return 3
  return 1
}
const filtered = computed(() => data.value.accounts
  .filter(a => (poolFilter.value === 'all' || a.pool === poolFilter.value) && (stateFilter.value === 'all' || a.state === stateFilter.value) && `${a.name} ${a.id}`.toLowerCase().includes(search.value.toLowerCase()))
  .map((account, index) => ({ account, index }))
  .sort((left, right) => accountSortRank(left.account) - accountSortRank(right.account) || left.index - right.index)
  .map(item => item.account))
const healthy = computed(() => data.value.accounts.filter(a => a.state === 'active' && a.remote.schedulable && a.remote.status === 'active' && !a.error && !availabilityIssue(a) && !guardWarning(a)).length)
const cooling = computed(() => data.value.accounts.filter(a => a.state === 'cooldown').length)
const coolingAccounts = computed(() => data.value.accounts
  .filter(account => account.state === 'cooldown' || (account.state === 'resuming' && account.resume_at != null))
  .sort((a, b) => (a.resume_at || 0) - (b.resume_at || 0)))
const cooldownText = account => account.state === 'resuming' ? '正在恢复调度' : account.resume_at > now.value ? remaining(account.resume_at) : '冷却已结束，等待恢复'
const watching = computed(() => data.value.accounts.filter(a => a.state === 'probation').length)
const currentDetail = computed(() => data.value.accounts.find(a => a.id === detail.value))
const breachCount = account => isGuarded(account) && !account.remote.image_only ? (account.breach_times || []).filter(stamp => stamp > now.value - settings.value.rule.breach_window_seconds && stamp <= now.value).length : 0
const guardWarning = account => {
  if (account.remote.image_only) return false
  if (!settings.value.rule.enabled || !isGuarded(account) || !['active', 'probation'].includes(account.state)) return false
  const recent = account.sample.filter(sample => Date.parse(sample.created_at) / 1000 > now.value - 300 && Date.parse(sample.created_at) / 1000 <= now.value)
  if (!recent.length) return false
  const slow = recent.filter(sample => sample.first_token_ms > settings.value.rule.threshold_ms).length
  const enough = account.state === 'probation' || recent.length >= settings.value.rule.sample_size
  return breachCount(account) > 0 || Boolean(account.breach_sample && enough && slow / recent.length >= settings.value.rule.slow_ratio)
}
const availabilityIssue = account => {
  if (account.remote.status !== 'active' || account.remote.schedulable === false) return '停止调度'
  for (const [field, label] of [['rate_limit_reset_at', '限流中'], ['overload_until', '过载中'], ['temp_unschedulable_until', '临时禁调中']]) {
    if (Date.parse(account.remote[field]) / 1000 > now.value) return label
  }
  return ''
}
const scheduleLabel = account => {
  if (account.cooldown_mode === 'image' && account.state === 'cooldown') return account.remote.schedulable ? '冷却 · 仅生图调度' : '冷却 · 调度已关闭'
  if (account.cooldown_mode === 'image' && account.state === 'pausing') return '生图分组切换中'
  if (['cooldown', 'manual', 'resuming'].includes(account.state)) return statuses[account.state]
  return availabilityIssue(account) || (account.error ? account.state === 'pausing' ? '暂缓停调' : '状态异常' : guardWarning(account) ? '告警 · 继续调度' : statuses[account.state])
}
const ratio = a => a.sample.length ? Math.round(a.slow_count / a.sample.length * 100) : 0
const title = computed(() => ({ overview: '调度总览', accounts: '账号号池', usage: '账号调用记录', import: '快速上号', rates: '用户倍率巡检', rules: '调度规则', events: '操作日志', mail: '邮件通知', invoices: '发票管理', settings: '连接设置' }[page.value]))

function handleAuthExpired() {
  auth.value = { ...auth.value, authenticated: false }
  settings.value = null
}

function handlePopState() {
  syncingRoute.value = true
  page.value = pageFromPath(globalThis.location.pathname)
  queueMicrotask(() => { syncingRoute.value = false })
}

watch(page, value => {
  if (value !== 'import') relogin.value = null
  if (syncingRoute.value) return
  const path = routePaths[value] || routePaths.overview
  if (globalThis.location.pathname !== path) globalThis.history.pushState({}, '', path)
  if (value === 'usage' && settings.value) void loadUsage()
})

function notify(message, error = false) {
  toast.value = { message, error }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = null }, error ? 9000 : 4500)
}
function handleApiError(error) {
  if (error.status === 401) {
    auth.value = { ...auth.value, authenticated: false }
    loadError.value = '登录已过期，请重新登录'
  }
}
async function action(fn) {
  if (busy.value) return
  busy.value = true
  try { await fn() } catch (error) { notify(error.message, true) } finally { busy.value = false }
}
async function refresh() {
  if (!auth.value.authenticated) return
  try { data.value = await api('/dashboard'); loadError.value = '' } catch (error) { handleApiError(error); loadError.value = error.message }
}
async function loadUsage() {
  if (!auth.value.authenticated || !data.value.configured || usageLoading.value) return
  usageLoading.value = true
  try {
    const result = await api('/usage/pools?limit=15')
    usagePoolRecords.value = Object.fromEntries(pools.map(pool => [pool.id, result.pools?.[pool.id]?.items || []]))
    usageTotal.value = Object.values(usagePoolRecords.value).reduce((total, items) => total + items.length, 0)
  } catch (error) {
    loadError.value = error.message
  } finally {
    usageLoading.value = false
  }
}
async function loadRanking() {
  if (!auth.value.authenticated || !data.value.configured || rankingLoading.value) return
  rankingLoading.value = true
  try {
    const result = await api('/usage/ranking')
    ranking.value = result.items || []
    rankingSampleSize.value = result.sample_size || 0
    rankingUpdatedAt.value = Date.now() / 1000
  } catch (error) {
    handleApiError(error); loadError.value = error.message
  } finally {
    rankingLoading.value = false
  }
}
async function loadWorkspace() {
  settings.value = await api('/settings')
  importOptions.value = clone(settings.value.import_options)
  await refresh()
  if (data.value.configured) {
    await loadMetadata()
    await loadRanking()
    if (page.value === 'usage') await loadUsage()
  }
}
async function handleAuthenticated(result) {
  auth.value = { ...auth.value, authenticated: true, username: result.username }
  loadError.value = ''
  try { await loadWorkspace() } catch (error) { handleApiError(error); notify(error.message, true) }
}
async function logout() {
  try { await api('/auth/logout', 'POST') } catch (error) { notify(error.message, true); return }
  auth.value = { ...auth.value, authenticated: false }
  settings.value = null
  data.value = { accounts: [], events: [], inspections: [], batches: [], worker: {}, configured: false }
}
function isUsageColumnVisible(column) {
  return settings.value?.usage_visible_columns?.includes(column) ?? true
}
async function saveUsageColumns() {
  await action(async () => {
    settings.value = await api('/settings', 'PUT', settings.value)
    notify('调用记录列设置已保存')
  })
}
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  globalThis.localStorage?.setItem('flowpool.sidebar.collapsed', sidebarCollapsed.value ? '1' : '0')
}
async function loadMetadata() { metadata.value = await api('/metadata') }
async function saveSettings() {
  await action(async () => { settings.value = await api('/settings', 'PUT', settings.value); notify('设置已保存，即时生效'); await refresh() })
}
async function testConnection() {
  await action(async () => { metadata.value = await api('/connection/test', 'POST', settings.value); notify(`连接成功，读取到 ${metadata.value.groups.length} 个 GPT 分组`) })
}
async function toggleScheduler() {
  await action(async () => {
    const current = await api('/settings')
    current.rule.enabled = !current.rule.enabled
    settings.value = await api('/settings', 'PUT', current)
    notify(current.rule.enabled ? '自动调度已开启' : '已停止新增自动暂停，已有冷却账号仍将按时恢复')
  })
}
async function openEnroll() {
  enrollPool.value = pools.some(pool => pool.id === poolFilter.value) ? poolFilter.value : 'priority'
  modal.value = true
  selected.value = []
  remoteLoaded.value = false
  await action(async () => { await loadMetadata(); await queryRemote() })
}
function localAccount(accountId) {
  return data.value.accounts.find(account => account.id === accountId)
}
function isPoolLocked(account) {
  const local = localAccount(account.id)
  return Boolean(local && local.pool !== enrollPool.value)
}
function selectableRemoteAccounts() {
  return remoteAccounts.value.filter(account => !isPoolLocked(account))
}
function syncEnrollSelection() {
  const selectedIds = new Set(selected.value)
  selected.value = remoteAccounts.value.filter(account => {
    const local = localAccount(account.id)
    return local ? local.pool === enrollPool.value : selectedIds.has(account.id)
  }).map(account => account.id)
}
function changeEnrollPool() {
  syncEnrollSelection()
}
function toggleRemoteSelection() {
  const available = selectableRemoteAccounts()
  const availableIds = new Set(available.map(account => account.id))
  const allSelected = available.length > 0 && available.every(account => selected.value.includes(account.id))
  selected.value = allSelected
    ? selected.value.filter(accountId => !availableIds.has(accountId))
    : [...new Set([...selected.value, ...available.map(account => account.id)])]
}
async function queryRemote() {
  remoteAccounts.value = await api(`/remote/accounts?search=${encodeURIComponent(remoteSearch.value)}&group=${encodeURIComponent(remoteGroup.value)}&account_type=oauth`)
  remoteAccounts.value.sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0) || b.id - a.id)
  syncEnrollSelection()
  remoteLoaded.value = true
}
async function enroll() {
  await action(async () => {
    const result = await api('/accounts/enroll', 'POST', { account_ids: selected.value, pool: enrollPool.value })
    modal.value = false; notify(`已将 ${result.count} 个账号录入${poolName(enrollPool.value)}`); await refresh()
  })
}
async function setAccountGuard(account, event) {
  const enabled = event.target.checked
  await action(async () => {
    await api(`/accounts/${account.id}/guard`, 'PUT', { enabled })
    notify(enabled ? '账号风控已开启' : '账号风控已关闭，已有冷却仍按时恢复')
    await refresh()
  })
  event.target.checked = isGuarded(data.value.accounts.find(item => item.id === account.id) || account)
}
async function setPoolGuard(pool, event) {
  const enabled = event.target.checked
  await action(async () => {
    const result = await api(`/pools/${pool}/guard`, 'PUT', { enabled })
    settings.value.rule.guarded_pools = result.guarded_pools
    notify(`${poolName(pool)}全部账号风控已${enabled ? '开启' : '关闭'}`)
    await refresh()
  })
  event.target.checked = poolGuardEnabled(pool)
  event.target.indeterminate = poolGuardMixed(pool)
}
async function accountAction(account, operation) {
  await action(async () => {
    await api(`/accounts/${account.id}/${operation}`, 'POST')
    const resumed = isGuarded(account) ? (account.resume_at != null ? '已结束冷却，恢复调度并进入新一轮观察' : '已恢复调度，进入新一轮观察') : '已恢复调度'
    notify({ pause: '账号已暂停，需手动恢复', resume: account.pool === 'third_party' || account.cooldown_mode === 'image' ? '已恢复全部正常 GPT 分组（含生图）并开启调度' : resumed, remove: '已移出本地号池并结束托管，上游状态保持不变' }[operation]); await refresh()
  })
}
async function selectBatch(value) {
  batch.value = value
  await nextTick()
  batchPanel.value?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
}
function openRelogin(item, sourceBatch = batch.value) {
  if (busy.value || loginLoading.value) return
  relogin.value = { batch: clone(sourceBatch), item: clone(item), source: loginForm.value?.credentialsFor(item.email) || '' }
}
function batchChecked(result) {
  if (batch.value?.id === result.id) batch.value = result
  void refresh()
}
async function reloginUpdated(result) {
  batch.value = result
  historyRevision.value++
  const item = result.items.find(item => item.index === relogin.value?.item.index)
  if (item?.status === 'done') notify(`${item.name} 已重新登录并推送入池`)
  await refresh()
}
async function useLoginBatch(preview) {
  batch.value = preview
  filePayload.value = null
  fileName.value = ''
  notify(`已准备 ${preview.items.length} 个账号，请核对后确认推送`)
  await nextTick()
  batchPanel.value?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
  await refresh()
}
async function readFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  batch.value = null; filePayload.value = null; fileName.value = ''
  try {
    if (file.size > 7 * 1024 * 1024) throw new Error('JSON 文件不能超过 7 MB')
    filePayload.value = JSON.parse((await file.text()).replace(/^\uFEFF/, ''))
    fileName.value = file.name
  } catch (error) { notify(error instanceof SyntaxError ? 'JSON 格式无效，请检查文件内容' : error.message, true) }
  event.target.value = ''
}
async function saveImportDefaults(event) {
  if (busy.value || !event.currentTarget.form.reportValidity()) return
  busy.value = true
  importSettingsError.value = ''
  try {
    const saved = await api('/settings')
    saved.import_options = importOptions.value
    settings.value = await api('/settings', 'PUT', saved)
    importSettingsDialog.value.close()
    notify('已保存为默认上号参数')
  } catch (error) { importSettingsError.value = error.message }
  finally { busy.value = false }
}
async function previewImport() {
  await action(async () => {
    batch.value = await api('/import/preview', 'POST', { payload: filePayload.value, options: importOptions.value })
    notify('预览已生成，请核对账号后推送入池'); await refresh()
  })
}
async function commitImport() {
  await action(async () => {
    batch.value = await api(`/import/${batch.value.id}/commit`, 'POST')
    const done = batch.value.items.filter(i => i.status === 'done').length
    notify(`批次处理完成：${done} 个账号已入池${batch.value.items.some(i => !['done', 'skipped'].includes(i.status)) ? '，部分账号需查看结果后重试' : ''}`)
    settings.value = await api('/settings'); filePayload.value = null; await refresh()
  })
}
async function discardBatch(id) {
  await action(async () => { await api(`/import/${id}`, 'DELETE'); if (batch.value?.id === id) batch.value = null; historyRevision.value++; await refresh(); notify('已清除本地导入批次，上游账号保留') })
}
function sampleHeight(value) { return Math.max(5, Math.min(100, value / Math.max(settings.value.rule.threshold_ms * 2, ...currentDetail.value.sample.map(s => s.first_token_ms)) * 100)) }
function viewPool(pool) { poolFilter.value = pool; page.value = 'accounts' }
function downloadExample() {
  const value = [{ platform: 'openai', email: 'account@example.com', credentials: { access_token: '替换为实际 access_token', refresh_token: '替换为实际 refresh_token', chatgpt_account_id: '替换为实际账号 ID' } }]
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = 'accounts.example.json'; link.click(); URL.revokeObjectURL(url)
}
async function loadAuthStatus() {
  authStatusError.value = ''
  try {
    const result = await api('/auth/status')
    auth.value = { ready: true, ...result }
    if (result.authenticated) await loadWorkspace()
  } catch (error) {
    auth.value = { ready: true, configured: false, authenticated: false, username: '' }
    authStatusError.value = error.message
  }
}
onMounted(async () => {
  window.addEventListener('flowpool-auth-expired', handleAuthExpired)
  globalThis.addEventListener('popstate', handlePopState)
  await loadAuthStatus()
  polling = setInterval(() => { void refresh(); if (page.value === 'usage') void loadUsage() }, refreshIntervalSeconds * 1000)
  rankingPolling = setInterval(() => { void loadRanking() }, 30000)
  clock = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
})
onUnmounted(() => {
  window.removeEventListener('flowpool-auth-expired', handleAuthExpired)
  globalThis.removeEventListener('popstate', handlePopState); clearInterval(polling); clearInterval(rankingPolling); clearInterval(clock); clearTimeout(toastTimer) })
</script>

<template>
  <AdminLogin v-if="auth.ready && !auth.authenticated" :configured="auth.configured" :status-error="authStatusError" @retry-status="loadAuthStatus" @authenticated="handleAuthenticated"/>
  <div v-else-if="auth.ready" :class="['app-shell', { 'sidebar-collapsed': sidebarCollapsed }]">
    <aside class="sidebar">
      <a href="#" class="brand" @click.prevent="page = 'overview'"><img class="brand-mark" src="/favicon.svg" alt="" width="40" height="40"/><span>FlowPool<small>GPT 账号调控</small></span></a>
      <SystemUpgrade :busy="busy || loginLoading"/>
      <div class="workspace-label">WORKSPACE <span>本地</span></div>
      <nav aria-label="主导航"><button v-for="item in nav" :key="item.id" :class="['nav-item', { selected: page === item.id }]" @click="page = item.id"><component :is="item.icon" :size="18"/><span>{{ item.name }}</span><span v-if="item.id === 'accounts' && data.accounts.length" class="nav-count">{{ data.accounts.length }}</span></button></nav>
      <div class="sidebar-bottom"><div class="platform-card"><div class="platform-icon"><Sparkles :size="18"/></div><div><strong>GPT / OpenAI</strong><small>覆盖所有上游分组</small></div><span class="dot green"></span></div><button :class="['nav-item', { selected: page === 'settings' }]" @click="page = 'settings'"><Settings2 :size="18"/><span>连接设置</span><span v-if="data.configured" class="dot green"></span></button><button class="collapse-toggle" :aria-label="sidebarCollapsed ? '展开菜单' : '收起菜单'" @click="toggleSidebar"><ChevronRight :size="16"/><span>{{ sidebarCollapsed ? '展开菜单' : '收起菜单' }}</span></button><div class="sidebar-footer"><span>FlowPool <b>v1.0</b></span><span>LOCAL FIRST</span></div></div>
    </aside>

    <main>
      <header class="topbar"><div class="breadcrumb">工作空间<ChevronRight :size="13"/><strong>{{ title }}</strong></div><div class="topbar-right"><span class="connection"><span :class="['dot', data.configured ? 'green' : 'gray']"></span>{{ data.configured ? '服务已配置' : '等待连接' }}</span><span class="top-divider"></span><button class="avatar" title="连接设置" aria-label="打开连接设置" @click="page = 'settings'">管</button><span>{{ auth.username || '管理员' }}</span><button class="text-button" @click="logout"><LogOut :size="14"/>退出</button></div></header>
      <div class="main-content">
        <div v-if="loadError" class="notice error"><Unplug :size="17"/>{{ loadError }}<button class="text-button" @click="refresh">重试</button></div>
        <div v-if="['overview', 'accounts', 'events'].includes(page)" class="page-actions"><template v-if="['overview', 'accounts'].includes(page)"><button class="button" :disabled="busy" @click="openEnroll"><Plus :size="16"/>录入已有账号</button><button class="button primary" @click="page = 'import'"><CloudUpload :size="16"/>快速上号</button></template><span v-else class="badge neutral">保留最近 10,000 条 · 展示 100 条</span></div>

        <template v-if="settings">
          <template v-if="['overview', 'accounts'].includes(page)">
            <div v-if="!data.configured" class="setup-banner"><div class="setup-icon"><Unplug :size="22"/></div><div><strong>连接 Sub2API，开启自动调度</strong><p>配置管理员 API Key 后，即可读取 GPT 账号、分组和调用记录。</p></div><button class="button" @click="page = 'settings'">配置连接<ArrowRight :size="15"/></button></div>
            <div v-if="page === 'overview'" class="stats-grid"><div class="stat-card"><div class="stat-label">托管账号 <Layers3 :size="17"/></div><div class="stat-value">{{ data.accounts.length }}<span>个</span></div><div class="stat-foot">覆盖 {{ new Set(data.accounts.flatMap(a => a.remote.groups.map(g => g.id))).size }} 个上游分组</div></div><div class="stat-card"><div class="stat-label">正常调度 <Zap :size="17" class="teal-text"/></div><div class="stat-value">{{ healthy }}<span>个</span></div><div class="stat-foot"><span class="dot green"></span>账号可调度且检查正常</div></div><div class="stat-card"><div class="stat-label">冷却暂停 <Clock3 :size="17" class="orange-text"/></div><div class="stat-value">{{ cooling }}<span>个</span></div><div class="stat-foot">冷却结束后自动恢复</div></div><div class="stat-card"><div class="stat-label">恢复观察 <ShieldCheck :size="17" class="purple-text"/></div><div class="stat-value">{{ watching }}<span>个</span></div><div class="stat-foot">只统计恢复后的调用</div></div><PerformanceMetrics/></div>
            <section class="panel rule-strip"><div class="rule-icon"><Activity :size="21"/></div><div class="rule-strip-content"><div class="inline"><strong>首字延迟守护</strong><span :class="['badge', settings.rule.enabled ? 'success' : 'neutral']">{{ settings.rule.enabled ? '自动调度已开启' : '自动调度未开启' }}</span><span v-if="settings.rule.alert_only" class="badge neutral">仅告警模式</span></div><p>守护范围：<b>{{ guardedPoolNames }}</b>。最近 <b>5 分钟</b> 内的最新 <b>{{ settings.rule.sample_size }}</b> 次有效调用，首字延迟 &gt; <b>{{ settings.rule.threshold_ms / 1000 }}s</b> 占比 ≥ <b>{{ Math.round(settings.rule.slow_ratio * 100) }}%</b> 计一次达限，<template v-if="settings.rule.alert_only">达到阈值后只记录告警并继续调度</template><template v-else><b>{{ settings.rule.breach_window_seconds }} 秒</b> 内累计 <b>{{ settings.rule.breach_count }} 次</b> 才停调；再次计次至少间隔 <b>{{ settings.rule.breach_min_interval_seconds }} 秒</b>、新增 <b>{{ Math.ceil(settings.rule.sample_size * settings.rule.breach_fresh_ratio) }} 条</b> 样本。每次冷却 <b>{{ settings.rule.cooldown_seconds / 60 }} 分钟</b>，恢复观察 <b>{{ settings.rule.probation_seconds }}s</b></template>。</p></div><button class="text-button" @click="page = 'rules'">配置规则<ChevronRight :size="15"/></button><button :class="['switch', { on: settings.rule.enabled }]" role="switch" :aria-checked="settings.rule.enabled" aria-label="自动调度开关" :disabled="busy" @click="toggleScheduler"><span></span></button></section>
            <div class="section-heading"><h2>我的号池 <span>仅在本地分类</span></h2><span class="muted small">{{ data.accounts.length }} 个已托管账号</span></div>
            <div class="pool-grid"><button v-for="pool in pools" :key="pool.id" :class="['pool-card', pool.color, { chosen: page === 'accounts' && poolFilter === pool.id }]" @click="viewPool(pool.id)"><div class="pool-icon"><component :is="pool.icon" :size="20"/></div><div class="pool-text"><strong>{{ pool.name }}</strong><small>{{ pool.desc }}</small></div><div class="pool-count">{{ data.accounts.filter(a => a.pool === pool.id).length }}<ChevronRight :size="16"/></div></button></div>
            <section class="panel accounts-panel"><div class="panel-heading"><div><h2>账号列表 <span class="count-chip">{{ filtered.length }}</span></h2><p>实时状态与最近调用表现</p></div><button class="button" title="点击立即检查账号；页面按显示间隔自动刷新" :disabled="busy" @click="action(async () => { await api('/scheduler/tick', 'POST'); await refresh(); notify('本轮检查已完成') })"><RefreshCw :size="17" :class="{ spinning: busy }"/>立即检查 · {{ refreshIntervalSeconds }} 秒刷新</button></div>
              <div class="table-toolbar"><div class="tabs"><button :class="{ active: poolFilter === 'all' }" @click="poolFilter = 'all'">全部账号</button><button v-for="pool in pools" :key="pool.id" :class="{ active: poolFilter === pool.id }" @click="poolFilter = pool.id">{{ pool.name }}</button></div><div class="filters"><label class="search-input"><Search :size="15"/><input v-model="search" placeholder="搜索名称或 ID" aria-label="搜索本地账号" /></label><select v-model="stateFilter" aria-label="筛选调度状态"><option value="all">全部状态</option><option v-for="(label, value) in statuses" :key="value" :value="value">{{ label }}</option></select></div></div>
              <div class="table-scroll"><table><thead><tr><th>账号名称</th><th>本地号池</th><th>调度状态</th><th>账号风控</th><th>账号额度</th><th>状态说明</th><th>慢调用占比 / 达限次数</th><th>下一步 / 最近检查</th><th class="align-right">操作</th></tr></thead><tbody><tr v-for="account in filtered" :key="account.id"><td><button class="account-name" @click="detail = account.id"><span class="account-avatar">{{ account.name.charAt(0).toUpperCase() }}</span><span><strong>{{ account.name }}</strong><small>#{{ account.id }} · {{ account.remote.type }} · {{ account.remote.groups.map(g => g.name).join(' / ') || '未绑定分组' }}</small></span></button></td><td><span :class="['pool-tag', account.pool]">{{ poolName(account.pool) }}</span></td><td><span :class="['badge', account.error || availabilityIssue(account) || guardWarning(account) ? 'warning' : account.state]"><span class="dot"></span>{{ scheduleLabel(account) }}</span><small v-if="account.error" class="cell-error" :title="account.error">{{ account.error }}</small></td><td><label class="check-row"><input type="checkbox" :checked="isGuarded(account)" :disabled="busy || !isPoolGuarded(account.pool)" :title="isPoolGuarded(account.pool) ? '修改账号风控' : '请先将所属分组纳入守护'" :aria-label="`账号 ${account.id} 风控`" @change="setAccountGuard(account, $event)"/>{{ isGuarded(account) ? '开启' : '关闭' }}</label></td><td><AccountQuota :account="account" :now="now" /></td><td><span class="status-explain">{{ sampleStatus(account) }}</span><small v-if="account.pool === 'third_party' && account.relogin_count > 0" class="muted">{{ account.relogin_failures > 3 ? `自动重登 · 成功${account.relogin_count} 次，失败 ${account.relogin_failures} 次 · 需手动处理` : `自动重登 · 成功${account.relogin_count} 次，失败 ${account.relogin_failures || 0} 次` }}</small></td><td><div class="ratio-cell"><span>{{ account.sample.length ? ratio(account) + '%' : '—' }}</span><small>{{ account.slow_count }} / {{ account.sample.length }} 次</small></div><div class="progress-track"><span :style="{ width: ratio(account) + '%' }" :class="{ danger: ratio(account) >= settings.rule.slow_ratio * 100 }"></span></div><small v-if="isGuarded(account)" class="muted">窗口内达限 {{ breachCount(account) }} / {{ settings.rule.breach_count }} 次</small></td><td><span v-if="account.state === 'cooldown'" class="small orange-text">{{ cooldownText(account) }}</span><span v-else-if="account.state === 'probation'" class="small purple-text">{{ account.probation_until > now ? remaining(account.probation_until) + '后评估' : '等待新调用评估' }}</span><span v-else class="muted small">最近 {{ lastCheckText(account) }}</span><small class="next-check">下一次 {{ nextCheckText(account) }}</small></td><td><div class="row-actions"><button class="icon-button" title="查看调用样本" aria-label="查看调用样本" @click="detail = account.id"><Activity :size="16"/></button><button v-if="['cooldown','manual'].includes(account.state) || account.remote.schedulable === false" class="icon-button" title="恢复调度" aria-label="恢复调度" :disabled="busy" @click="accountAction(account, 'resume')"><Play :size="16"/></button><button v-else class="icon-button" title="手动暂停" aria-label="手动暂停" :disabled="busy" @click="accountAction(account, 'pause')"><Pause :size="16"/></button><button class="icon-button" title="移出本地号池" aria-label="移出本地号池" :disabled="busy" @click="accountAction(account, 'remove')"><X :size="15"/></button></div></td></tr></tbody></table></div>
              <div v-if="!filtered.length" class="empty-state"><div class="empty-orbit"><Layers3 :size="30"/></div><h3>{{ data.accounts.length ? '没有符合条件的账号' : '你的号池，准备就绪' }}</h3><p>{{ data.accounts.length ? '试试调整筛选条件或搜索关键词。' : '录入已有账号，或上传 JSON 文件，开始第一次自动调度。' }}</p><button v-if="!data.accounts.length" class="button" :disabled="busy" @click="data.configured ? openEnroll() : page = 'settings'"><Plus :size="15"/>{{ data.configured ? '录入第一个账号' : '连接 Sub2API' }}</button></div>
              <div class="table-footer"><span><span class="dot green"></span>每 {{ settings.rule.poll_seconds }} 秒自动检查 · 页面每 {{ refreshIntervalSeconds }} 秒刷新</span><span>最近检查 {{ shortTime(data.worker.last_tick) }}</span></div>
            </section>
            <section v-if="page === 'overview'" class="panel recent-events"><div class="panel-heading"><h2>最近动态</h2><button class="text-button" @click="page = 'events'">全部日志<ArrowRight :size="14"/></button></div><div v-if="!data.events.length" class="quiet-empty">暂时没有操作记录。一切从连接你的服务开始。</div><div v-for="event in data.events.slice(0, 4)" :key="event.id" class="event-row"><span :class="['event-dot', event.level]"><Check :size="12"/></span><span>{{ event.message }}</span><small v-if="event.account_id">#{{ event.account_id }}</small><time>{{ timeText(event.created_at) }}</time></div></section>
          </template>

          <template v-if="page === 'settings'"><div class="connection-settings-grid"><form class="settings-layout" @submit.prevent="saveSettings"><section class="panel form-panel"><div class="card-title"><div class="tinted-icon"><Unplug :size="21"/></div><div><h2>Sub2API 连接</h2><p>所有远端操作均通过管理员接口执行</p></div><span :class="['badge', settings.key_configured ? 'success' : 'neutral']">{{ settings.key_configured ? 'Key 已设置' : '待配置' }}</span></div><label>Sub2API 地址<input v-model.trim="settings.base_url" type="url" disabled placeholder="https://your-sub2api.example.com" required/><small>与管理员登录共用同一后端。切换地址需在服务器配置 FLOWPOOL_SUB2API_URL 并重启。</small></label><label>管理员 API Key<input v-model="settings.admin_key" type="password" autocomplete="new-password" :placeholder="settings.key_configured ? '已设置，留空保持不变' : '输入 Sub2API 管理员 API Key'"/><small>在 Sub2API 后台生成，用于 /api/v1/admin/*。</small></label><label>鉴权方式<input value="X-API-Key · 管理员 Key" disabled/></label><div class="form-actions"><button class="button primary" :disabled="busy"><Check :size="16"/>保存设置</button><button type="button" class="button" :disabled="busy" @click="testConnection"><RefreshCw :size="16"/>测试连接</button></div></section><aside class="help-panel"><ShieldCheck :size="26"/><h3>你的服务，你的数据</h3><p>管理员 Key 加密保存于本机，页面不会回显。账号分类与调度状态存储在独立的 SQLite 数据库。</p><div class="help-line"><CheckCheck :size="16"/>仅管理 GPT / OpenAI 平台</div><div class="help-line"><CheckCheck :size="16"/>已有账号录入仅保存本地分类</div><div class="help-line"><CheckCheck :size="16"/>不通过页面传输管理员 Key 到第三方</div><hr/><p>本地服务默认仅监听 127.0.0.1。此版本面向单机管理员使用。</p></aside></form><PostgresSettings @notify="notify"/><section class="panel form-panel defaults-panel"><div class="card-title"><div><h2>默认上号参数</h2><p>保存后用于下一次打开快速上号页面</p></div></div><form @submit.prevent="saveSettings"><ImportOptions :model="settings.import_options" :groups="metadata.groups" :proxies="metadata.proxies" :busy="busy" @refresh="action(loadMetadata)"/><div class="form-actions"><button class="button primary" :disabled="busy">保存默认参数</button><button type="button" class="button" @click="importOptions = clone(settings.import_options); page = 'import'">使用这些参数上号<ArrowRight :size="15"/></button></div></form></section></div></template>

          <template v-if="page === 'rules'"><form @submit.prevent="saveSettings"><section class="panel form-panel"><div class="card-title"><div class="tinted-icon"><ShieldCheck :size="22"/></div><div><h2>首字延迟守护</h2><p>对选中的本地号池自动采样、暂停、冷却和恢复观察</p></div><label class="check-row"><input v-model="settings.rule.enabled" type="checkbox"/>开启自动调度</label><label class="check-row"><input v-model="settings.rule.alert_only" type="checkbox"/>只告警，不自动冷却</label></div><fieldset class="guard-pools"><legend>分组风控（批量设置账号）</legend><div class="guard-pool-options"><label v-for="pool in pools" :key="pool.id" class="check-row"><input type="checkbox" :checked="poolGuardEnabled(pool.id)" :indeterminate="poolGuardMixed(pool.id)" :disabled="busy" @change="setPoolGuard(pool.id, $event)"/>{{ pool.name }}（{{ poolGuardCount(pool.id) }}/{{ poolAccountCount(pool.id) }}）</label></div><small>分组开关立即更新组内全部账号；仅已纳入守护的分组支持单独修改账号风控，其它分组的账号开关禁用。新录入账号默认沿用分组设置，总开关关闭时暂停全部风控判断。账号风控仅控制首字延迟守护；关闭后已有冷却仍会按时恢复。</small></fieldset><div class="notice compact"><CircleHelp :size="15"/><span>冷却前先检查高权重组：已有可用账号正在调度则不启用新账号；否则优先启用三方账号，三方无可用账号则启用高权重账号。两组均无可用账号时，当前账号继续调度，不进入冷却。</span></div><div class="flow-diagram"><div><span class="flow-num">01</span><Activity :size="22"/><strong>监测调用</strong><small>5 分钟内最近 {{ settings.rule.sample_size }} 条有效样本</small></div><ArrowRight :size="19"/><div><span class="flow-num">02</span><Pause :size="22"/><strong>{{ settings.rule.alert_only ? '达限后继续调度' : '达限累计后冷却' }}</strong><small>{{ settings.rule.alert_only ? '只记录告警，不停止调度' : `${settings.rule.breach_count} 次后冷却，${settings.rule.cooldown_seconds / 60} 分钟后恢复` }}</small></div><ArrowRight :size="19"/><div><span class="flow-num">03</span><ShieldCheck :size="22"/><strong>恢复观察</strong><small>{{ settings.rule.probation_seconds }} 秒，仅看新调用</small></div><ArrowRight :size="19"/><div><span class="flow-num">04</span><Zap :size="22"/><strong>继续调度</strong><small>{{ settings.rule.alert_only ? '持续记录达限告警' : '达限次数达标才重新冷却' }}</small></div></div><div class="form-grid rules-fields"><label>采样窗口（条）<input v-model.number="settings.rule.sample_size" type="number" min="1" max="100" required/><small>固定统计最近 5 分钟，正常阶段需凑满样本才判断</small></label><label>首字延迟阈值（毫秒）<input v-model.number="settings.rule.threshold_ms" type="number" min="100" max="300000" required/><small>严格大于阈值的调用才记为慢调用</small></label><label>慢调用比例阈值（0–1）<input v-model.number="settings.rule.slow_ratio" type="number" min="0" max="1" step="0.01" required/><small>占比达到或超过阈值计一次达限，0.5 表示 50%</small></label><label>达限滚动窗口（秒）<input v-model.number="settings.rule.breach_window_seconds" type="number" min="10" max="86400" required/><small>默认 120 秒，窗口随当前时间滚动，过期达限自动失效</small></label><label>窗口内停调次数<input v-model.number="settings.rule.breach_count" type="number" min="1" max="100" required/><small>默认 2 次；再次计次需同时满足间隔和新样本要求</small></label><label>计次最小间隔（秒）<input v-model.number="settings.rule.breach_min_interval_seconds" type="number" min="1" max="3600" required/><small>默认 30 秒，从上次实际计次开始计算</small></label><label>再次计次新样本比例（0–1）<input v-model.number="settings.rule.breach_fresh_ratio" type="number" min="0.01" max="1" step="0.01" required/><small>默认 50%，至少 {{ Math.ceil(settings.rule.sample_size * settings.rule.breach_fresh_ratio) }} 条新调用；按配置样本量向上取整</small></label><label>冷却时长（秒）<input v-model.number="settings.rule.cooldown_seconds" type="number" min="10" max="86400" required/><small>默认 1200 秒，每次固定 20 分钟，不随次数递增</small></label><label>恢复观察时长（秒）<input v-model.number="settings.rule.probation_seconds" type="number" min="10" max="3600" required/><small>观察到期后按新样本累计达限；恢复时清空旧次数</small></label><label>检查间隔（秒）<input v-model.number="settings.rule.poll_seconds" type="number" min="5" max="300" required/><small>单轮完成后等待此间隔，再开始下一轮</small></label></div><div class="notice"><CircleHelp :size="18"/><span>只有开启账号风控的账号参与首字延迟自动判断；总开关关闭时停止新的自动判断。调用有效统计窗口固定为最近 5 分钟，窗口外的历史调用不参与判断；没有有效调用时清除告警。<template v-if="settings.rule.alert_only">当前为仅告警模式，达限累计仍会记录并发送告警，但不会停止调度。</template><template v-else>达限先告警，滚动窗口内次数达标才冷却。每次冷却固定等待 {{ settings.rule.cooldown_seconds / 60 }} 分钟后恢复，不随冷却次数递增。三方账号保持调度，仅绑定正常的「生图」分组；冷却结束或手动结束冷却后，重新绑定所有正常状态的 GPT 分组。风控组及高权重组冷却仍停止调度。</template>再次计次必须同时满足最小间隔和新样本比例；新样本相对上次实际计次的调用 ID 水位判断，数量按配置样本量向上取整，恢复观察阶段也不降低新样本要求。三方账号另检查认证状态（包含仅生图账号）：守护总开关开启时，已保存账密的账号发生 401 / 认证失效会自动重登；JSON 或历史未保存账密账号需手动处理。每次重登至少间隔 5 分钟；连续失败超过 3 次（第 4 次失败）后放弃自动重登，重启或等待不解除。成功重登后清零失败次数；已放弃账号需人工成功重登后恢复。手动暂停或配置变化导致的取消不计为失败。三方冷却期间不采样，不会因调度开关保持开启而提前结束冷却；生图分组缺失或非正常状态时不执行分组切换。次数过期不会把相同样本重新计次；恢复调度或修改达限规则后重新累计达限次数。自动冷却前只检查上游「Pro号池」是否有其它可用的本地托管账号，不要求覆盖当前账号的其它上游分组。所有守护分组采用相同替补规则：优先复用正在调度的高权重账号，否则选择三方账号，再选择高权重账号，不选择风控账号作为替补。候选须为 active、未限流且不在本地冷却或状态切换中，手动暂停账号可被启用。三方已有可用账号调度时直接复用。两组均不可用或启用失败时不允许冷却。进入冷却后只复核本次保障账号一次，复核失败则恢复当前账号；冷却期间不再检查替补。恢复后同时应用 5 分钟窗口、新的时间边界与记录 ID 水位，排除旧调用和恢复前在途请求。观察通过后也保留边界，防止旧慢调用再次触发。所有号池均可手动暂停 / 恢复。</span></div><div class="form-actions"><button class="button primary" :disabled="busy"><Check :size="16"/>保存调度规则</button></div></section></form>
            <section class="panel cooldown-panel" aria-labelledby="cooldown-title">
              <div class="panel-heading">
                <div><h2 id="cooldown-title">达限累计后冷却 <span class="count-chip">{{ coolingAccounts.length }}</span></h2><p>三方组冷却仅保留生图调度，到期恢复所有正常 GPT 分组；其它组到期恢复调度。支持立即结束冷却。</p></div>
                <button class="button" :disabled="busy" @click="refresh"><RefreshCw :size="15"/>刷新 · 每 {{ refreshIntervalSeconds }} 秒</button>
              </div>
              <div v-if="!coolingAccounts.length" class="quiet-empty">当前没有冷却中的账号</div>
              <div v-else class="table-scroll">
                <table><thead><tr><th>账号</th><th>冷却原因</th><th>剩余冷却时间</th><th>预计恢复时间</th><th class="align-right">操作</th></tr></thead>
                  <tbody><tr v-for="account in coolingAccounts" :key="account.id">
                    <td><button class="account-name" @click="detail = account.id"><span><strong>{{ account.name }}</strong><small>#{{ account.id }} · {{ poolName(account.pool) }}</small></span></button></td>
                    <td class="cooldown-reason">{{ account.reason || '达限累计次数达到冷却条件' }}</td>
                    <td><strong class="cooldown-countdown orange-text">{{ cooldownText(account) }}</strong><small class="muted">连续第 {{ account.cooldown_count || 1 }} 次冷却</small><small v-if="account.error" class="cooldown-error">{{ account.error }}</small></td>
                    <td class="muted small">{{ timeText(account.resume_at) }}</td>
                    <td class="align-right"><button class="button" :disabled="busy || (account.state === 'resuming' && !account.error)" @click="accountAction(account, 'resume')"><Play :size="15"/>{{ account.state === 'resuming' ? account.error ? '重试恢复' : '恢复中…' : account.cooldown_mode === 'image' ? '结束冷却，恢复全部分组' : '立即启动调度' }}</button></td>
                  </tr></tbody>
                </table>
              </div>
              <div class="table-footer"><span>到期后的下一轮巡检恢复，当前巡检间隔 {{ settings.rule.poll_seconds }} 秒；恢复后，仍在守护范围内的账号进入 {{ settings.rule.probation_seconds }} 秒观察期。</span></div>
            </section>
            <section class="panel inspection-panel"><div class="panel-heading"><div><h2>账号巡检记录 <span class="count-chip">{{ data.inspections.length }}</span></h2><p>{{ settings.rule.enabled ? '记录各守护分组的自动判断，包含每次采取的措施' : '自动守护已关闭，不再采样巡检；已有冷却仍按时恢复，历史记录继续保留' }}</p></div><button class="button" :disabled="busy" @click="refresh"><RefreshCw :size="15"/>刷新 · 每 {{ refreshIntervalSeconds }} 秒</button></div><div v-if="!data.inspections.length" class="empty-state"><Activity :size="28"/><h3>暂无巡检记录</h3><p>开启守护的分组出现新的有效调用后，这里会显示样本占比和实际措施。</p></div><div v-else class="table-scroll"><table><thead><tr><th>时间</th><th>账号</th><th>样本</th><th>超限占比</th><th>窗口内达限</th><th>采取措施</th><th>巡检说明</th></tr></thead><tbody><tr v-for="inspection in data.inspections" :key="inspection.id"><td class="muted small">{{ timeText(inspection.created_at) }}</td><td><strong>{{ accountName(inspection.account_id) }}</strong><small>#{{ inspection.account_id }} · {{ poolName(data.accounts.find(account => account.id === inspection.account_id)?.pool) || '已移出号池' }}</small></td><td>{{ inspection.slow_count }} / {{ inspection.sample_size }}</td><td>{{ inspection.sample_size ? Math.round(inspection.slow_ratio * 100) + '%' : '—' }}</td><td><template v-if="inspection.breach_limit != null">{{ inspection.breach_count }} / {{ inspection.breach_limit }} 次<small class="muted">最近 {{ inspection.breach_window_seconds }} 秒</small></template><span v-else class="muted">—</span></td><td><span :class="['badge', ['停止调度', '仅生图调度', '冷却切换未确认', '暂缓停调', '停调未确认', '恢复未确认', '告警', '自动重登失败', '暂停自动重登'].includes(inspection.action) ? 'warning' : ['保持调度', '提前恢复调度', '恢复全部分组', '自动重登成功'].includes(inspection.action) ? 'success' : 'neutral']">{{ inspection.action }}</span></td><td>{{ inspection.message }}</td></tr></tbody></table></div></section></template>

          <template v-if="page === 'import'"><div class="import-layout"><div class="import-right"><div class="import-toolbar"><div class="tabs" aria-label="上号方式"><button :class="{ active: importMode === 'login' }" :disabled="busy || loginLoading" @click="importMode = 'login'">账密快速上号</button><button :class="{ active: importMode === 'json' }" :disabled="busy || loginLoading" @click="importMode = 'json'">JSON 文件导入</button></div><button class="button" :disabled="busy || loginLoading" @click="importSettingsError = ''; importSettingsDialog.showModal()"><Settings2 :size="16"/>上号设置</button></div><LoginImport ref="loginForm" v-show="importMode === 'login'" :busy="busy" :configured="data.configured" :options="importOptions" @loading="loginLoading = $event" @prepared="useLoginBatch" @started="batch = null"/><section v-show="importMode === 'json'" class="panel form-panel"><div class="card-title"><div class="tinted-icon"><FileJson :size="20"/></div><div><h2>上传账号</h2><p>先预览，再推送，一步开始调度</p></div><span class="badge neutral">JSON</span></div><input ref="fileInput" type="file" accept=".json,application/json" class="hidden" @change="readFile"/><button class="upload-zone" :disabled="busy || loginLoading" @click="fileInput.click()"><div class="upload-icon"><CloudUpload :size="28"/></div><strong>{{ fileName || '点击选择 JSON 文件' }}</strong><span>{{ fileName ? '文件已读取，点击可重新选择' : '支持单账号、账号数组、Sub2API 导出与 Codex auth.json' }}</span><small>最多 500 个账号 · 文件不超过 7 MB</small></button><div class="upload-hint"><span><ShieldCheck :size="14"/>仅推送到你配置的 Sub2API</span><button class="text-button" @click="downloadExample"><ArrowDownToLine :size="14"/>示例文件</button></div><div class="import-steps"><span :class="{ done: filePayload || batch }"><b>1</b>准备账号</span><ChevronRight :size="13"/><span :class="{ done: batch }"><b>2</b>校验预览</span><ChevronRight :size="13"/><span :class="{ done: batch?.items.some(i => i.status === 'done') }"><b>3</b>推送入池</span></div><button class="button primary full-width" :disabled="busy || loginLoading || !filePayload || !data.configured" @click="previewImport"><FileJson :size="16"/>{{ batch ? '按当前参数重新生成预览' : '校验并预览账号' }}</button><p v-if="!data.configured" class="small muted">请先到连接设置中保存管理员地址和 Key。</p></section>
            <section v-if="batch" ref="batchPanel" class="panel form-panel"><div class="card-title"><div><h2>导入预览 <span class="count-chip">{{ batch.items.length }}</span></h2><p>本次目标：{{ poolName(batch.pool) }} · 使用生成预览时的参数</p><p v-if="batch.options">上游分组：{{ batch.options.group_ids.map(id => metadata.groups.find(group => group.id === id)?.name || '#' + id).join(' / ') }} · 并发 {{ batch.options.concurrency }} · 优先级 {{ batch.options.priority }} · {{ batch.options.duplicate === 'update' ? '更新已有账号' : '跳过已有账号' }}</p><p v-if="batch.options?.model_mappings?.length" class="mapping-preview">模型映射：{{ batch.options.model_mappings.map(item => `${item.source} → ${item.target}`).join('；') }}</p><p v-else>模型映射：保留账号 JSON 原有配置，无原配置则不设置</p></div></div><div class="import-results"><div v-for="item in batch.items" :key="item.index" class="import-result"><FileJson :size="17"/><div><strong>{{ item.name }}</strong><small>{{ item.message || item.email || '校验通过，等待推送' }}</small></div><span :class="['badge', item.status === 'done' ? 'success' : ['failed', 'uncertain', 'updating'].includes(item.status) ? 'warning' : 'neutral']">{{ { pending: '待推送', done: '已入池', skipped: '已跳过', failed: '失败', uncertain: '待核对', updating: '待重试', creating: '创建中', created: '待入池' }[item.status] }}</span><button class="button" :disabled="busy || loginLoading || !data.configured" @click="openRelogin(item)"><RefreshCw :size="14"/>重新登录并推送</button></div></div><div class="notice compact"><CircleHelp :size="15"/><span>推送将创建 / 更新上游账号并开启调度；所选号池已开启守护时，自动进入恢复观察。成功条目重试时自动跳过。</span></div><button class="button primary full-width" :disabled="busy || loginLoading || batch.items.every(i => ['done','skipped'].includes(i.status))" @click="commitImport"><LoaderCircle v-if="busy" :size="16" class="spinning"/><CloudUpload v-else :size="16"/>{{ busy ? '正在逐条推送…' : batch.items.some(i => ['failed', 'uncertain', 'updating'].includes(i.status)) ? '重试未完成项' : '确认推送入池' }}</button></section>
            <ImportHistory :revision="historyRevision" :batches="data.batches" :busy="busy || loginLoading" :configured="data.configured" @select="selectBatch" @relogin="(sourceBatch, item) => openRelogin(item, sourceBatch)" @discard="discardBatch" @checked="batchChecked"/>
          </div></div></template>

          <RateInspection v-if="page === 'rates'" @notify="notify"/>
          <MailNotifications v-if="page === 'mail'" @notify="notify"/>
          <InvoiceManagement v-if="page === 'invoices'" @notify="notify"/>
          <section v-if="page === 'events'" class="panel"><div class="panel-heading"><h2>调度与操作记录</h2><button class="button" @click="refresh"><RefreshCw :size="15"/>刷新 · 每 {{ refreshIntervalSeconds }} 秒</button></div><div class="table-scroll"><table><thead><tr><th>时间</th><th>级别</th><th>账号</th><th>操作</th><th>详情</th></tr></thead><tbody><tr v-for="event in data.events" :key="event.id"><td class="muted small">{{ timeText(event.created_at) }}</td><td><span :class="['badge', event.level === 'info' ? 'success' : 'warning']">{{ { info: '信息', warning: '提醒', error: '异常' }[event.level] }}</span></td><td>{{ event.account_id ? '#' + event.account_id : '系统' }}</td><td>{{ { settings: '更新设置', guard: '风控设置', rpm_alert: 'RPM 增长告警', rate_correction: '用户倍率纠正', rate_rules: '充值档位规则', postgres_settings: '数据库连接设置', mail_settings: '邮件配置', mail_error: '邮件异常', auto_relogin: '自动重登', auto_relogin_error: '自动重登失败', image_cooldown: '冷却 · 仅生图调度', pause: '暂停调度', resume: '恢复调度', healthy: '观察通过', replacement: '启用替补调度', enroll: '录入号池', remove: '移出号池', import: '推送账号', import_observation: '入池观察', upstream_error: '接口异常', worker_error: '调度异常' }[event.action] || event.action }}</td><td>{{ event.message }}</td></tr></tbody></table></div><div v-if="!data.events.length" class="empty-state"><Activity :size="28"/><h3>还没有操作日志</h3><p>连接、上号与调度决策会在这里留下记录。</p></div></section>
          <section v-if="page === 'usage'" class="panel usage-panel">
            <div class="panel-heading usage-heading">
              <div><h2>账号调用记录 <span class="count-chip">{{ usageTotal }}</span></h2><p>三类本地号池同时展示最新调用记录</p></div>
              <div class="usage-actions"><div class="column-settings-wrap"><button class="button" @click="usageColumnsOpen = !usageColumnsOpen"><ListFilter :size="15"/>列设置</button><div v-if="usageColumnsOpen" class="column-settings"><strong>可选列</strong><label v-for="(label, key) in usageColumnLabels" :key="key"><input v-model="settings.usage_visible_columns" type="checkbox" :value="key" @change="saveUsageColumns"/>{{ label }}</label><small>账号、延迟、时间固定展示</small></div></div><button class="button" :disabled="usageLoading" @click="loadUsage"><RefreshCw :size="15" :class="{ spinning: usageLoading }"/>刷新 · 每 {{ refreshIntervalSeconds }} 秒</button></div>
            </div>
            <div v-if="!data.configured" class="empty-state"><Unplug :size="28"/><h3>请先连接 Sub2API</h3><p>配置管理员 API Key 后才能读取账号调用记录。</p><button class="button" @click="page = 'settings'">配置连接<ArrowRight :size="15"/></button></div>
            <div v-else class="usage-pool-grid">
              <section v-for="pool in pools" :key="pool.id" :class="['usage-pool-card', pool.color]"><div class="usage-pool-heading"><div><h3>{{ pool.name }}</h3><small>{{ pool.desc }}</small><span class="usage-active-count"><span class="dot green"></span>正在调度中 {{ usageActiveCounts[pool.id] || 0 }} 个</span></div><span class="count-chip">{{ usagePoolRecords[pool.id]?.length || 0 }} 条记录</span></div><div class="usage-records-scroll"><table class="usage-table" :class="{ 'usage-table-expanded': settings.usage_visible_columns.length > 0 }"><thead><tr><th>账号</th><th v-if="isUsageColumnVisible('model')">模型</th><th v-if="isUsageColumnVisible('reasoning_effort')">推理强度</th><th v-if="isUsageColumnVisible('group')">分组</th><th>延迟</th><th>时间</th></tr></thead><tbody><tr v-for="record in usagePoolRecords[pool.id]" :key="record.id"><td><strong>{{ record.account_name || `#${record.account_id}` }}</strong><small>#{{ record.account_id }}</small></td><td v-if="isUsageColumnVisible('model')">{{ record.model || '—' }}</td><td v-if="isUsageColumnVisible('reasoning_effort')"><span class="reasoning-tag">{{ record.reasoning_effort || '—' }}</span></td><td v-if="isUsageColumnVisible('group')"><span class="pool-tag">{{ record.group_name || '未分组' }}</span></td><td><div class="latency-cell"><span :class="{ 'slow-latency': record.first_token_ms > settings.rule.threshold_ms }">首字 {{ latencyText(record.first_token_ms) }}</span><small>总耗时 {{ latencyText(record.duration_ms) }}</small></div></td><td class="muted small">{{ usageTimeText(record.created_at) }}</td></tr></tbody></table><div v-if="!usagePoolRecords[pool.id]?.length" class="usage-pool-empty">暂无调用记录</div></div></section>
            </div>
          </section>
        </template>
        <div v-else class="empty-state"><LoaderCircle class="spinning" :size="28"/><p>正在读取本地工作空间…</p></div>
        <footer class="page-footer"><span><Database :size="13"/>数据保存在本机 · SQLite</span><span>自动调度，让账号各得其时。</span></footer>
      </div>
    </main>
    <RankingSidebar v-if="settings && !['rates', 'settings'].includes(page)" :accounts="rankingAccounts" :sample-size="rankingSampleSize" :refresh-text="rankingRefreshText"/>

    <ReloginAccount v-if="page === 'import' && relogin" :batch="relogin.batch" :item="relogin.item" :source="relogin.source" @close="relogin = null" @loading="loginLoading = $event" @updated="reloginUpdated"/>

    <dialog v-if="page === 'import' && importOptions" ref="importSettingsDialog" class="modal import-settings-dialog" aria-labelledby="import-settings-title" @click.self="!busy && importSettingsDialog.close()" @cancel="busy && $event.preventDefault()">
      <form @submit.prevent="importSettingsDialog.close()">
        <div class="modal-heading"><div><h2 id="import-settings-title">上号设置</h2><p>JSON 与账密上号共用，已生成的预览保留原参数</p></div><button class="icon-button" type="button" :disabled="busy" aria-label="关闭上号设置" @click="importSettingsDialog.close()"><X :size="20"/></button></div>
        <div class="modal-body form-panel"><ImportOptions :model="importOptions" :groups="metadata.groups" :proxies="metadata.proxies" :busy="busy" @refresh="action(loadMetadata)"/><p v-if="importSettingsError" class="notice error" role="alert">{{ importSettingsError }}</p></div>
        <div class="modal-footer"><button class="button" type="button" :disabled="busy" @click="saveImportDefaults">保存为默认参数</button><button class="button primary" :disabled="busy"><Check :size="16"/>完成</button></div>
      </form>
    </dialog>

    <Transition name="toast"><div v-if="toast" role="status" :class="['toast', { error: toast.error }]"><CircleHelp v-if="toast.error" :size="18"/><Check v-else :size="18"/><span>{{ toast.message }}</span><button class="icon-button" aria-label="关闭提示" @click="toast = null"><X :size="15"/></button></div></Transition>

    <div v-if="modal" class="modal-overlay" @click.self="modal = false"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="enroll-title"><div class="modal-heading"><div><h2 id="enroll-title">录入已有账号</h2><p>默认读取 OAuth 类型的 GPT 账号，分类保存在本地。</p></div><button class="icon-button" aria-label="关闭" @click="modal = false"><X :size="20"/></button></div><div class="modal-body"><div class="notice compact"><Layers3 :size="17"/>默认只展示 OAuth 类型；一个账号只能进入一个本地号池，已在其它号池的账号会置灰。</div><form class="remote-filters" @submit.prevent="action(queryRemote)"><label class="search-input"><Search :size="16"/><input v-model="remoteSearch" placeholder="搜索 Sub2API 账号" /></label><select v-model="remoteGroup" aria-label="上游分组"><option value="">全部 GPT 分组</option><option v-for="group in metadata.groups" :key="group.id" :value="group.id">{{ group.name }}</option></select><button class="button" :disabled="busy">查询</button></form><div class="remote-list"><label v-for="account in remoteAccounts" :key="account.id" :class="['remote-account', { locked: isPoolLocked(account) }]" :title="isPoolLocked(account) ? `已在${poolName(localAccount(account.id).pool)}，不能加入${poolName(enrollPool)}` : ''"><input v-model="selected" type="checkbox" :value="account.id" :disabled="isPoolLocked(account)"/><span><strong>{{ account.name }}</strong><small>#{{ account.id }} · {{ account.groups.map(g => g.name).join(' / ') || '未绑定分组' }}</small></span><span class="badge neutral">{{ account.type }}</span><small v-if="localAccount(account.id)">{{ isPoolLocked(account) ? `已在${poolName(localAccount(account.id).pool)}，不可重复加入` : '已录入当前号池' }}</small><small v-else>未录入</small></label><div v-if="!remoteAccounts.length" class="quiet-empty">{{ busy ? '正在读取上游账号…' : remoteLoaded ? '没有匹配的 GPT 账号' : '配置连接后可读取账号' }}</div></div></div><div class="modal-footer"><button class="text-button" :disabled="busy || !selectableRemoteAccounts().length" @click="toggleRemoteSelection">{{ selectableRemoteAccounts().length && selectableRemoteAccounts().every(account => selected.includes(account.id)) ? '取消全选' : '全选' }}</button><span class="muted small">已选 {{ selected.length }} 个</span><select v-model="enrollPool" aria-label="录入到本地号池" @change="changeEnrollPool"><option v-for="pool in pools" :key="pool.id" :value="pool.id">{{ pool.name }}</option></select><button class="button primary" :disabled="busy || !selected.length" @click="enroll"><Plus :size="15"/>录入号池</button></div></section></div>

    <div v-if="currentDetail" class="modal-overlay" @click.self="detail = null"><section class="modal detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title"><div class="modal-heading"><div><h2 id="detail-title">{{ currentDetail.name }}</h2><p>#{{ currentDetail.id }} · {{ poolName(currentDetail.pool) }}</p></div><button class="icon-button" aria-label="关闭" @click="detail = null"><X :size="20"/></button></div><div class="modal-body"><div class="detail-stats"><div><small>慢调用占比</small><strong>{{ currentDetail.sample.length ? ratio(currentDetail) + '%' : '—' }}</strong></div><div><small>有效样本</small><strong>{{ currentDetail.sample.length }}<small> 次</small></strong></div><div><small>当前状态</small><span class="badge neutral">{{ statuses[currentDetail.state] }}</span></div></div><div class="sample-chart"><div v-for="sample in [...currentDetail.sample].reverse()" :key="sample.id" class="chart-column"><span>{{ (sample.first_token_ms / 1000).toFixed(1) }}s</span><div :class="{ slow: sample.first_token_ms > settings.rule.threshold_ms }" :style="{ height: sampleHeight(sample.first_token_ms) + '%' }"></div><small>#{{ sample.id }}</small></div><p v-if="!currentDetail.sample.length" class="muted">最近 5 分钟内暂无符合当前统计周期的有效调用记录。</p></div><div class="chart-legend"><span class="dot green"></span>正常首字延迟<span class="dot orange"></span>超过 {{ settings.rule.threshold_ms / 1000 }}s</div><div class="detail-info"><p><span>有效统计窗口</span>最近 5 分钟（固定）</p><p><span>统计边界</span>{{ currentDetail.epoch ? timeText(currentDetail.epoch) : '初始采样，仅取最近 5 分钟调用' }}</p><p><span>记录 ID 水位</span>{{ currentDetail.watermark || '未设置' }}</p><p><span>最近检查</span>{{ timeText(currentDetail.last_checked) }}</p><p v-if="isGuarded(currentDetail)"><span>窗口内达限</span>{{ breachCount(currentDetail) }} / {{ settings.rule.breach_count }} 次 · 最近 {{ settings.rule.breach_window_seconds }} 秒</p><p><span>最近决策</span>{{ currentDetail.reason || '尚未触发调度' }}</p><p v-if="currentDetail.error" class="orange-text">{{ currentDetail.error }}</p></div></div></section></div>
  </div>
  <div v-else class="auth-loading"><LoaderCircle class="spinning" :size="28"/><p>正在准备登录…</p></div>
</template>
