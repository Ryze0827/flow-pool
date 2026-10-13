<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowRight, ChevronDown, ChevronRight, RefreshCw, Search, ShieldCheck, X } from 'lucide-vue-next'
import { api } from './api'

const props = defineProps({ batches: Array, busy: Boolean, configured: Boolean, revision: Number })
const emit = defineEmits(['select', 'relogin', 'discard', 'checked'])
const search = ref('')
const page = ref(1)
const items = ref([])
const total = ref(0)
const loading = ref(false)
const checking = ref('')
const error = ref('')
const expanded = ref(new Set())
const onlyIssues = ref(false)
const now = ref(Date.now() / 1000)
const pageSize = 10
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const pools = { priority: '高权重组', risk: '风控组', third_party: '三方账号组' }
const importLabels = { pending: '待推送', done: '已入池', skipped: '已跳过', failed: '导入失败', uncertain: '待核对', updating: '待重试', creating: '创建中', created: '待入池' }
const healthLabels = { auth_expired: '登录失效', error: '上游错误', inactive: '已停用', unavailable: '不可调度', not_enrolled: '未入本地池', not_imported: '未关联账号', missing: '上游已删除', healthy: '未见异常' }
const timeText = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '未检查'
const isIssue = item => item.health ? item.health.status !== 'healthy' : ['failed', 'uncertain', 'updating'].includes(item.status)
const issueCount = batch => batch.items.filter(isIssue).length
const checkedAt = batch => Math.max(0, ...batch.items.map(item => item.health?.checked_at || 0))
const matchingItems = batch => batch.items.filter(item => (!onlyIssues.value || isIssue(item)) && `${item.name} ${item.email || ''} ${item.account_id || ''}`.toLowerCase().includes(search.value.trim().toLowerCase()))
const isOpen = batch => expanded.value.has(batch.id) || !!search.value.trim() || onlyIssues.value
let timer, clock, requestId = 0, mounted = true, firstLoad = true

async function load() {
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const result = await api(`/import/batches?search=${encodeURIComponent(search.value.trim())}&page=${page.value}&page_size=${pageSize}`)
    if (!mounted || id !== requestId) return
    items.value = result.items
    total.value = result.total
    if (firstLoad && result.items.length) { expanded.value = new Set([result.items[0].id]); firstLoad = false }
    if (page.value > pages.value) { page.value = pages.value; return }
  } catch (failure) {
    if (mounted && id === requestId) error.value = failure.message
  } finally {
    if (mounted && id === requestId) loading.value = false
  }
}
function toggle(id) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
async function check(batch) {
  if (checking.value || props.busy) return
  checking.value = batch.id
  error.value = ''
  try {
    const result = await api(`/import/${batch.id}/check`, 'POST')
    if (!mounted) return
    items.value = items.value.map(item => item.id === result.id ? result : item)
    expanded.value = new Set([...expanded.value, result.id])
    emit('checked', result)
  } catch (failure) {
    if (mounted) error.value = `批次检查失败：${failure.message}。保留上次结果，请稍后重试。`
  } finally { if (mounted) checking.value = '' }
}
watch(search, () => {
  clearTimeout(timer)
  requestId++
  timer = setTimeout(() => { if (page.value === 1) void load(); else page.value = 1 }, 250)
})
watch(page, load)
watch(() => props.revision, load)
watch(() => JSON.stringify(props.batches), () => { if (!checking.value) void load() })
onMounted(() => { void load(); clock = setInterval(() => { now.value = Date.now() / 1000 }, 1000) })
onUnmounted(() => { mounted = false; requestId++; clearTimeout(timer); clearInterval(clock) })
</script>

<template>
  <section class="panel import-history" aria-labelledby="import-history-title">
    <div class="panel-heading"><div><h2 id="import-history-title">最近导入批次 <span class="count-chip">{{ total }}</span></h2><p>搜索全部历史账号，检查失效状态，快速重登入池</p></div><button class="button" :disabled="loading || !!checking" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新列表</button></div>
    <div class="history-toolbar"><label class="search-input"><Search :size="16"/><input v-model="search" maxlength="200" placeholder="搜索账号名称、邮箱或 ID" aria-label="搜索导入批次账号"/><button v-if="search" class="icon-button" aria-label="清空账号搜索" @click="search = ''"><X :size="14"/></button></label><label class="history-filter"><input v-model="onlyIssues" type="checkbox"/>仅展示需处理账号</label></div>
    <details class="history-guide"><summary>入池后自动观察 5 分钟 <span>查看检查与重登说明</span></summary><p>推送成功后自动观察 5 分钟，期间上游账号错误会使用已保存账密自动重登。暂停、停用、到期和限流不触发重登；无账密需手动处理。检查不发起模型调用。</p></details>
    <p v-if="error" class="notice error history-error" role="alert">{{ error }}</p>
    <div v-if="!items.length" class="quiet-empty" role="status">{{ loading ? '正在读取批次…' : search ? '没有匹配的账号或批次' : '还没有导入批次' }}</div>
    <div class="history-batch-list">
    <article v-for="batch in items" :key="batch.id" :class="['history-batch', { 'is-open': isOpen(batch) }]">
      <div class="history-batch-heading">
        <button class="history-toggle" :aria-expanded="isOpen(batch)" :aria-controls="`history-${batch.id}`" @click="toggle(batch.id)"><ChevronDown v-if="isOpen(batch)" :size="17"/><ChevronRight v-else :size="17"/><span><strong>{{ timeText(batch.created_at) }} <span class="badge neutral">{{ pools[batch.pool] }}</span></strong><small>批次 #{{ batch.id.slice(0, 8) }} · 最近检查 {{ timeText(checkedAt(batch)) }}</small></span><span class="history-summary"><span><b>{{ batch.items.length }}</b>账号</span><span class="summary-success"><b>{{ batch.items.filter(item => item.status === 'done').length }}</b>已入池</span><span v-if="issueCount(batch)" class="summary-warning"><b>{{ issueCount(batch) }}</b>需处理</span><span v-if="batch.items.some(item => !item.health)"><b>{{ batch.items.filter(item => !item.health).length }}</b>未检查</span></span></button>
        <div class="history-batch-actions"><button class="button" :disabled="busy || !!checking || !configured" @click="check(batch)"><RefreshCw v-if="checking === batch.id" :size="14" class="spinning"/><ShieldCheck v-else :size="14"/>{{ checking === batch.id ? '检查中…' : '检查整批' }}</button><button class="icon-button" :disabled="busy || !!checking" title="清除本地批次" aria-label="清除本地批次" @click="emit('discard', batch.id)"><X :size="16"/></button></div>
      </div>
      <div v-if="isOpen(batch)" :id="`history-${batch.id}`" class="history-batch-body">
        <div class="history-batch-meta"><span>批次 {{ batch.id.slice(0, 8) }} · 并发 {{ batch.options.concurrency }} · 优先级 {{ batch.options.priority }}</span><button class="text-button" :disabled="busy || !!checking" @click="emit('select', batch)">查看参数 / 重试推送<ArrowRight :size="14"/></button></div>
        <div v-if="!matchingItems(batch).length" class="quiet-empty">当前筛选下没有需展示的账号；可点击「检查整批」更新状态。</div>
        <div v-else class="table-scroll history-table"><table><thead><tr><th>账号 / 导入结果</th><th>最近检查</th><th>号池 / 调度</th><th class="align-right">操作</th></tr></thead><tbody>
          <tr v-for="item in matchingItems(batch)" :key="item.index">
            <td><strong>{{ item.name }}</strong><small v-if="item.email && !item.name.includes(item.email)">{{ item.email }}</small><small>{{ item.account_id ? '#' + item.account_id + ' · ' : '' }}{{ importLabels[item.status] || item.status }}</small><small v-if="item.status !== 'done' && item.message">{{ item.message }}</small><small v-if="item.observation?.until" class="purple-text">{{ item.observation.until > now ? `入池观察 · 剩余 ${Math.ceil(item.observation.until - now)} 秒` : '本次 5 分钟入池观察已结束' }}</small><small v-if="item.observation?.error" class="orange-text">{{ item.observation.error }}</small></td>
            <td><span :class="['badge', !item.health ? 'neutral' : item.health.status === 'healthy' ? 'success' : 'warning']">{{ healthLabels[item.health?.status] || '未检查' }}</span><small>{{ item.health?.message || '点击检查整批，获取最新上游状态' }}</small><small v-if="item.health">{{ timeText(item.health.checked_at) }}</small></td>
            <td><template v-if="item.health"><strong>{{ pools[item.health.local_pool] || '未入本地池' }}</strong><small>{{ item.health.schedulable === true ? '上游调度已开启' : item.health.schedulable === false ? '上游调度已停止' : '上游状态未知' }}</small><small v-if="item.health.local_state === 'cooldown'">本地冷却中</small></template><span v-else class="muted">待检查</span></td>
            <td class="align-right"><button class="button" :disabled="busy || !!checking || !configured" @click="emit('relogin', batch, item)"><RefreshCw :size="14"/>重新登录并推送</button></td>
          </tr>
        </tbody></table></div>
      </div>
    </article>
    </div>
    <div class="table-footer history-pagination"><span>共 {{ total }} 个{{ search ? '匹配' : '' }}批次 · 第 {{ page }} / {{ pages }} 页</span><div><button class="button" :disabled="loading || page <= 1" @click="page--">上一页</button><button class="button" :disabled="loading || page >= pages" @click="page++">下一页</button></div></div>
  </section>
</template>


<style scoped>
.history-toolbar { gap: 20px; padding-bottom: 14px; }
.history-toolbar .search-input { flex: 0 1 560px; }
.history-filter { margin-left: auto; }
.history-guide { margin: 0 24px 20px; color: var(--muted); font-size: 13px; }
.history-guide summary { cursor: pointer; width: fit-content; line-height: 1.8; }
.history-guide summary span { margin-left: 12px; color: var(--accent); }
.history-guide p { max-width: 850px; padding: 8px 14px; border-left: 2px solid var(--border); }
.history-batch-list { padding: 0 20px 20px; display: grid; gap: 12px; background: linear-gradient(#fff, #f7f9fc); }
.history-batch { border: 1px solid var(--border); border-radius: 10px; background: #fff; overflow: hidden; }
.history-batch.is-open { border-color: #b9cee1; box-shadow: 0 3px 12px #29466008; }
.history-batch-heading { padding: 18px 20px; gap: 24px; }
.is-open .history-batch-heading { background: #f6f9fd; }
.history-toggle { gap: 14px; }
.history-toggle > svg { color: var(--muted); }
.history-toggle > span:first-of-type { flex: 1; }
.history-toggle strong { gap: 10px; font-size: 15px; font-variant-numeric: tabular-nums; }
.history-toggle small { font-size: 12px; margin-top: 8px; font-weight: 400; }
.history-toggle .badge { font-size: 12px; padding: 4px 8px; }
.history-summary { display: flex; align-items: center; gap: 24px; margin-left: auto; padding: 0 24px; border-left: 1px solid var(--border); }
.history-summary > span { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: 12px; white-space: nowrap; text-align: center; }
.history-summary b { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--text); }
.history-summary .summary-success b { color: var(--success); }
.history-summary .summary-warning b { color: var(--warning); }
.history-batch-actions { gap: 10px; }
.history-batch-actions > .icon-button { color: var(--muted); }
.history-batch-meta { background: #fff; border-top: 1px solid var(--border); border-bottom: 0; padding: 14px 20px; font-size: 12px; }
.history-table th { background: #f8fafc; font-size: 12px; padding-top: 10px; padding-bottom: 10px; }
.history-table td { padding-top: 18px; padding-bottom: 18px; }
.history-table td strong { color: var(--text); }
.history-table td small { font-size: 12px; line-height: 1.65; }
.history-table td .purple-text { color: var(--purple); }
.history-table td .orange-text { color: var(--warning); }
.history-pagination { padding: 16px 24px; background: #fff; }
@media (max-width: 1100px) {
  .history-toggle { flex-wrap: wrap; }
  .history-summary { width: 100%; border: 0; padding: 0 0 0 31px; margin: 4px 0 0; gap: 18px; }
  .history-summary > span { flex-direction: row; align-items: baseline; gap: 5px; }
  .history-summary b { font-size: 15px; }
}
@media (max-width: 600px) {
  .history-toolbar { padding-inline: 16px; gap: 12px; }
  .history-filter { margin-left: 0; }
  .history-guide { margin-inline: 16px; }
  .history-guide summary span { display: block; margin-left: 16px; }
  .history-batch-list { padding-inline: 10px; }
  .history-batch-heading { padding: 16px 12px; flex-wrap: wrap; gap: 14px; }
  .history-toggle strong { font-size: 14px; }
  .history-batch-actions { width: 100%; justify-content: flex-end; }
  .history-batch-meta { gap: 8px; padding: 12px; }
}
</style>
