<script setup>
import { computed } from 'vue'

const props = defineProps({ account: Object, now: Number })
const stamp = value => {
  if (value == null || value === '') return null
  const parsed = typeof value === 'number' ? value : Date.parse(value) / 1000
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}
const updated = computed(() => stamp(props.account.remote.extra?.codex_usage_updated_at))
const planType = computed(() => String(props.account.remote.plan_type || '').trim().toLowerCase())
const windows = computed(() => ['5h', '7d'].filter(window => window !== '5h' || planType.value === 'plus').map(window => {
  const extra = props.account.remote.extra || {}
  const raw = extra[`codex_${window}_used_percent`]
  const used = raw == null || raw === '' ? NaN : Number(raw)
  const seconds = Number(extra[`codex_${window}_reset_after_seconds`])
  const reset = stamp(extra[`codex_${window}_reset_at`]) || (updated.value && Number.isFinite(seconds) && seconds > 0 ? updated.value + seconds : null)
  const usedPercent = Number.isFinite(used) && used >= 0 ? Math.min(used, 100) : 0
  return { label: window === '5h' ? '5 小时' : '7 天', used: usedPercent, hasSnapshot: Number.isFinite(used) && used >= 0, remaining: 100 - usedPercent, reset }
}))
const dateText = value => new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
function resetText(reset) {
  if (!reset) return '重置时间未知'
  if (reset <= props.now) return '已到重置时间，待更新'
  const minutes = Math.ceil((reset - props.now) / 60)
  if (minutes >= 1440) return `${Math.floor(minutes / 1440)}天${Math.floor(minutes % 1440 / 60)}小时后重置`
  return `${Math.floor(minutes / 60)}小时${minutes % 60}分后重置`
}
</script>

<template>
  <div class="quota-cell">
    <template v-if="windows.length">
      <div v-for="window in windows" :key="window.label" class="quota-window">
        <span>{{ window.label }} · 剩余 {{ Number(window.remaining.toFixed(1)) }}%<small v-if="!window.hasSnapshot" class="quota-default">默认满额</small></span>
        <div class="quota-track" role="meter" :aria-label="`${window.label}剩余额度`" :aria-valuenow="window.remaining" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: `${window.remaining}%` }" :class="{ low: window.remaining <= 10, exhausted: window.remaining <= 0 }"></span>
        </div>
        <small :title="window.reset ? dateText(window.reset) : ''">{{ resetText(window.reset) }}</small>
      </div>
      <small :title="updated ? dateText(updated) : ''">{{ updated ? `快照 ${dateText(updated)}` : '快照时间未知' }}</small>
    </template>
    <span v-else class="muted">暂无额度数据</span>
  </div>
</template>

<style scoped>
.quota-cell { min-width: 170px; font-size: 12px; }
.quota-window + .quota-window { margin-top: 8px; }
.quota-cell small { display: block; color: var(--text-muted, #718096); font-size: 11px; margin-top: 3px; }
.quota-track { height: 4px; background: #e5e7eb; border-radius: 3px; overflow: hidden; margin-top: 4px; }
.quota-track span { display: block; height: 100%; background: #14b8a6; }
.quota-track span.low { background: #f59e0b; }
.quota-track span.exhausted { background: #ef4444; }
.quota-default { display: inline !important; margin-left: 4px; }
</style>
