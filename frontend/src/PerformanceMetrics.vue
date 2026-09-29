<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Activity, Mail, RefreshCw } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['mail'])
const metrics = ref(null)
const error = ref('')
const loading = ref(false)
const now = ref(Date.now() / 1000)
let polling, clock
const sample = computed(() => metrics.value?.sample)
const stale = computed(() => metrics.value?.stale || (sample.value && now.value - sample.value.received_at > 65))
const growth = computed(() => {
  if (!sample.value) return '等待采样'
  if (sample.value.baseline === 'initial') return '首次采样，建立基线'
  if (sample.value.baseline === 'zero' && sample.value.rpm > 0) return '从 0 恢复，建立基线'
  const value = sample.value.growth_percent
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
})
const timeText = value => new Date(value * 1000).toLocaleTimeString('zh-CN', { hour12: false })
const points = computed(() => {
  const rows = metrics.value?.history || []
  const max = Math.max(1, ...rows.map(row => row.rpm))
  return rows.map((row, index) => `${rows.length <= 1 ? 0 : index / (rows.length - 1) * 600},${90 - row.rpm / max * 80}`).join(' ')
})
async function refresh() {
  if (loading.value) return
  loading.value = true
  try { metrics.value = await api('/performance'); error.value = '' }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
onMounted(() => {
  void refresh()
  polling = setInterval(refresh, 30000)
  clock = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
})
onUnmounted(() => { clearInterval(polling); clearInterval(clock) })
</script>

<template>
  <section class="panel performance-panel" aria-labelledby="performance-title">
    <div class="panel-heading"><div><h2 id="performance-title"><Activity :size="18"/>性能指标</h2><p>全站 GPT / OpenAI · 最近 60 秒已落库调用 · 后台每 30 秒采样</p></div><button class="button" :disabled="loading" @click="refresh"><RefreshCw :size="15"/>刷新显示</button></div>
    <p v-if="error || metrics?.error" class="notice error" role="alert">{{ error || metrics.error }}，暂不判断增长告警。</p>
    <p v-else-if="stale" class="notice error" role="alert">指标已过期，显示的是上次成功采样结果。</p>
    <p v-if="metrics && !metrics.configured" class="small muted">请先配置 Sub2API 连接以启动 RPM 监控。</p>
    <div class="performance-grid">
      <div><span class="small muted">{{ stale || metrics?.error ? '上次 RPM' : '当前 RPM' }}</span><div class="stat-value">{{ sample?.rpm ?? '—' }}<span>次 / 分钟</span></div></div>
      <div><span class="small muted">较上次采样增长率</span><div :class="['rpm-growth', { 'orange-text': sample?.alert }]">{{ growth }}</div><small class="muted">上次 RPM：{{ sample?.previous_rpm ?? '—' }} · 严格超过 100% 告警</small></div>
      <div><span :class="['badge', metrics?.mail_enabled ? 'success' : 'neutral']"><Mail :size="14"/>{{ metrics?.mail_enabled ? 'RPM 邮件告警已开启' : 'RPM 邮件告警未开启' }}</span><p><button class="text-button" @click="emit('mail')">配置邮件通知</button></p></div>
    </div>
    <svg v-if="metrics?.history?.length > 1" class="rpm-trend" viewBox="0 0 600 100" role="img" aria-label="最近 RPM 采样趋势"><polyline :points="points" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
    <p v-if="metrics?.mail_error" class="notice error" role="alert">邮件告警未入队：{{ metrics.mail_error }}</p>
    <p class="small muted">首次采样、上次 RPM 为 0 或采样中断后先建立基线，不计算翻倍告警。监控不依赖页面打开，也不改变账号调度。</p>
    <p v-if="sample" class="small muted">采样时间 {{ timeText(sample.sampled_at) }} · {{ sample.source === 'postgres' ? 'PG 聚合' : 'API 查询' }} · <template v-if="metrics.next_check_at">下次采样 {{ Math.max(0, Math.ceil(metrics.next_check_at - now)) }} 秒后</template> · 最近 {{ metrics.history.length }} 次有效采样</p>
  </section>
</template>

<style scoped>
.performance-panel { margin-bottom: 20px; }
.performance-panel h2 { display: flex; gap: 8px; align-items: center; }
.performance-grid { display: grid; grid-template-columns: 1fr 1.3fr 1fr; gap: 24px; align-items: center; padding: 8px 0 16px; }
.rpm-growth { font-size: 24px; font-weight: 650; margin: 10px 0; }
.rpm-trend { width: 100%; height: 100px; color: var(--purple, #7255d6); }
.performance-panel .badge { gap: 6px; }
@media (max-width: 760px) { .performance-grid { grid-template-columns: 1fr; gap: 16px; } }
</style>
