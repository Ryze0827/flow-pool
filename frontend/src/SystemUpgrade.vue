<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowUpCircle, ArrowUpToLine, CircleAlert, CircleCheck, Info, LoaderCircle, RefreshCw, X } from 'lucide-vue-next'
import { api } from './api'
import { createUpgradePolling } from './upgradePolling'
import { loadUpgradeStatus } from './upgradeStatus'
import { version } from '../package.json'

const props = defineProps({ busy: Boolean })
const dialog = ref(null)
const state = ref({ repo: {}, running: false, job: null })
const loaded = ref(false)
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const reconnecting = ref(false)
const startedJobId = ref(null)
const timeText = value => {
  if (!value) return '—'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
const field = value => loaded.value ? (value || '—') : '…'
const active = computed(() => submitting.value || state.value.running)
const updateAvailable = computed(() => Boolean(state.value.repo.update_available))
const entryTitle = computed(() => `FlowPool v${version} · ${active.value ? '查看升级进度' : updateAvailable.value ? '发现新版本，点击升级' : '检查更新'}`)
const labels = { success: '升级完成', unchanged: '已是最新', failed: '升级失败', rolled_back: '已回退', recovery_failed: '需要处理', interrupted: '任务中断', running: '升级中' }
const jobLabel = computed(() => labels[state.value.job?.status] || '等待升级')
const latestStep = computed(() => {
  const items = state.value.job?.steps
  return items?.length ? items[items.length - 1] : null
})
const upgradeDone = computed(() => {
  const { job, repo } = state.value
  return job?.status === 'success' && job.new_commit === repo.commit && !repo.update_available
})
const startable = computed(() => !active.value && !props.busy && !loading.value && state.value.repo.ready && state.value.repo.update_available)
const statusView = computed(() => {
  const { repo } = state.value
  if (active.value) return { tone: 'running', icon: LoaderCircle, spin: true, title: '正在升级', detail: '关闭弹框不会中断升级；升级完成前暂停页面写入操作。' }
  if (loading.value) return { tone: 'neutral', icon: LoaderCircle, spin: true, title: '检测更新中', detail: '正在获取远端版本信息，请稍候。' }
  if (!loaded.value) return error.value
    ? { tone: 'warning', icon: CircleAlert, title: '无法读取升级状态', detail: error.value }
    : { tone: 'neutral', icon: LoaderCircle, spin: true, title: '正在读取版本信息' }
  if (upgradeDone.value) return { tone: 'success', icon: CircleCheck, title: '升级已完成', detail: '正在自动刷新页面并加载新版本。' }
  if (!repo.ready) return { tone: 'warning', icon: CircleAlert, title: '暂不能升级', detail: repo.reason }
  if (repo.update_available) return { tone: 'info', icon: ArrowUpCircle, title: '发现新版本', detail: `将从 ${repo.commit.slice(0, 8)} 更新到 ${repo.latest_commit.slice(0, 8)}。` }
  if (repo.reason) return { tone: 'warning', icon: CircleAlert, title: '无法确认是否有新版本', detail: repo.reason }
  if (repo.latest_commit) return { tone: 'success', icon: CircleCheck, title: '已是最新版本', detail: `远端版本 ${repo.latest_commit.slice(0, 8)} 与当前一致。` }
  return { tone: 'neutral', icon: Info, title: '尚未检查远端版本', detail: '点击「检查更新」确认是否有新版本。' }
})
let reloading = false
watch(upgradeDone, done => {
  if (!done || reloading || state.value.job?.id !== startedJobId.value) return
  reloading = true
  globalThis.location.reload()
})
let mounted = true, fetching = false
let requestController
const polling = createUpgradePolling(() => refresh(!state.value.running), () => state.value.running)

async function refresh(checkRemote = false) {
  if (fetching) return
  polling.cancel()
  fetching = true
  loading.value = true
  if (checkRemote) {
    error.value = ''
    reconnecting.value = false
    if (!active.value) startedJobId.value = null
  }
  const controller = new AbortController()
  requestController = controller
  const timeout = setTimeout(() => controller.abort(), checkRemote ? 200000 : 10000)
  try {
    await loadUpgradeStatus(api, checkRemote, controller.signal, result => {
      if (mounted) { state.value = result; loaded.value = true }
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
  error.value = ''
  await refresh(true)
}
async function start() {
  if (!startable.value) return
  submitting.value = true
  error.value = ''
  try {
    const job = await api('/upgrade', 'POST')
    if (!mounted) return
    startedJobId.value = job.id
    state.value = { ...state.value, running: true, job }
    polling.schedule()
  } catch (failure) { error.value = failure.message }
  finally { submitting.value = false }
}
onMounted(() => refresh(true))
onUnmounted(() => {
  mounted = false
  polling.stop()
  requestController?.abort()
})
</script>

<template>
  <button :class="['upgrade-entry', { 'has-update': updateAvailable }]" :title="entryTitle" :aria-label="entryTitle" @click="open"><span>v{{ version }}</span><LoaderCircle v-if="active" :size="11" class="spinning" aria-hidden="true"/><span v-else-if="updateAvailable" class="upgrade-dot" aria-hidden="true"></span></button>
  <dialog ref="dialog" class="modal import-settings-dialog upgrade-dialog" aria-labelledby="upgrade-title" @click.self="dialog.close()">
    <div class="modal-heading"><div><h2 id="upgrade-title">升级 FlowPool</h2><p>拉取远程代码，构建后自动部署</p></div><button class="icon-button" aria-label="关闭升级窗口" @click="dialog.close()"><X :size="20"/></button></div>
    <div class="modal-body upgrade-body">
      <div :class="['upgrade-status', 'tone-' + statusView.tone]">
        <component :is="statusView.icon" :size="20" :class="{ spinning: statusView.spin }"/>
        <div><strong>{{ statusView.title }}</strong><p v-if="statusView.detail">{{ statusView.detail }}</p></div>
      </div>
      <div v-if="!loading || active" class="upgrade-version"><div><small>当前版本</small><strong>v{{ version }}</strong><small v-if="state.repo.commit">提交 {{ state.repo.commit.slice(0, 8) }}</small></div><div><small>目标提交</small><strong>{{ field((state.repo.latest_commit || state.job?.new_commit)?.slice(0, 8)) }}</strong></div></div>
      <p v-if="!loading || active" class="small muted upgrade-hint">自动安装依赖、构建并重启；升级期间请勿进行其它写入操作。</p>
      <p v-if="busy && !active && !loading" class="notice compact">页面正在执行其他操作，完成后即可开始升级。</p>
      <div v-if="state.job && (!loading || active)" class="upgrade-progress">
        <div class="section-heading"><strong>{{ jobLabel }}</strong><span :class="['badge', ['success', 'unchanged'].includes(state.job.status) ? 'success' : active ? 'neutral' : 'warning']">{{ active ? '后台执行' : '已结束' }}</span></div>
        <p role="status" aria-live="polite">{{ reconnecting ? '暂时无法连接服务，可能正在重启，正在自动重连…' : state.job.message }}</p>
        <div class="upgrade-progress-summary"><strong v-if="latestStep">当前步骤：{{ latestStep.message }}</strong><small class="muted">最近更新：{{ timeText(state.job.updated_at) }} · {{ active ? '自动跟进中' : '任务已结束' }}</small></div>
      </div>
      <p v-if="error && (!loading || active)" class="notice error" role="alert">{{ error }}</p>
    </div>
    <div class="modal-footer">
      <span v-if="upgradeDone && !loading" class="small muted">正在自动刷新页面…</span>
      <template v-else>
        <button class="button upgrade-check" :disabled="loading || active" @click="refresh(true)"><RefreshCw :size="15" :class="{ spinning: loading }"/>检查更新</button>
        <button class="button primary" :disabled="!startable" @click="start"><LoaderCircle v-if="active" :size="16" class="spinning"/><ArrowUpToLine v-else :size="16"/>{{ active ? '正在升级…' : '开始升级' }}</button>
      </template>
    </div>
  </dialog>
</template>
