<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { CircleHelp, Plus, RefreshCw, X } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const rules = ref(null)
const rulesError = ref('')
const items = ref([])
const page = ref(1)
const pages = ref(1)
const total = ref(0)
const selected = ref(null)
const detail = computed(() => items.value.find(item => item.id === selected.value))
const note = ref('')
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const statuses = { pending: '待审核', rejected: '已驳回', approved: '待上传', issued: '已开票' }
const mailStatuses = { pending: '待发送', sending: '发送中', sent: '已提交 SMTP', failed: '发送失败', skipped: '已取消' }
const amount = value => `$${Number(value).toFixed(2)}`
const timeText = value => new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
let polling

async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    const result = await api(`/invoices?page=${page.value}`)
    items.value = result.items; pages.value = result.pages; total.value = result.total
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function perform(operation, message) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await operation(); emit('notify', message) } catch (e) { error.value = e.message } finally { busy.value = false; await refresh() }
}
async function load() {
  try { rules.value = await api('/invoices/rules'); await refresh() } catch (e) { error.value = e.message }
}
async function saveRules() {
  if (busy.value) return
  rulesError.value = ''
  const minimums = rules.value.tiers.map(tier => Number(tier.minimum))
  if (!minimums.includes(0)) {
    rulesError.value = '首档金额起点必须为 0。配置更高金额的手续费时，请保留 0 档并增加档位。'; return
  }
  if (new Set(minimums).size !== minimums.length) {
    rulesError.value = '金额起点不能重复，请为每个档位设置不同的起点。'; return
  }
  busy.value = true
  try {
    rules.value = await api('/invoices/rules', 'PUT', rules.value)
    emit('notify', '手续费规则已保存')
  } catch (e) { rulesError.value = e.message } finally { busy.value = false }
}
function open(item) { selected.value = item.id; note.value = '' }
function review(action) {
  const item = detail.value
  if (action === 'approve' && !window.confirm(`审核通过后将从账户余额扣除 ${amount(item.fee)} 手续费，确认继续？`)) return
  if (action.startsWith('confirm_') && !window.confirm('请先在 Sub2API 按申请编号核对余额流水。确认保存核对结果？')) return
  void perform(() => api(`/invoices/${item.id}/review`, 'POST', { action, note: note.value }), '审核结果已保存')
}
function upload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 5 * 1024 * 1024) {
    error.value = '请上传不超过 5 MB 的 PDF'; return
  }
  const id = detail.value.id
  void perform(() => api(`/invoices/${id}/file`, 'PUT', file, { rawBody: true }), '发票已上传，邮件已加入发送队列')
}
function resend() {
  if (!window.confirm('确认重新发送发票邮件？对方可能会收到重复邮件。')) return
  const id = detail.value.id
  void perform(() => api(`/invoices/${id}/resend`, 'POST', {}), '邮件已加入发送队列')
}
onMounted(() => { void load(); polling = setInterval(refresh, 10000) })
onUnmounted(() => clearInterval(polling))
</script>

<template>
  <div class="invoice-management">
    <div v-if="error" class="notice error" role="alert"><CircleHelp :size="17"/>{{ error }}<button class="text-button" @click="error = ''; load()">重试</button></div>
    <form v-if="rules" class="panel form-panel" @submit.prevent="saveRules">
      <div class="card-title"><div><h2>发票手续费</h2><p>按充值入账金额（USD）匹配档位，每次申请收取该档固定手续费；审核通过后从账户余额扣除。</p></div><label class="check-row"><input v-model="rules.enabled" type="checkbox" :disabled="busy"/>开启自助发票</label></div>
      <div v-for="(tier, index) in rules.tiers" :key="index" class="invoice-tier">
        <label>金额起点（含，USD）<input v-model="tier.minimum" type="number" min="0" step="0.01" required :disabled="busy"/></label>
        <label>固定手续费（USD）<input v-model="tier.fee" type="number" min="0" step="0.01" required :disabled="busy"/></label>
        <button type="button" class="button" :disabled="busy || rules.tiers.length === 1" @click="rules.tiers.splice(index, 1)">删除档位</button>
      </div>
      <p class="small muted">首档起点必须为 0，金额起点不能重复；配置更高金额的手续费时，请保留 0 档并增加档位。每档适用至下一档起点，按合并后的申请总金额匹配档位，只收取一次固定手续费，金额最多两位小数。</p>
      <p v-if="rulesError" class="notice error" role="alert">{{ rulesError }}</p>
      <div class="form-actions"><button type="button" class="button" :disabled="busy || rules.tiers.length >= 20" @click="rules.tiers.push({ minimum: '', fee: '' })"><Plus :size="15"/>增加档位</button><button class="button primary" :disabled="busy">保存规则</button></div>
      <p class="small muted">发票邮件复用“邮件通知”中的 SMTP 配置，发送到申请邮箱；不受告警通知开关影响。</p>
    </form>
    <section class="panel">
      <div class="panel-heading"><h2>发票申请 · {{ total }}</h2><button class="button" :disabled="loading || busy" @click="refresh"><RefreshCw :size="15"/>刷新</button></div>
      <div class="table-scroll"><table><thead><tr><th>提交时间</th><th>用户</th><th>抬头</th><th>开票金额</th><th>手续费</th><th>状态</th><th>邮件</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.id"><td class="small">{{ timeText(item.created_at) }}</td><td>{{ item.user_email || '#' + item.user_id }}</td><td>{{ item.title }}</td><td>{{ amount(item.amount) }}</td><td>{{ amount(item.fee) }}</td><td><span class="badge" :class="item.status === 'issued' ? 'success' : 'neutral'">{{ ['charging', 'uncertain'].includes(item.charge_status) ? '扣费待核对' : statuses[item.status] }}</span></td><td>{{ mailStatuses[item.mail_status] || '—' }}</td><td><button class="button" @click="open(item)">查看</button></td></tr>
      </tbody></table></div>
      <div v-if="!items.length" class="quiet-empty">{{ loading ? '正在加载…' : '暂无发票申请' }}</div>
      <div class="form-actions"><button class="button" :disabled="page <= 1 || loading || busy" @click="page--; refresh()">上一页</button><span>{{ page }} / {{ pages }}</span><button class="button" :disabled="page >= pages || loading || busy" @click="page++; refresh()">下一页</button></div>
    </section>
    <div v-if="detail" class="modal-overlay" @click.self="!busy && (selected = null)"><section class="modal detail-modal" role="dialog" aria-modal="true" aria-labelledby="invoice-detail-title">
      <div class="modal-heading"><h2 id="invoice-detail-title">发票申请</h2><button class="icon-button" aria-label="关闭" :disabled="busy" @click="selected = null"><X :size="20"/></button></div>
      <div class="modal-body">
        <div v-if="error" class="notice error" role="alert">{{ error }}</div>
        <p class="small muted">申请编号：{{ detail.id }}</p>
        <dl class="invoice-details"><dt>抬头</dt><dd>{{ detail.title }}</dd><dt>税号</dt><dd>{{ detail.tax_id }}</dd><dt>接收邮箱</dt><dd>{{ detail.email }}</dd><dt>订单</dt><dd>{{ detail.orders.map(order => '#' + order.id).join('、') }}</dd><dt>开票金额</dt><dd>{{ amount(detail.amount) }}</dd><dt>手续费</dt><dd>{{ amount(detail.fee) }}（{{ detail.fee_mode === 'fixed' ? '固定金额' : detail.rate + '%' }}）</dd><dt>状态</dt><dd>{{ statuses[detail.status] }}</dd></dl>
        <p v-if="detail.note" class="notice">{{ detail.note }}</p>
        <template v-if="detail.status === 'pending'">
          <label>审核 / 核对说明<textarea v-model.trim="note" rows="3" maxlength="500" :disabled="busy" placeholder="驳回或核对扣费结果时必填"/></label>
          <div v-if="['uncertain', 'charging'].includes(detail.charge_status)" class="form-actions"><button class="button primary" :disabled="busy || !note" @click="review('confirm_charged')">核对：已扣费</button><button class="button" :disabled="busy || !note" @click="review('confirm_not_charged')">核对：未扣费</button></div>
          <div v-else class="form-actions"><button class="button primary" :disabled="busy" @click="review('approve')">审核通过并扣费</button><button class="button" :disabled="busy || !note" @click="review('reject')">驳回</button></div>
        </template>
        <label v-if="detail.status === 'approved'">上传电子发票（PDF，最多 5 MB）<input type="file" accept=".pdf,application/pdf" :disabled="busy" @change="upload"/></label>
        <template v-if="detail.status === 'issued'"><p>邮件：{{ mailStatuses[detail.mail_status] || '—' }}</p><p v-if="detail.mail_error" class="notice error">{{ detail.mail_error }}</p><div class="form-actions"><a class="button" :href="`/api/invoices/${detail.id}/file`" target="_blank" rel="noopener">下载发票</a><button class="button" :disabled="busy || ['pending', 'sending'].includes(detail.mail_status)" @click="resend">重新发送邮件</button></div></template>
      </div>
    </section></div>
  </div>
</template>

<style scoped>
.invoice-management { display: grid; gap: 24px; }
.invoice-tier { display: flex; align-items: end; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.invoice-tier label { flex: 1; min-width: 180px; }
.invoice-details { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 12px 20px; }
.invoice-details dd { margin: 0; overflow-wrap: anywhere; }
.invoice-details dt { color: var(--muted); }
.invoice-management textarea { width: 100%; padding: 12px; border: 1px solid var(--border); border-radius: 9px; font: inherit; resize: vertical; }
</style>
