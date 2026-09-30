<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ListFilter, X } from 'lucide-vue-next'

defineProps({
  accounts: { type: Array, default: () => [] },
  sampleSize: { type: Number, default: 0 },
  refreshText: { type: String, default: '' }
})

const open = ref(false)
const sidebar = ref(null)
const toggle = ref(null)

function reveal(event) {
  if (event.pointerType === 'mouse') open.value = true
}
function activate(event) {
  // Mouse users already opened the panel by hovering; taps and keyboard clicks toggle it.
  open.value = event.pointerType === 'mouse' ? true : !open.value
}
function leave(event) {
  if (event.pointerType === 'mouse') open.value = false
}
function close(returnFocus = false) {
  open.value = false
  if (returnFocus) toggle.value?.focus()
}
function blur(event) {
  if (!sidebar.value?.contains(event.relatedTarget) && !sidebar.value?.matches(':hover')) close()
}
function outside(event) {
  if (!sidebar.value?.contains(event.target)) close()
}

onMounted(() => document.addEventListener('pointerdown', outside))
onUnmounted(() => document.removeEventListener('pointerdown', outside))
</script>

<template>
  <aside ref="sidebar" :class="['ranking-sidebar', { 'is-open': open }]" aria-label="调用占比排名" @pointerleave="leave" @focusout="blur" @keydown.esc.prevent.stop="close(true)">
    <button ref="toggle" class="button ranking-toggle" type="button" aria-label="查看调用占比排名" title="调用占比排名" aria-controls="usage-ranking-sidebar" :aria-expanded="open" @pointerenter="reveal" @click="activate"><ListFilter :size="18"/></button>
    <section v-show="open" id="usage-ranking-sidebar" class="panel ranking-panel">
      <div class="panel-heading"><div><h2>调用占比排名</h2><p>最新 {{ sampleSize }} 条调用 · {{ refreshText }}</p></div><button class="icon-button" type="button" aria-label="收起调用占比排名" @click="close(true)"><X :size="16"/></button></div>
      <div class="ranking-list"><div v-for="(item, index) in accounts" :key="item.account_id" class="ranking-row"><span class="ranking-index">{{ index + 1 }}</span><div class="ranking-account"><strong>{{ item.account.name }}</strong><small>#{{ item.account_id }} · {{ item.poolName }} · {{ item.scheduleLabel }}</small></div><strong class="ranking-share">{{ item.share.toFixed(1) }}%</strong></div><p v-if="!accounts.length" class="quiet-empty">暂无本地托管账号</p></div>
    </section>
  </aside>
</template>

<style scoped>
.ranking-sidebar { position: fixed; right: 0; top: 150px; z-index: 14; }
.ranking-toggle { display: grid; place-items: center; width: 40px; height: 44px; padding: 0; border-radius: 12px 0 0 12px; border-right: 0; box-shadow: 0 4px 16px color-mix(in srgb, var(--text) 8%, transparent); }
.ranking-toggle:focus-visible { outline-offset: -3px; }
.is-open .ranking-toggle { color: var(--accent); border-color: var(--control-border); }
.ranking-panel { position: absolute; right: calc(100% - 1px); top: 0; width: min(285px, calc(100vw - 56px)); margin: 0; max-height: calc(100dvh - 180px); overflow: auto; box-shadow: 0 8px 28px color-mix(in srgb, var(--text) 8%, transparent); }
.panel-heading { display: flex; align-items: flex-start; flex-wrap: nowrap; padding: 18px 16px 14px; gap: 8px; }
.panel-heading > div { min-width: 0; flex: 1; }
.panel-heading h2 { font-size: 16px; }
.ranking-list { padding-inline: 16px; }
.ranking-account strong { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 600px) {
  .ranking-sidebar { top: 85px; }
  .ranking-panel { max-height: calc(100dvh - 180px); }
}
</style>
