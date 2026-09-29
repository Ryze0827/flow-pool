<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Check, CircleHelp, Mail, RefreshCw, Send, ShieldCheck } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const settings = ref(null)
const recipients = ref('')
const messages = ref([])
const busy = ref(false)
const refreshing = ref(false)
const error = ref('')
const detail = ref(null)
const refreshIntervalSeconds = 10
const statuses = { pending: '待发送', sending: '发送中', sent: '已提交 SMTP', failed: '发送失败', skipped: '已取消' }
const kinds = { warning: '告警', cooldown: '冷却', recovery: '冷却恢复', rpm: 'RPM 增长', test: '测试' }
const timeText = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'
let polling

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try { messages.value = await api('/mail/messages') } catch (e) { error.value = e.message } finally { refreshing.value = false }
}
async function load() {
  try {
    settings.value = await api('/mail/settings')
    recipients.value = settings.value.recipients.join('\n')
    error.value = ''
    await refresh()
  } catch (e) { error.value = e.message }
}
function payload() {
  return { ...settings.value, recipients: recipients.value.split(/[\s,;，；]+/).filter(Boolean) }
}
async function perform(operation) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const value = payload()
    if (operation === 'save') {
      settings.value = await api('/mail/settings', 'PUT', value)
      recipients.value = settings.value.recipients.join('\n')
      emit('notify', '邮件通知配置已保存，立即生效')
    } else {
      const result = await api(operation === 'connection' ? '/mail/test-connection' : '/mail/test', 'POST', value)
      emit('notify', result.message)
    }
  } catch (e) { error.value = e.message } finally { busy.value = false; await refresh() }
}
onMounted(() => { void load(); polling = setInterval(refresh, refreshIntervalSeconds * 1000) })
onUnmounted(() => clearInterval(polling))
</script>

<template>
  <div class="mail-notifications">
    <div v-if="error" class="notice error" role="alert"><CircleHelp :size="17"/>{{ error }}<button v-if="!settings" class="text-button" @click="load">重试</button></div>
    <div v-if="!settings && !error" class="quiet-empty">正在加载邮件配置…</div>
    <form v-if="settings" class="settings-layout" @submit.prevent="perform('save')">
      <section class="panel form-panel">
        <div class="card-title"><div class="tinted-icon"><Mail :size="22"/></div><div><h2>发送设置</h2><p>通过 SMTP 发送首字延迟、RPM 增长告警、冷却及恢复通知</p></div><label class="check-row"><input v-model="settings.enabled" type="checkbox" :disabled="busy"/>开启邮件通知</label></div>
        <div class="form-grid">
          <label>SMTP 服务器<input v-model.trim="settings.smtp_host" placeholder="smtp.example.com" maxlength="253" :disabled="busy" :required="settings.enabled"/></label>
          <label>SMTP 端口<input v-model.number="settings.smtp_port" type="number" min="1" max="65535" required :disabled="busy"/></label>
          <label>SMTP 用户名<input v-model.trim="settings.smtp_username" autocomplete="off" placeholder="通常为发件邮箱" :disabled="busy"/></label>
          <label>SMTP 密码 / 授权码<input v-model="settings.smtp_password" type="password" autocomplete="new-password" :placeholder="settings.smtp_password_configured ? '已设置，留空保留原密码' : '填写密码或邮箱授权码'" :disabled="busy"/><small>更换服务器或用户名时需重新填写密码</small></label>
          <label>发件邮箱<input v-model.trim="settings.smtp_from_email" type="email" placeholder="notify@example.com" :required="settings.enabled" :disabled="busy"/></label>
          <label>发件人名称<input v-model="settings.smtp_from_name" maxlength="120" :disabled="busy"/></label>
        </div>
        <label class="check-row mail-tls"><input v-model="settings.smtp_use_tls" type="checkbox" :disabled="busy"/>启用 TLS 加密</label>
        <p class="small muted">开启时：465 端口使用 SSL/TLS，其它端口使用 STARTTLS（通常为 587）。</p>
        <label>收件邮箱<textarea v-model="recipients" rows="3" placeholder="admin@example.com&#10;ops@example.com" :required="settings.enabled" :disabled="busy"/><small>支持多个收件人，每行一个或用逗号分隔，最多 20 个</small></label>
        <fieldset class="guard-pools mail-types"><legend>通知类型</legend><div class="guard-pool-options"><label class="check-row"><input v-model="settings.notify_warning" type="checkbox" :disabled="busy"/>告警通知</label><label class="check-row"><input v-model="settings.notify_cooldown" type="checkbox" :disabled="busy"/>冷却通知</label><label class="check-row"><input v-model="settings.notify_recovery" type="checkbox" :disabled="busy"/>冷却恢复通知</label><label class="check-row"><input v-model="settings.notify_rpm" type="checkbox" :disabled="busy"/>RPM 增长告警</label></div></fieldset>
        <label>同一账号告警通知间隔（秒）<input v-model.number="settings.warning_interval_seconds" type="number" min="60" max="86400" required :disabled="busy"/><small>默认 900 秒（15 分钟），避免每轮巡检重复发信；冷却及恢复通知独立发送</small></label>
        <div class="form-actions mail-actions"><button class="button primary" :disabled="busy"><Check :size="16"/>保存配置</button><button class="button" type="button" :disabled="busy" @click="perform('connection')"><RefreshCw :size="16"/>测试连接</button><button class="button" type="button" :disabled="busy" @click="perform('test')"><Send :size="16"/>发送测试邮件</button></div>
      </section>
      <aside class="help-panel"><ShieldCheck :size="26"/><h3>关注真正的状态变化</h3><p>告警通知：慢调用比例达限、累计次数不足，或因缺少可用替补而暂缓停调时发送。</p><p>冷却通知：确认停止调度或切换生图，并完成替补复核后发送，每轮冷却只通知一次。</p><p>冷却恢复通知：到期、手动提前结束或替补不可用而提前恢复时，确认恢复成功后发送，每轮一次。恢复失败不发送成功通知，不受告警间隔或守护开关影响。</p><p>RPM 告警：后台每 30 秒采样，最近 60 秒 GPT 调用数较上次增长严格超过 100% 时，每次达限采样发送一封，不受账号告警间隔或调度开关影响。首次采样、从 0 恢复或中断后先建立基线。</p><p>账号邮件仅展示账号、措施和拉起账号；未新启用替补时显示“否”，冷却措施附恢复时间。</p><hr/><p>保存后自动通知新事件，不补发历史记录。测试按钮使用当前表单配置；发送测试邮件不受通知总开关限制。</p><p>SMTP 密码在本机加密保存。邮件发送失败会记录原因，调度继续执行；发送结果不明时不自动重发，避免重复邮件。</p></aside>
    </form>
    <section class="panel mail-history">
      <div class="panel-heading"><div><h2>发送记录 <span class="count-chip">{{ messages.length }}</span></h2><p>展示最近 100 条；已提交 SMTP 不代表收件箱已送达</p></div><button class="button" :disabled="refreshing" @click="refresh"><RefreshCw :size="15"/>刷新 · 每 {{ refreshIntervalSeconds }} 秒</button></div>
      <div v-if="!messages.length" class="quiet-empty">暂无发送记录，保存配置后可发送测试邮件。</div>
      <div v-else class="table-scroll"><table><thead><tr><th>时间</th><th>类型</th><th>邮件</th><th>收件人</th><th>状态</th></tr></thead><tbody><tr v-for="message in messages" :key="message.id"><td class="muted small">{{ timeText(message.created_at) }}</td><td>{{ kinds[message.kind] }}</td><td><button class="text-button mail-subject" @click="detail = message">{{ message.subject }}</button><small v-if="message.account_id">账号 #{{ message.account_id }}</small></td><td>{{ message.recipients.join('、') }}</td><td><span :class="['badge', message.status === 'sent' ? 'success' : message.status === 'failed' ? 'warning' : 'neutral']">{{ statuses[message.status] }}</span><small v-if="message.error" class="cell-error">{{ message.error }}</small></td></tr></tbody></table></div>
    </section>
    <div v-if="detail" class="modal-overlay" @click.self="detail = null"><section class="modal detail-modal" role="dialog" aria-modal="true" aria-labelledby="mail-detail-title"><div class="modal-heading"><h2 id="mail-detail-title">邮件内容</h2><button class="button" @click="detail = null">关闭</button></div><div class="modal-body"><h3>{{ detail.subject }}</h3><p class="small muted">收件人：{{ detail.recipients.join('、') }}</p><pre class="mail-body">{{ detail.body }}</pre></div></section></div>
  </div>
</template>
