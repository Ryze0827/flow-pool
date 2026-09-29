<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { api } from './api'

const metrics = ref(null)
const error = ref('')
const loading = ref(false)
const now = ref(Date.now() / 1000)
let polling, clock
const sample = computed(() => metrics.value?.sample)
const stale = computed(() => metrics.value?.stale || (sample.value && now.value - sample.value.received_at > 65))
const growth = computed(() => {
  const value = sample.value?.growth_percent
  if (value == null) return sample.value ? '建立基线' : '等待采样'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
})
const status = computed(() => {
  if (error.value || metrics.value?.error) return '采样异常'
  if (metrics.value && !metrics.value.configured) return '未配置连接'
  if (stale.value) return '数据已过期'
  if (metrics.value?.mail_error) return '邮件告警异常'
  return growth.value
})
const warning = computed(() => Boolean(error.value || metrics.value?.error || stale.value || metrics.value?.mail_error || sample.value?.alert))
const detail = computed(() => {
  const parts = ['全站 GPT · 最近 60 秒调用 · 较上次采样增长超过 100% 时邮件告警']
  if (sample.value) parts.push(`采样时间：${new Date(sample.value.sampled_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}`)
  parts.push(metrics.value?.mail_enabled ? '邮件告警已开启' : '邮件告警未开启')
  if (error.value || metrics.value?.error) parts.push(error.value || metrics.value.error)
  if (metrics.value?.mail_error) parts.push(metrics.value.mail_error)
  return parts.join('\n')
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
  <div class="stat-card performance-card" :title="detail">
    <div class="stat-label">性能指标 <button class="icon-button performance-refresh" type="button" :disabled="loading" :aria-busy="loading" title="刷新性能指标" aria-label="刷新性能指标" @click="refresh"><RefreshCw :size="17" :class="{ spinning: loading }"/></button></div>
    <div class="stat-value"><strong>{{ sample?.rpm ?? '—' }}</strong><span>RPM</span></div>
    <div class="stat-foot"><span :class="{ 'orange-text': warning }">{{ status }}</span><span>· 30s 刷新</span></div>
  </div>
</template>

<style scoped>
.performance-card { min-width: 0; }
.performance-refresh { width: 28px; height: 28px; margin: -6px; }
.stat-value { display: flex; align-items: baseline; }
.stat-value strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; font: inherit; }
.stat-value > span { flex-shrink: 0; }
.stat-foot { gap: 5px; white-space: nowrap; }
.stat-foot > span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.stat-foot > span:last-child { flex-shrink: 0; }
</style>
