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
let timer, requestId = 0, mounted = true, firstLoad = true

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
onMounted(load)
onUnmounted(() => { mounted = false; requestId++; clearTimeout(timer) })
</script>

<template>
  <section class="panel import-history" aria-labelledby="import-history-title">
    <div class="panel-heading"><div><h2 id="import-history-title">最近导入批次 <span class="count-chip">{{ total }}</span></h2><p>搜索全部历史账号，检查失效状态，快速重登入池</p></div><button class="button" :disabled="loading || !!checking" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新列表</button></div>
    <div class="history-toolbar"><label class="search-input"><Search :size="16"/><input v-model="search" maxlength="200" placeholder="搜索账号名称、邮箱或 ID" aria-label="搜索导入批次账号"/><button v-if="search" class="icon-button" aria-label="清空账号搜索" @click="search = ''"><X :size="14"/></button></label><label class="history-filter"><input v-model="onlyIssues" type="checkbox"/>仅展示需处理账号</label></div>
    <p class="history-help">批次检查实时读取 Sub2API 状态，不发起模型调用。导入成功不代表当前登录有效；检查结果以标注时间为准。</p>
    <p v-if="error" class="notice error history-error" role="alert">{{ error }}</p>
    <div v-if="!items.length" class="quiet-empty" role="status">{{ loading ? '正在读取批次…' : search ? '没有匹配的账号或批次' : '还没有导入批次' }}</div>
    <article v-for="batch in items" :key="batch.id" class="history-batch">
      <div class="history-batch-heading">
        <button class="history-toggle" :aria-expanded="isOpen(batch)" :aria-controls="`history-${batch.id}`" @click="toggle(batch.id)"><ChevronDown v-if="isOpen(batch)" :size="17"/><ChevronRight v-else :size="17"/><span><strong>{{ timeText(batch.created_at) }} <span class="badge neutral">{{ pools[batch.pool] }}</span></strong><small>{{ batch.items.length }} 个账号 · 已入池 {{ batch.items.filter(item => item.status === 'done').length }} · 需处理 {{ issueCount(batch) }} · 未检查 {{ batch.items.filter(item => !item.health).length }}</small><small>最近检查：{{ timeText(checkedAt(batch)) }}</small></span></button>
        <div class="history-batch-actions"><button class="button" :disabled="busy || !!checking || !configured" @click="check(batch)"><RefreshCw v-if="checking === batch.id" :size="14" class="spinning"/><ShieldCheck v-else :size="14"/>{{ checking === batch.id ? '检查中…' : '检查整批' }}</button><button class="icon-button" :disabled="busy || !!checking" title="清除本地批次" aria-label="清除本地批次" @click="emit('discard', batch.id)"><X :size="16"/></button></div>
      </div>
      <div v-if="isOpen(batch)" :id="`history-${batch.id}`" class="history-batch-body">
        <div class="history-batch-meta"><span>批次 {{ batch.id.slice(0, 8) }} · 并发 {{ batch.options.concurrency }} · 优先级 {{ batch.options.priority }}</span><button class="text-button" :disabled="busy || !!checking" @click="emit('select', batch)">查看参数 / 重试推送<ArrowRight :size="14"/></button></div>
        <div v-if="!matchingItems(batch).length" class="quiet-empty">当前筛选下没有需展示的账号；可点击「检查整批」更新状态。</div>
        <div v-else class="table-scroll history-table"><table><thead><tr><th>账号 / 导入结果</th><th>最近检查</th><th>号池 / 调度</th><th class="align-right">操作</th></tr></thead><tbody>
          <tr v-for="item in matchingItems(batch)" :key="item.index">
            <td><strong>{{ item.name }}</strong><small v-if="item.email && !item.name.includes(item.email)">{{ item.email }}</small><small>{{ item.account_id ? '#' + item.account_id + ' · ' : '' }}{{ importLabels[item.status] || item.status }}</small><small v-if="item.status !== 'done' && item.message">{{ item.message }}</small></td>
            <td><span :class="['badge', !item.health ? 'neutral' : item.health.status === 'healthy' ? 'success' : 'warning']">{{ healthLabels[item.health?.status] || '未检查' }}</span><small>{{ item.health?.message || '点击检查整批，获取最新上游状态' }}</small><small v-if="item.health">{{ timeText(item.health.checked_at) }}</small></td>
            <td><template v-if="item.health"><strong>{{ pools[item.health.local_pool] || '未入本地池' }}</strong><small>{{ item.health.schedulable === true ? '上游调度已开启' : item.health.schedulable === false ? '上游调度已停止' : '上游状态未知' }}</small><small v-if="item.health.local_state === 'cooldown'">本地冷却中</small></template><span v-else class="muted">待检查</span></td>
            <td class="align-right"><button class="button" :disabled="busy || !!checking || !configured" @click="emit('relogin', batch, item)"><RefreshCw :size="14"/>重新登录并推送</button></td>
          </tr>
        </tbody></table></div>
      </div>
    </article>
    <div class="table-footer history-pagination"><span>共 {{ total }} 个{{ search ? '匹配' : '' }}批次 · 第 {{ page }} / {{ pages }} 页</span><div><button class="button" :disabled="loading || page <= 1" @click="page--">上一页</button><button class="button" :disabled="loading || page >= pages" @click="page++">下一页</button></div></div>
  </section>
</template>
