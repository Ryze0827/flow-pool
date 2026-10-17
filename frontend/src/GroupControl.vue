<script setup>
import { computed, onMounted, ref } from 'vue'
import { RefreshCw, Zap } from 'lucide-vue-next'
import { api } from './api'

const MAX_SELECTION = 200
const emit = defineEmits(['notify'])
const groups = ref([])
const loading = ref(false)
const loadError = ref('')
const search = ref('')
const selected = ref([])
const submitting = ref(false)
const actionError = ref('')
const outcome = ref(null)
const pendingEnabled = ref(true)
const confirmation = ref(null)
const busy = computed(() => loading.value || submitting.value)
const forcedCount = computed(() => groups.value.filter(group => group.force_openai_fast === true).length)
const plainCount = computed(() => groups.value.filter(group => group.force_openai_fast === false).length)
const unknownCount = computed(() => groups.value.filter(group => group.force_openai_fast === null).length)
const visible = computed(() => {
  const query = search.value.trim().toLowerCase()
  return groups.value.filter(group => !query || `${group.name} ${group.id} ${group.status}`.toLowerCase().includes(query))
})
const allVisibleSelected = computed(() => visible.value.length > 0 && visible.value.every(group => selected.value.includes(group.id)))
const someVisibleSelected = computed(() => visible.value.some(group => selected.value.includes(group.id)))
const selectedGroups = computed(() => groups.value.filter(group => selected.value.includes(group.id)))
const resultById = computed(() => new Map((outcome.value?.results ?? []).map(result => [result.id, result])))
const canApply = computed(() => !busy.value && selected.value.length > 0 && selected.value.length <= MAX_SELECTION)
const statusLabel = status => ({ active: '正常', inactive: '已停用' })[status] || status || '未知'
const fastLabel = value => value === true ? '强制 Fast' : value === false ? '跟随全局' : '未知'
const fastTone = value => value === true ? 'success' : value === false ? 'neutral' : 'warning'

async function loadGroups() {
  if (loading.value) return
  loading.value = true
  loadError.value = ''
  try {
    const result = await api('/group-control/groups')
    groups.value = result.items
    const known = new Set(result.items.map(group => group.id))
    selected.value = selected.value.filter(id => known.has(id))
  } catch (cause) { loadError.value = cause.message }
  finally { loading.value = false }
}
function toggleGroup(id, checked) {
  if (checked && !selected.value.includes(id) && selected.value.length >= MAX_SELECTION) {
    actionError.value = `单次最多选择 ${MAX_SELECTION} 个分组`
    return
  }
  actionError.value = ''
  selected.value = checked ? [...selected.value.filter(value => value !== id), id] : selected.value.filter(value => value !== id)
}
function toggleVisible(event) {
  const ids = visible.value.map(group => group.id)
  if (event.target.checked) {
    const next = [...new Set([...selected.value, ...ids])]
    if (next.length > MAX_SELECTION) {
      event.target.checked = false
      actionError.value = `单次最多选择 ${MAX_SELECTION} 个分组，请缩小搜索范围后再全选`
      return
    }
    actionError.value = ''
    selected.value = next
  } else {
    selected.value = selected.value.filter(id => !ids.includes(id))
  }
}
function askApply(enabled) {
  if (!canApply.value) return
  pendingEnabled.value = enabled
  confirmation.value.showModal()
}
async function apply() {
  if (submitting.value) return
  const enabled = pendingEnabled.value
  const ids = [...selected.value]
  confirmation.value.close()
  submitting.value = true
  actionError.value = ''
  outcome.value = null
  try {
    outcome.value = await api('/group-control/openai-fast', 'POST', { group_ids: ids, enabled })
    emit('notify', `OpenAI Fast 设置已确认 ${outcome.value.succeeded} 个，未确认 ${outcome.value.failed} 个`, outcome.value.failed > 0)
  } catch (cause) { actionError.value = cause.message }
  finally {
    submitting.value = false
    selected.value = []
    await loadGroups()
  }
}
function resultText(id) {
  const result = resultById.value.get(id)
  if (!result) return '—'
  return result.verified ? '已确认' : result.error || '未确认'
}
onMounted(loadGroups)
</script>

<template>
  <div class="group-control">
    <section class="panel form-panel">
      <div class="card-title">
        <div><h2>分组调控</h2><p>统一设置 GPT / OpenAI 分组的 OpenAI Fast 强制 priority。开启后，该分组的请求会强制使用 priority（Fast），覆盖全局 Fast / Flex 策略。</p></div>
        <button class="button" :disabled="busy" @click="loadGroups"><RefreshCw :size="16" :class="{ spinning: loading }"/>刷新分组</button>
      </div>
      <p v-if="loadError" class="notice error" role="alert">{{ loadError }} <button class="text-button" @click="loadGroups">重试</button></p>
      <p v-else-if="loading && !groups.length" class="muted" role="status">正在读取 GPT 分组…</p>
      <div v-if="groups.length" class="group-summary">
        <span>GPT 分组 <b>{{ groups.length }}</b></span>
        <span>已强制 Fast <b>{{ forcedCount }}</b></span>
        <span>跟随全局 <b>{{ plainCount }}</b></span>
        <span v-if="unknownCount">状态未知 <b>{{ unknownCount }}</b></span>
      </div>
    </section>

    <section class="panel form-panel">
      <div class="group-toolbar">
        <input v-model="search" type="search" placeholder="搜索分组名称、ID 或状态" aria-label="搜索 GPT 分组"/>
        <span class="small muted" role="status">已选 <b>{{ selected.length }}</b> 个 · 单次最多 {{ MAX_SELECTION }} 个</span>
        <div class="form-actions">
          <button class="button primary" :disabled="!canApply" @click="askApply(true)"><Zap :size="16"/>设为强制 Fast</button>
          <button class="button" :disabled="!canApply" @click="askApply(false)">取消强制 Fast</button>
        </div>
      </div>
      <p class="small muted">写入后会从 Sub2API 重新读取实际值再确认。若 Sub2API 处于简易模式，它会忽略该设置，对应分组将显示为未确认。</p>
      <p v-if="actionError" class="notice error" role="alert">{{ actionError }}</p>
      <p v-if="submitting" class="notice" role="status">正在写入所选分组，请勿关闭页面…</p>
      <div v-if="outcome" class="notice" role="status">
        已确认 {{ outcome.succeeded }} 个，未确认 {{ outcome.failed }} 个（目标：{{ outcome.enabled ? '强制 Fast' : '跟随全局' }}）
      </div>
      <fieldset v-if="visible.length" class="group-selection" :disabled="busy">
        <legend>选择分组（可多选）</legend>
        <label class="group-select-all">
          <input type="checkbox" aria-label="全选当前筛选结果" :checked="allVisibleSelected" :indeterminate="someVisibleSelected && !allVisibleSelected" @change="toggleVisible"/>
          <span>全选当前筛选结果</span>
        </label>
        <div class="group-checkbox-grid">
          <label v-for="group in visible" :key="group.id" :class="['group-checkbox', { selected: selected.includes(group.id) }]">
            <input type="checkbox" :checked="selected.includes(group.id)" :disabled="!selected.includes(group.id) && selected.length >= MAX_SELECTION" :aria-label="group.name" @change="toggleGroup(group.id, $event.target.checked)"/>
            <span class="group-checkbox-content">
              <span class="group-checkbox-heading"><strong>{{ group.name }}</strong><small>#{{ group.id }}</small></span>
              <span class="group-checkbox-meta"><span :class="['badge', group.status === 'active' ? 'success' : 'neutral']">{{ statusLabel(group.status) }}</span><span :class="['badge', fastTone(group.force_openai_fast)]">{{ fastLabel(group.force_openai_fast) }}</span><span v-if="resultById.has(group.id)" class="group-result">{{ resultText(group.id) }}</span></span>
            </span>
          </label>
        </div>
      </fieldset>
      <p v-else-if="groups.length" class="muted">没有匹配的分组。</p>
      <p v-else-if="!loading && !loadError" class="muted">没有 GPT / OpenAI 分组，请先在 Sub2API 中创建分组。</p>
    </section>

    <dialog ref="confirmation" class="group-confirm" aria-labelledby="group-confirm-title">
      <form @submit.prevent="apply">
        <h2 id="group-confirm-title">{{ pendingEnabled ? '设为强制 Fast' : '取消强制 Fast' }}</h2>
        <p>{{ pendingEnabled ? '所选分组的 OpenAI 请求将强制使用 priority（Fast）模式，覆盖全局 Fast / Flex 策略。' : '所选分组将恢复为跟随全局 Fast / Flex 策略。' }}</p>
        <div class="group-confirm-list">
          <p v-for="group in selectedGroups" :key="group.id"><strong>{{ group.name }}</strong> <small>#{{ group.id }}</small><br/>{{ fastLabel(group.force_openai_fast) }} → {{ pendingEnabled ? '强制 Fast' : '跟随全局' }}</p>
        </div>
        <div class="form-actions">
          <button type="button" class="button" @click="confirmation.close()">取消</button>
          <button class="button primary">确认{{ pendingEnabled ? '设为强制 Fast' : '取消强制 Fast' }}（{{ selectedGroups.length }}）</button>
        </div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.group-control { display: grid; gap: 20px; }
.group-summary { display: flex; gap: 24px; flex-wrap: wrap; margin-top: 16px; }
.group-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 16px; }
.group-toolbar input[type='search'] { min-width: 220px; flex: 1; }
.group-toolbar .form-actions { margin-left: auto; }
.group-selection { border: 0; padding: 20px 0 0; margin: 0; min-width: 0; }
.group-selection legend { padding: 0; font-size: 14px; font-weight: 600; }
.group-select-all { display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; color: var(--muted); font-size: 13px; cursor: pointer; }
.group-select-all input { margin: 0; }
.group-checkbox-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; margin-top: 12px; max-height: 440px; overflow: auto; padding: 2px; }
.group-checkbox { display: flex; align-items: flex-start; gap: 10px; min-width: 0; padding: 12px; border: 1px solid var(--border); border-radius: 9px; background: #fff; cursor: pointer; transition: border-color .15s, background .15s, box-shadow .15s; }
.group-checkbox:hover { border-color: #b7c8d9; background: #fbfcfe; }
.group-checkbox.selected { border-color: #9fbfd0; background: #f4f9fb; box-shadow: 0 2px 7px #2946600b; }
.group-checkbox input { margin: 3px 0 0; }
.group-checkbox-content { min-width: 0; flex: 1; }
.group-checkbox-heading { display: flex; align-items: baseline; gap: 7px; min-width: 0; }
.group-checkbox-heading strong { min-width: 0; overflow-wrap: anywhere; font-size: 14px; font-weight: 550; }
.group-checkbox-heading small { flex-shrink: 0; color: var(--muted); }
.group-checkbox-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.group-result { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.group-selection:disabled .group-checkbox { cursor: wait; }
.group-selection:disabled .group-checkbox:hover { border-color: var(--border); background: #fff; }
.group-confirm { width: min(640px, calc(100vw - 32px)); border: 0; border-radius: 16px; padding: 28px; }
.group-confirm::backdrop { background: #11182766; }
.group-confirm-list { max-height: 45vh; overflow: auto; margin: 16px 0; }
.group-confirm-list small { color: var(--muted); }
</style>
