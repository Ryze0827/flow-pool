<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RotateCcw, Search } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const userIds = ref([])
const options = ref([])
const usersLoading = ref(false)
const hasMoreUsers = ref(false)
const startAt = ref('')
const endAt = ref('')
const result = ref(null)
const history = ref([])
const busy = ref(false)
const error = ref('')
const statuses = { pending: '待返还', credited: '已返还', failed: '失败，可重试', uncertain: '待人工核对', skipped: '已跳过' }
let searchTimer
let searchSequence = 0
const canExecute = computed(() => result.value && (result.value.counts.pending + result.value.counts.failed > 0))
const amount = value => Number(value).toLocaleString('en-US', { maximumFractionDigits: 10 })
const dateTime = value => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
watch([userIds, startAt, endAt], () => { result.value = null })

async function searchUsers(search = '') {
  const sequence = ++searchSequence
  usersLoading.value = true
  try {
    const data = await api(`/promotions/quota-refunds/users?search=${encodeURIComponent(search)}`)
    if (sequence !== searchSequence) return
    const retained = options.value.filter(option => userIds.value.includes(option.value))
    const found = data.items.map(user => ({ value: user.id, label: `${user.username || user.email} · #${user.id}${user.username && user.email ? ` · ${user.email}` : ''}` }))
    options.value = [...new Map([...retained, ...found].map(option => [option.value, option])).values()]
    hasMoreUsers.value = data.has_more
  } catch (cause) { if (sequence === searchSequence) error.value = cause.message }
  finally { if (sequence === searchSequence) usersLoading.value = false }
}
function queueSearch(value) {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => searchUsers(value), 250)
}
async function loadHistory() { history.value = (await api('/promotions/quota-refunds')).items }
async function preview() {
  if (busy.value) return
  error.value = ''
  result.value = null
  const start = new Date(`${startAt.value}:00+08:00`)
  const end = new Date(`${endAt.value}:00+08:00`)
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end || end > new Date()) {
    error.value = '请选择已经结束的有效时间段，结束时间须晚于开始时间。'
    return
  }
  busy.value = true
  try {
    result.value = await api('/promotions/quota-refunds/preview', 'POST', { user_ids: userIds.value, start_at: start.toISOString(), end_at: end.toISOString() })
    await loadHistory()
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function execute() {
  if (busy.value || !canExecute.value) return
  busy.value = true
  error.value = ''
  try {
    result.value = await api(`/promotions/quota-refunds/${result.value.id}/execute`, 'POST')
    const counts = result.value.counts
    emit('notify', `额度返还：成功 ${counts.credited} 人，失败 ${counts.failed} 人，待核对 ${counts.uncertain} 人，跳过 ${counts.skipped} 人`)
    await loadHistory()
  } catch (cause) {
    error.value = `${cause.message}。请刷新此记录核对执行结果。`
    try { result.value = await api(`/promotions/quota-refunds/${result.value.id}`) } catch { /* 保留记录以便重新读取。 */ }
  } finally { busy.value = false }
}
async function openRecord(id) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { result.value = await api(`/promotions/quota-refunds/${id}`) }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
onMounted(async () => {
  await searchUsers()
  try { await loadHistory() } catch (cause) { error.value = cause.message }
})
onUnmounted(() => { clearTimeout(searchTimer); searchSequence += 1 })
</script>

<template>
  <section id="refund-panel" class="panel refund-panel" role="tabpanel" aria-labelledby="refund-title">
    <header class="refund-heading"><RotateCcw :size="22"/><div><h2 id="refund-title">额度返还</h2><p>将指定时间段内实际消费的余额额度，原额返还至用户余额。</p></div></header>
    <form class="refund-form" @submit.prevent="preview">
      <label class="refund-users">返还用户
        <a-select v-model:value="userIds" mode="multiple" show-search allow-clear :filter-option="false" :options="options" :loading="usersLoading" :disabled="busy" placeholder="不选择则默认所有用户，可搜索用户名、邮箱或 ID" aria-label="返还用户" @search="queueSearch"/>
        <small>{{ userIds.length ? `已选择 ${userIds.length} 位用户` : '当前范围：所有用户' }}<template v-if="hasMoreUsers"> · 输入关键词查找更多用户</template></small>
      </label>
      <label>消费开始时间<input v-model="startAt" type="datetime-local" required :disabled="busy" /></label>
      <label>消费结束时间<input v-model="endAt" type="datetime-local" required :disabled="busy" /></label>
      <p class="refund-hint">北京时间（Asia/Shanghai），包含开始时间，不包含结束时间。仅统计实际余额扣费，不含订阅套餐用量；无消费用户不发放。需要已配置 Sub2API PostgreSQL。</p>
      <button class="button" type="submit" :disabled="busy"><Search :size="15"/>{{ busy ? '处理中…' : '预览返还额度' }}</button>
    </form>
    <p v-if="error" class="notice error refund-notice" role="alert">{{ error }}</p>
    <div v-if="result" class="refund-result" aria-live="polite">
      <div class="refund-result-heading"><div><h3>返还明细</h3><p>{{ dateTime(result.start_at) }} — {{ dateTime(result.end_at) }}（北京时间）</p><p>{{ result.user_ids.length ? `指定 ${result.user_ids.length} 位用户` : '所有用户' }} · 本次预览快照</p></div><button class="button" :disabled="busy" @click="openRecord(result.id)"><RotateCcw :size="14"/>刷新记录</button></div>
      <div class="refund-summary"><span>有消费用户<strong>{{ result.items.length }} 人</strong></span><span>消费额度合计<strong>{{ amount(result.total_amount) }}</strong></span><span>已返还额度<strong>{{ amount(result.credited_amount) }}</strong></span></div>
      <p v-if="result.counts.uncertain" class="notice error">有 {{ result.counts.uncertain }} 位用户的加款结果待核对。请在 Sub2API 用户余额记录中按下方批次编号核对；系统不会自动重复加款。</p>
      <p v-if="result.counts.skipped" class="notice">有 {{ result.counts.skipped }} 位用户存在重叠返还时段，已跳过。请另选不重叠时段。</p>
      <div v-if="result.items.length" class="refund-table-wrap"><table class="refund-table"><thead><tr><th>用户</th><th>消费次数</th><th>返还额度</th><th>状态</th></tr></thead><tbody><tr v-for="item in result.items" :key="item.user_id"><td><strong>{{ item.username || item.email || `用户 #${item.user_id}` }}</strong><small>#{{ item.user_id }} · {{ item.email }}</small></td><td>{{ item.requests }}</td><td>{{ amount(item.amount) }}</td><td><span class="badge" :class="item.status === 'credited' ? 'success' : 'neutral'">{{ statuses[item.status] }}</span><small v-if="item.error" class="refund-item-error">{{ item.error }}</small></td></tr></tbody></table></div>
      <p v-else class="quiet-empty">此时间段内没有可返还的余额消费。</p>
      <div class="refund-actions"><small>批次 {{ result.id }}</small><button v-if="canExecute" class="button primary" :disabled="busy" @click="execute"><RotateCcw :size="15"/>{{ busy ? '正在返还…' : result.counts.failed ? '重试失败项并继续返还' : '确认并触发额度返还' }}</button></div>
      <p class="refund-hint">按本次预览金额执行。已返还或结果待核对的重叠时间段不会再次发放。</p>
    </div>
    <div class="refund-history"><h3>最近返还记录 <small>最近 20 次预览与执行</small></h3><div v-if="!history.length" class="quiet-empty">暂无返还记录</div><button v-for="item in history" :key="item.id" type="button" class="refund-history-row" :disabled="busy" @click="openRecord(item.id)"><span>{{ dateTime(item.start_at) }} — {{ dateTime(item.end_at) }}<small>成功 {{ item.counts.credited }} · 待返还 {{ item.counts.pending }} · 失败 {{ item.counts.failed }} · 待核对 {{ item.counts.uncertain }} · 跳过 {{ item.counts.skipped }}</small></span><span>已返还 {{ amount(item.credited_amount) }} →</span></button></div>
  </section>
</template>

<style scoped>
.refund-panel { overflow: hidden; }
.refund-heading { display: flex; gap: .85rem; align-items: center; padding: 1.5rem; border-bottom: 1px solid var(--border); }
.refund-heading > svg { color: var(--accent); flex-shrink: 0; }
.refund-heading h2 { font-size: 1.05rem; margin: 0 0 .3rem; }
.refund-heading p, .refund-result-heading p { color: var(--muted); font-size: .85rem; margin: .25rem 0 0; }
.refund-form { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; padding: 1.5rem; }
.refund-form label { display: flex; flex-direction: column; gap: .5rem; min-width: 0; font-size: .85rem; }
.refund-form input { min-width: 0; width: 100%; }
.refund-users, .refund-hint { grid-column: 1 / -1; }
.refund-form > .button { justify-self: start; }
.refund-form small, .refund-hint, .refund-actions small, .refund-history small { color: var(--muted); font-size: .78rem; }
.refund-hint { margin: 0; line-height: 1.7; }
.refund-notice { margin: 0 1.5rem 1rem; }
.refund-result, .refund-history { border-top: 1px solid var(--border); padding: 1.5rem; }
.refund-result-heading, .refund-actions { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.refund-result h3, .refund-history h3 { font-size: .95rem; margin: 0; }
.refund-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1.25rem 0; padding: 1rem; background: var(--bg); border: 1px solid var(--border); border-radius: .6rem; }
.refund-summary span { color: var(--muted); font-size: .8rem; }
.refund-summary strong { display: block; color: var(--text); font-size: 1.2rem; margin-top: .4rem; overflow-wrap: anywhere; }
.refund-table-wrap { overflow-x: auto; max-height: 26rem; }
.refund-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
.refund-table th, .refund-table td { padding: .85rem .7rem; text-align: left; border-bottom: 1px solid var(--border); }
.refund-table th { color: var(--muted); font-weight: 500; white-space: nowrap; }
.refund-table small, .refund-history-row small { display: block; color: var(--muted); margin-top: .3rem; }
.refund-item-error { max-width: 20rem; }
.refund-actions { margin: 1rem 0 .7rem; }
.refund-actions small { overflow-wrap: anywhere; }
.refund-history h3 small { margin-left: .5rem; font-weight: 400; }
.refund-history-row { display: flex; justify-content: space-between; align-items: center; gap: 1rem; width: 100%; text-align: left; padding: 1rem 0; border: 0; border-bottom: 1px solid var(--border); background: transparent; color: var(--text); cursor: pointer; font: inherit; font-size: .82rem; }
.refund-history-row:hover { color: var(--accent); }
.refund-history-row > span:last-child { white-space: nowrap; }
@media (max-width: 600px) {
  .refund-heading, .refund-form, .refund-result, .refund-history { padding: 1rem; }
  .refund-form { grid-template-columns: 1fr; }
  .refund-summary { gap: .6rem; padding: .75rem; }
  .refund-summary strong { font-size: 1rem; }
  .refund-history-row { align-items: flex-start; flex-direction: column; gap: .5rem; }
  .refund-notice { margin-inline: 1rem; }
}
</style>
