<script setup>
import { computed } from 'vue'

const props = defineProps({ account: Object, now: Number })
const stamp = value => {
  if (value == null || value === '') return null
  const parsed = typeof value === 'number' ? value : Date.parse(value) / 1000
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}
const updated = computed(() => stamp(props.account.remote.extra?.codex_usage_updated_at))
const windows = computed(() => ['5h', '7d'].map(window => {
  const extra = props.account.remote.extra || {}
  const raw = extra[`codex_${window}_used_percent`]
  const used = raw == null || raw === '' ? NaN : Number(raw)
  const seconds = Number(extra[`codex_${window}_reset_after_seconds`])
  const reset = stamp(extra[`codex_${window}_reset_at`]) || (updated.value && Number.isFinite(seconds) && seconds > 0 ? updated.value + seconds : null)
  return { label: window === '5h' ? '5 小时' : '7 天', used: Number.isFinite(used) && used >= 0 ? used : null, reset }
}))
const hasData = computed(() => windows.value.some(window => window.used !== null))
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
    <template v-if="hasData">
      <div v-for="window in windows" :key="window.label" class="quota-window">
        <span>{{ window.label }} · {{ window.used === null ? '暂无数据' : `已用 ${Number(window.used.toFixed(1))}%` }}</span>
        <div v-if="window.used !== null" class="quota-track" role="meter" :aria-label="`${window.label}额度已用比例`" :aria-valuenow="Math.min(window.used, 100)" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: `${Math.min(window.used, 100)}%` }" :class="{ high: window.used >= 90 }"></span>
        </div>
        <small v-if="window.used !== null" :title="window.reset ? dateText(window.reset) : ''">{{ resetText(window.reset) }}</small>
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
.quota-track span.high { background: #f59e0b; }
</style>
