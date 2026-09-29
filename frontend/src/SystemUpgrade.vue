<script setup>
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { ArrowUpToLine, CheckCheck, LoaderCircle, RefreshCw, X } from 'lucide-vue-next'
import { api } from './api'

const props = defineProps({ busy: Boolean })
const dialog = ref(null)
const state = ref({ repo: {}, running: false, job: null })
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const reconnecting = ref(false)
const timeText = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'
const active = computed(() => submitting.value || state.value.running)
const labels = { success: '升级完成', unchanged: '已是最新', failed: '升级失败', rolled_back: '已回退', recovery_failed: '需要处理', interrupted: '任务中断', running: '升级中' }
const jobLabel = computed(() => labels[state.value.job?.status] || '等待升级')
let mounted = true, fetching = false

async function refresh() {
  if (fetching) return
  fetching = true
  loading.value = true
  try {
    const result = await api('/upgrade')
    if (!mounted) return
    state.value = result
    reconnecting.value = false
    error.value = ''
  } catch (failure) {
    if (!mounted) return
    if (state.value.running) reconnecting.value = true
    else error.value = failure.message || '无法读取升级状态'
  } finally {
    fetching = false
    if (mounted) loading.value = false
  }
}
async function open() {
  await nextTick()
  dialog.value.showModal()
  await refresh()
}
async function start() {
  if (active.value || props.busy || !state.value.repo.ready) return
  submitting.value = true
  error.value = ''
  try {
    const job = await api('/upgrade', 'POST')
    state.value = { ...state.value, running: true, job }
  } catch (failure) { error.value = failure.message }
  finally { submitting.value = false }
}
function reload() { globalThis.location.reload() }
onUnmounted(() => { mounted = false })
</script>

<template>
  <button class="nav-item upgrade-entry" :title="active ? '查看升级进度' : '升级 FlowPool'" aria-label="升级 FlowPool" @click="open"><LoaderCircle v-if="active" :size="18" class="spinning"/><ArrowUpToLine v-else :size="18"/><span>{{ active ? '升级中' : '一键升级' }}</span></button>
  <dialog ref="dialog" class="modal import-settings-dialog upgrade-dialog" aria-labelledby="upgrade-title" @click.self="dialog.close()">
    <div class="modal-heading"><div><h2 id="upgrade-title">升级 FlowPool</h2><p>拉取远程代码，构建后自动部署</p></div><button class="icon-button" aria-label="关闭升级窗口" @click="dialog.close()"><X :size="20"/></button></div>
    <div class="modal-body upgrade-body">
      <div class="upgrade-version"><div><small>当前分支</small><strong>{{ state.repo.branch || '尚未提交' }}</strong></div><div><small>当前版本</small><strong>{{ state.repo.commit?.slice(0, 8) || '—' }}</strong></div><div><small>更新来源</small><strong>{{ state.repo.upstream || '未设置跟踪分支' }}</strong></div></div>
      <p class="small muted">先安装依赖、构建并验证，再短暂重启。部署失败会尝试回退；本地未提交改动会阻止升级。</p>
      <p v-if="!state.repo.ready && !active" class="notice compact">{{ state.repo.reason || '正在检查部署环境…' }}</p>
      <div v-if="state.job" class="upgrade-progress" role="status" aria-live="polite">
        <div class="section-heading"><strong>{{ jobLabel }}</strong><span :class="['badge', ['success', 'unchanged'].includes(state.job.status) ? 'success' : active ? 'neutral' : 'warning']">{{ active ? '后台执行' : '已结束' }}</span></div>
        <p>{{ reconnecting ? '暂时无法连接服务，可能正在重启，请稍后手动刷新状态。' : state.job.message }}</p>
        <small v-if="state.job.new_commit">版本 {{ state.job.old_commit.slice(0, 8) }} → {{ state.job.new_commit.slice(0, 8) }}</small>
        <ol v-if="state.job.steps?.length" class="upgrade-steps"><li v-for="(step, index) in state.job.steps" :key="index"><span>{{ step.message }}</span><time>{{ timeText(step.time) }}</time></li></ol>
        <small class="muted">最近更新：{{ timeText(state.job.updated_at) }} · 手动刷新状态</small>
      </div>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <p v-if="active" class="small muted">关闭弹框不会中断升级；升级完成前暂停页面写入操作。</p>
    </div>
    <div class="modal-footer"><button class="button" :disabled="loading" @click="refresh"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新状态</button><button v-if="state.job?.status === 'success' && !active" class="button" @click="reload"><CheckCheck :size="16"/>刷新页面</button><button class="button primary" :disabled="active || busy || !state.repo.ready || loading" @click="start"><LoaderCircle v-if="active" :size="16" class="spinning"/><ArrowUpToLine v-else :size="16"/>{{ active ? '正在升级…' : '开始升级' }}</button></div>
  </dialog>
</template>
