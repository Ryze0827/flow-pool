<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { LoaderCircle, ShieldCheck } from 'lucide-vue-next'
import { api } from './api'
import { parseLoginRows } from './loginRows'

const props = defineProps({ busy: Boolean, configured: Boolean, options: Object })
const emit = defineEmits(['prepared', 'loading', 'started'])
const quickSource = ref('')
const submittedSource = ref('')
const rows = ref([])
const loading = ref(false)
const error = ref('')
const savedOptions = ref(null)
const batchId = ref(null)
const provider = ref('session_studio')
const savedProvider = ref('session_studio')
const successCount = computed(() => rows.value.filter(row => ['done', 'skipped'].includes(row.status)).length)
const failedCount = computed(() => rows.value.filter(row => row.status === 'failed').length)
const finishedCount = computed(() => successCount.value + failedCount.value)
const labels = { waiting: '待处理', running: '登录中', pushing: '推送中', done: '已入池', skipped: '已跳过', failed: '需人工处理' }
let mounted = true

async function login(retry = false) {
  if (loading.value || props.busy || !props.configured) return
  error.value = ''
  if (!retry) {
    try { rows.value = parseLoginRows(quickSource.value) } catch (failure) { error.value = failure.message; return }
    savedOptions.value = JSON.parse(JSON.stringify(props.options))
    batchId.value = null
    savedProvider.value = provider.value
    submittedSource.value = quickSource.value
    emit('started')
  }
  const pending = rows.value.filter(row => retry ? row.status === 'failed' : row.status === 'waiting')
  pending.forEach(row => { row.status = 'waiting'; row.message = '' })
  loading.value = true
  emit('loading', true)
  try {
    if (!batchId.value) {
      const batch = await api('/import/login-batch', 'POST', savedOptions.value)
      batchId.value = batch.id
      savedOptions.value = JSON.parse(JSON.stringify(batch.options))
    }
    let next = 0
    async function worker() {
      while (mounted && next < pending.length) {
        const row = pending[next++]
        row.status = row.batchId ? 'pushing' : 'running'
        try {
          if (!row.batchId) {
            const result = await api('/import/login', 'POST', {
              email: row.email, password: row.password, totp_secret: row.totp_secret, workspace_id: row.workspace_id,
              options: savedOptions.value, provider: savedProvider.value, batch_id: batchId.value
            })
            const item = result.items.find(item => item.email?.toLowerCase() === row.email.toLowerCase())
            if (!item) throw new Error('批次中未找到该账号，请刷新导入历史核对')
            row.itemIndex = item.index
            row.batchId = result.id
            row.password = ''; row.totp_secret = ''; row.workspaces = []
          }
          row.status = 'pushing'
          const result = await api(`/import/${row.batchId}/commit?item_index=${row.itemIndex}`, 'POST')
          const item = result.items.find(item => item.index === row.itemIndex)
          row.status = item?.status === 'done' ? 'done' : item?.status === 'skipped' ? 'skipped' : 'failed'
          row.message = item?.message || labels[row.status]
          if (mounted) emit('prepared', result)
        } catch (failure) {
          row.status = 'failed'
          row.message = failure.message
          row.workspaces = failure.workspaces || []
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(3, pending.length) }, worker))

  } catch (failure) {
    error.value = failure.message
    pending.forEach(row => { if (row.status === 'waiting') row.status = 'failed' })
  } finally {
    loading.value = false
    if (mounted) {
      emit('loading', false)
    }
  }
}
function credentialsFor(email) {
  if (!email) return ''
  try {
    const row = parseLoginRows(quickSource.value).find(row => row.email.toLowerCase() === email.toLowerCase())
    return row ? `${row.email}----${row.password}----${row.totp_secret}` : ''
  } catch { return '' }
}
defineExpose({ credentialsFor })
onUnmounted(() => {
  mounted = false
  quickSource.value = ''; submittedSource.value = ''
  rows.value.forEach(row => { row.password = ''; row.totp_secret = '' })
  rows.value = []
  emit('loading', false)
})
</script>

<template>
  <section class="panel form-panel login-panel">
    <div class="card-title"><div class="tinted-icon"><ShieldCheck :size="20"/></div><div><h2>账密批量上号</h2><p>每行一个账号，最多 3 个账号同时登录，成功后自动推送入池，失败项保留人工处理</p></div></div>
    <form @submit.prevent="login(false)">
      <div class="login-channel"><label>登录通道<select v-model="provider" :disabled="busy || loading"><option value="local">本地登录</option><option value="session_studio">Session Studio</option></select></label></div>
      <div class="quick-parse"><label>粘贴账号列表 <small>每行：邮箱----密码----2FA密钥 · 最多 500 个账号</small><textarea v-model="quickSource" rows="4" :disabled="loading" spellcheck="false" autocomplete="off" aria-label="批量账号列表"></textarea><small><span class="mobile-full-copy">未启用 2FA 时保留末尾 ----，密钥留空。空行自动忽略。</span><span class="mobile-brief-copy">无 2FA 时保留末尾 ----。</span></small></label></div>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <div class="form-actions"><button class="button primary" :disabled="busy || loading || !configured || !options?.group_ids?.length || !quickSource.trim()"><LoaderCircle v-if="loading" :size="16" class="spinning"/><ShieldCheck v-else :size="16"/>{{ loading ? `正在处理 ${finishedCount} / ${rows.length}` : '批量登录并入池' }}</button><button v-if="failedCount" type="button" class="button" :disabled="busy || loading || !configured || quickSource !== submittedSource" @click="login(true)">重试失败账号（原通道）</button></div>
      <p v-if="!configured" class="small muted">请先到连接设置中保存管理员地址和 Key。</p>
      <p v-else-if="!options?.group_ids?.length" class="small muted">请先点击「上号设置」选择本地号池和 GPT 上游分组。</p>
      <p v-if="loading" class="small muted" role="status">最多 3 个账号并发处理，{{ savedProvider === 'session_studio' ? '第三方排队和登录每个账号最长约 26 分钟' : '本地登录每个账号最长约 3 分半钟' }}；请保持当前页面打开。成功账号自动入池；推送失败的批次会保存到导入历史，可稍后重试。</p>
      <div v-if="rows.length" class="login-batch-results">
        <p class="small muted" role="status">共 {{ rows.length }} 个 · 成功 {{ successCount }} 个 · 失败 {{ failedCount }} 个</p>
        <div class="import-results"><div v-for="row in rows" :key="row.line" class="import-result"><div><strong>第 {{ row.line }} 行 · {{ row.email }}</strong><small>{{ row.message || labels[row.status] }}</small><select v-if="row.workspaces.length" v-model="row.workspace_id" :disabled="loading" :aria-label="`第 ${row.line} 行工作空间`"><option value="">选择工作空间后重试</option><option v-for="space in row.workspaces" :key="space.id" :value="space.id">{{ space.name || space.id }}</option></select></div><span :class="['badge', ['done', 'skipped'].includes(row.status) ? 'success' : row.status === 'failed' ? 'warning' : 'neutral']">{{ labels[row.status] }}</span></div></div>
      </div>
      <p class="small muted login-footnote"><span class="mobile-full-copy">登录成功后加密保存账密与 2FA 密钥，供三方账号认证失效时自动重登；沿用本次登录通道，不回显或写入日志。六位动态验证码不支持自动重登。</span><span class="mobile-brief-copy">成功后加密保存账密及 2FA，用于自动重登；六位动态验证码不可复用。</span></p>
    </form>
  </section>
</template>
