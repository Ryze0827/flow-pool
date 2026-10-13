<script setup>
import { computed, ref, watch } from 'vue'
import { Minus, Plus } from 'lucide-vue-next'
import { api } from './api'

const props = defineProps({ account: Object, busy: Boolean })
const emit = defineEmits(['updated', 'notify'])
const priority = computed(() => props.account.remote.priority)
const draft = ref(priority.value ?? '')
const editing = ref(false)
const saving = ref(false)
const disabled = computed(() => props.busy || saving.value)
watch(priority, value => { if (!editing.value && !saving.value) draft.value = value ?? '' })

async function save(value = draft.value) {
  if (disabled.value) return
  const number = value === '' ? NaN : Number(value)
  if (!Number.isInteger(number) || number < 0 || number > 10000) {
    draft.value = priority.value ?? ''
    emit('notify', '优先级须为 0–10000 的整数', true)
    return
  }
  if (number === priority.value) { draft.value = number; return }
  saving.value = true
  draft.value = number
  try {
    const result = await api(`/accounts/${props.account.id}/priority`, 'PUT', { priority: number })
    emit('updated', result)
    draft.value = result.priority
  } catch (error) {
    draft.value = priority.value ?? ''
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
  <form class="priority-control" :aria-busy="saving" @submit.prevent="save()">
    <button type="button" :disabled="disabled || draft === '' || Number(draft) <= 0" :aria-label="`降低账号 ${account.id} 优先级数值`" title="减 1" @pointerdown.prevent @click="step(-1)"><Minus :size="14"/></button>
    <input v-model="draft" type="number" min="0" max="10000" step="1" inputmode="numeric" :disabled="disabled" :aria-label="`账号 ${account.id} 优先级`" title="输入优先级，回车或离开输入框保存" placeholder="—" @focus="editing = true" @blur="finishEditing" @keydown.esc.prevent="draft = priority ?? ''; $event.target.blur()"/>
    <button type="button" :disabled="disabled || draft === '' || Number(draft) >= 10000" :aria-label="`提高账号 ${account.id} 优先级数值`" title="加 1" @pointerdown.prevent @click="step(1)"><Plus :size="14"/></button>
  </form>
</template>

<style scoped>
.priority-control { display: inline-flex; align-items: center; width: 110px; height: 34px; border: 1px solid var(--border); border-radius: 9px; background: #f8fafc; overflow: hidden; }
.priority-control:focus-within { border-color: #73afc3; box-shadow: 0 0 0 3px #5baec212; }
.priority-control button { display: grid; place-items: center; flex: 0 0 28px; height: 100%; border: 0; padding: 0; background: transparent; color: var(--muted); }
.priority-control button:hover:not(:disabled) { background: #edf1f6; color: var(--accent); }
.priority-control button:disabled { color: #bdc6d2; }
.priority-control input { width: 0; min-width: 0; flex: 1; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; text-align: center; font-size: 14px; font-variant-numeric: tabular-nums; appearance: textfield; -moz-appearance: textfield; }
.priority-control input::-webkit-inner-spin-button, .priority-control input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.priority-control[aria-busy=true] { opacity: .65; }
@media(max-width:600px) { .priority-control { height: 40px; } .priority-control input { font-size: 16px; } }
</style>
