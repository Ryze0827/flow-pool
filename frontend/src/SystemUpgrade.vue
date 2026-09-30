<script setup>
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { ArrowUpToLine, CheckCheck, LoaderCircle, RefreshCw, X } from 'lucide-vue-next'
import { api } from './api'
import { createUpgradePolling } from './upgradePolling'
import { loadUpgradeStatus } from './upgradeStatus'

const props = defineProps({ busy: Boolean })
const dialog = ref(null)
const state = ref({ repo: {}, running: false, job: null })
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const reconnecting = ref(false)
const timeText = value => {
  if (!value) return '—'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
const active = computed(() => submitting.value || state.value.running)
const labels = { success: '升级完成', unchanged: '已是最新', failed: '升级失败', rolled_back: '已回退', recovery_failed: '需要处理', interrupted: '任务中断', running: '升级中' }
const jobLabel = computed(() => labels[state.value.job?.status] || '等待升级')
let mounted = true, fetching = false
let requestController
const polling = createUpgradePolling(() => refresh(false), () => state.value.running)

async function refresh(checkRemote = false) {
  if (fetching) return
  polling.cancel()
  fetching = true
  loading.value = true
  const controller = new AbortController()
  requestController = controller
  const timeout = setTimeout(() => controller.abort(), checkRemote ? 200000 : 10000)
  try {
    await loadUpgradeStatus(api, checkRemote, controller.signal, result => {
      if (mounted) state.value = result
    })
    if (!mounted) return
    reconnecting.value = false
    error.value = ''
  } catch (failure) {
    if (!mounted) return
    if (state.value.running) reconnecting.value = true
    else error.value = failure.name === 'AbortError' ? '请求超时，暂时无法确认更新状态，请稍后重试' : failure.message || '无法读取升级状态'
  } finally {
    clearTimeout(timeout)
    requestController = null
    fetching = false
    if (mounted) loading.value = false
    polling.schedule()
  }
}
async function open() {
  await nextTick()
  dialog.value.showModal()
  await refresh(true)
}
async function start() {
  if (active.value || props.busy || !state.value.repo.ready) return
  submitting.value = true
  error.value = ''
  try {
    const job = await api('/upgrade', 'POST')
    if (!mounted) return
    state.value = { ...state.value, running: true, job }
    polling.schedule()
  } catch (failure) { error.value = failure.message }
  finally { submitting.value = false }
}
function reload() { globalThis.location.reload() }
onUnmounted(() => {
  mounted = false
  polling.stop()
  requestController?.abort()
})
</script>

<template>
  <button class="nav-item upgrade-entry" :title="active ? '查看升级进度' : '升级 FlowPool'" aria-label="升级 FlowPool" @click="open"><LoaderCircle v-if="active" :size="18" class="spinning"/><ArrowUpToLine v-else :size="18"/><span>{{ active ? '升级中' : '一键升级' }}</span></button>
  <dialog ref="dialog" class="modal import-settings-dialog upgrade-dialog" aria-labelledby="upgrade-title" @click.self="dialog.close()">
    <div class="modal-heading"><div><h2 id="upgrade-title">升级 FlowPool</h2><p>拉取远程代码，构建后自动部署</p></div><button class="icon-button" aria-label="关闭升级窗口" @click="dialog.close()"><X :size="20"/></button></div>
    <div class="modal-body upgrade-body">
      <div class="upgrade-version"><div><small>当前分支</small><strong>{{ state.repo.branch || '尚未提交' }}</strong></div><div><small>当前版本</small><strong>{{ state.repo.commit?.slice(0, 8) || '—' }}</strong><small v-if="state.repo.commit_at">提交于 {{ timeText(state.repo.commit_at) }}</small></div><div><small>更新来源</small><strong>{{ state.repo.upstream || '未设置跟踪分支' }}</strong><small v-if="state.repo.latest_commit">{{ state.repo.update_available ? `发现新版本 ${state.repo.latest_commit.slice(0, 8)}` : '远端已是最新版本' }}</small></div></div>
      <p class="small muted">先安装依赖、构建并验证，再短暂重启。部署失败会尝试回退；本地未提交改动会阻止升级。</p>
      <p v-if="state.repo.update_available && !active" class="notice compact">发现远端新版本，点击“开始升级”执行更新。</p>
      <p v-else-if="!state.repo.ready && !active" class="notice compact">{{ state.repo.reason || '正在检查部署环境…' }}</p>
      <div v-if="state.job" class="upgrade-progress" role="status" aria-live="polite">
        <div class="section-heading"><strong>{{ jobLabel }}</strong><span :class="['badge', ['success', 'unchanged'].includes(state.job.status) ? 'success' : active ? 'neutral' : 'warning']">{{ active ? '后台执行' : '已结束' }}</span></div>
        <p>{{ reconnecting ? '暂时无法连接服务，可能正在重启，正在自动重连…' : state.job.message }}</p>
        <small v-if="state.job.new_commit">版本 {{ state.job.old_commit.slice(0, 8) }} → {{ state.job.new_commit.slice(0, 8) }}</small>
        <ol v-if="state.job.steps?.length" class="upgrade-steps"><li v-for="(step, index) in state.job.steps" :key="index"><span>{{ step.message }}</span><time>{{ timeText(step.time) }}</time></li></ol>
        <small class="muted">最近更新：{{ timeText(state.job.updated_at) }} · {{ active ? '每 2 秒自动刷新' : '升级任务已结束' }}</small>
      </div>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <p v-if="active" class="small muted">关闭弹框不会中断升级；升级完成前暂停页面写入操作。</p>
    </div>
    <div class="modal-footer"><button class="button" :disabled="loading || active" @click="refresh(true)"><RefreshCw :size="15" :class="{ spinning: loading }"/>检查更新</button><button v-if="state.job?.status === 'success' && !active" class="button primary" @click="reload"><CheckCheck :size="16"/>完成</button><button class="button primary" :disabled="active || busy || !state.repo.ready || loading || !state.repo.update_available" @click="start"><LoaderCircle v-if="active" :size="16" class="spinning"/><ArrowUpToLine v-else :size="16"/>{{ active ? '正在升级…' : '开始升级' }}</button></div>
  </dialog>
</template>
