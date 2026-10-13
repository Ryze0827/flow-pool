<script setup>
import { computed, ref, watch } from 'vue'
import { Minus, Plus } from 'lucide-vue-next'
import { api } from './api'

const props = defineProps({ account: Object, busy: Boolean })
const emit = defineEmits(['updated', 'notify'])
const concurrency = computed(() => props.account.remote.concurrency)
const draft = ref(concurrency.value ?? '')
const editing = ref(false)
const saving = ref(false)
const disabled = computed(() => props.busy || saving.value)
watch(concurrency, value => { if (!editing.value && !saving.value) draft.value = value ?? '' })

async function save(value = draft.value) {
  if (disabled.value) return
  const number = value === '' ? NaN : Number(value)
  if (!Number.isInteger(number) || number < 1 || number > 1000) {
    draft.value = concurrency.value ?? ''
    emit('notify', '并发须为 1–1000 的整数', true)
    return
  }
  if (number === concurrency.value) { draft.value = number; return }
  saving.value = true
  draft.value = number
  try {
    const result = await api(`/accounts/${props.account.id}/concurrency`, 'PUT', { concurrency: number })
    emit('updated', result)
    draft.value = result.concurrency
  } catch (error) {
    draft.value = concurrency.value ?? ''
    emit('notify', error.message, true)
  } finally {
    saving.value = false
  }
}
function step(delta) {
  const value = draft.value === '' ? NaN : Number(draft.value)
  if (Number.isInteger(value)) void save(value + delta)
}
function finishEditing() {
  editing.value = false
  void save()
}
</script>

<template>
  <form class="concurrency-control" :aria-busy="saving" @submit.prevent="save()">
    <button type="button" :disabled="disabled || draft === '' || Number(draft) <= 1" :aria-label="`降低账号 ${account.id} 并发数值`" title="减 1" @pointerdown.prevent @click="step(-1)"><Minus :size="14"/></button>
    <input v-model="draft" type="number" min="1" max="1000" step="1" inputmode="numeric" :disabled="disabled" :aria-label="`账号 ${account.id} 并发`" title="输入并发，回车或离开输入框保存" placeholder="—" @focus="editing = true" @blur="finishEditing" @keydown.esc.prevent="draft = concurrency ?? ''; $event.target.blur()"/>
    <button type="button" :disabled="disabled || draft === '' || Number(draft) >= 1000" :aria-label="`提高账号 ${account.id} 并发数值`" title="加 1" @pointerdown.prevent @click="step(1)"><Plus :size="14"/></button>
  </form>
</template>

<style scoped>
.concurrency-control { display: inline-flex; align-items: center; width: 90px; height: 34px; border: 1px solid var(--border); border-radius: 9px; background: #f8fafc; overflow: hidden; }
.concurrency-control:focus-within { border-color: #73afc3; box-shadow: 0 0 0 3px #5baec212; }
.concurrency-control button { display: grid; place-items: center; flex: 0 0 22px; height: 100%; border: 0; padding: 0; background: transparent; color: var(--muted); }
.concurrency-control button:hover:not(:disabled) { background: #edf1f6; color: var(--accent); }
.concurrency-control button:disabled { color: #bdc6d2; }
.concurrency-control input { width: 0; min-width: 0; flex: 1; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; text-align: center; font-size: 14px; font-variant-numeric: tabular-nums; appearance: textfield; -moz-appearance: textfield; }
.concurrency-control input::-webkit-inner-spin-button, .concurrency-control input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.concurrency-control[aria-busy=true] { opacity: .65; }
@media(max-width:600px) { .concurrency-control { height: 40px; } .concurrency-control input { font-size: 16px; } }
</style>
