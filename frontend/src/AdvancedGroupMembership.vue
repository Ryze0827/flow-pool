<script setup>
import { computed, ref } from 'vue'
import { ArrowDownToLine, ArrowUpToLine, RefreshCw, Settings2, X } from 'lucide-vue-next'
import { api } from './api'

const props = defineProps({ pools: Array, accounts: Array, busy: Boolean, configured: Boolean })
const emit = defineEmits(['loading', 'updated', 'notify'])
const loaded = ref(false)
const loading = ref(false)
const working = ref(false)
const groups = ref([])
const saved = ref([])
const draft = ref([])
const selectedPools = ref([])
const search = ref('')
const error = ref('')
const outcome = ref(null)
const pending = ref(null)
const confirmation = ref(null)
const expanded = ref(false)
const advancedDialog = ref(null)
const disabled = computed(() => props.busy || working.value || loading.value)
const sameIds = (left, right) => [...left].sort((a, b) => a - b).join(',') === [...right].sort((a, b) => a - b).join(',')
const dirty = computed(() => !sameIds(draft.value, saved.value))
const selectedAccounts = computed(() => props.accounts.filter(account => selectedPools.value.includes(account.pool)))
const options = computed(() => [...groups.value, ...saved.value.filter(id => !groups.value.some(group => group.id === id))
  .map(id => ({ id, name: `分组 #${id}（已删除或不可用）`, status: 'missing' }))])
const visibleGroups = computed(() => options.value.filter(group => `${group.name} ${group.id}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const selectedGroups = computed(() => options.value.filter(group => saved.value.includes(group.id)))
const canApply = computed(() => !disabled.value && loaded.value && !dirty.value && saved.value.length > 0 && selectedAccounts.value.length > 0 && selectedAccounts.value.length <= 10000)
const canAdd = computed(() => canApply.value && selectedGroups.value.every(group => group.status === 'active'))
const poolName = id => props.pools.find(pool => pool.id === id)?.name || id
const statusLabel = status => ({ changed: '已更新', unchanged: '无需修改', skipped: '已跳过', failed: '失败 / 未确认' })[status]

function setWorking(value) { working.value = value; emit('loading', value) }
function acceptConfig(config) {
  groups.value = config.groups
  saved.value = [...config.group_ids]
  draft.value = [...config.group_ids]
  loaded.value = true
}
async function load() {
  if (disabled.value || !props.configured) return
  loading.value = true
  error.value = ''
  try { acceptConfig(await api('/account-groups/config')) }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}
function toggleExpanded() {
  if (working.value) return
  if (expanded.value) {
    advancedDialog.value?.close()
    return
  }
  expanded.value = true
  if (!loaded.value) void load()
  advancedDialog.value?.showModal()
}
function closePanel() {
  expanded.value = false
}
async function save() {
  if (disabled.value || !dirty.value) return
  setWorking(true)
  error.value = ''
  try {
    acceptConfig(await api('/account-groups/config', 'PUT', { group_ids: draft.value }))
    emit('notify', '高级分组配置已保存')
  } catch (cause) { error.value = cause.message }
  finally { setWorking(false) }
}
async function prepare(operation) {
  if (!(operation === 'add' ? canAdd.value : canApply.value)) return
  setWorking(true)
  error.value = ''
  try {
    const [config, dashboard] = await Promise.all([api('/account-groups/config'), api('/dashboard')])
    if (!sameIds(config.group_ids, saved.value)) {
      acceptConfig(config)
      throw new Error('高级分组配置已变化，已刷新配置，请重新选择操作')
    }
    acceptConfig(config)
    if (operation === 'add' && selectedGroups.value.some(group => group.status !== 'active')) throw new Error('高级分组包含停用或已删除分组，请调整配置后再移入')
    const accounts = dashboard.accounts.filter(account => selectedPools.value.includes(account.pool))
    if (!accounts.length || accounts.length > 10000) throw new Error('请选择包含 1–10000 个托管账号的号池')
    pending.value = { operation, pools: [...selectedPools.value], group_ids: [...saved.value], account_ids: accounts.map(account => account.id),
      accounts, groups: [...selectedGroups.value] }
    confirmation.value.showModal()
  } catch (cause) { error.value = cause.message }
  finally { setWorking(false) }
}
async function apply() {
  if (disabled.value || !pending.value) return
  const { operation, pools, group_ids, account_ids } = pending.value
  setWorking(true)
  error.value = ''
  outcome.value = null
  try {
    outcome.value = await api('/account-groups/batch', 'POST', { operation, pools, group_ids, account_ids })
    const result = outcome.value
    emit('notify', `高级分组：更新 ${result.changed} 个，无需修改 ${result.unchanged} 个，跳过 ${result.skipped} 个，失败或未确认 ${result.failed} 个`, result.failed + result.skipped > 0)
    emit('updated')
  } catch (cause) {
    error.value = cause.message
    emit('updated')
  } finally {
    confirmation.value.close()
    setWorking(false)
  }
}
</script>

<template>
  <div class="advanced-groups">
    <button type="button" class="button advanced-toggle" :aria-expanded="expanded" :disabled="working" @click="toggleExpanded"><Settings2 :size="15"/>高级分组操作</button>
    <dialog ref="advancedDialog" class="modal advanced-dialog" aria-labelledby="advanced-groups-title" @cancel="closePanel" @close="closePanel">
      <div class="modal-heading"><div><h2 id="advanced-groups-title">高级分组批量操作</h2><p>多选 FlowPool 号池</p></div><button type="button" class="icon-button" :disabled="working" aria-label="关闭高级分组操作" @click="advancedDialog.close()"><X :size="18"/></button></div>
      <div v-if="expanded" class="advanced-body">
      <p v-if="!configured" class="muted small">请先配置 Sub2API 连接。</p>
      <template v-else>
        <div class="advanced-toolbar"><p class="small muted">作用于所选号池的全部托管账号，不受列表搜索和状态筛选影响。</p><button type="button" class="button" :disabled="disabled || dirty" @click="load"><RefreshCw :size="14"/>刷新分组</button></div>
        <p v-if="loading" class="small muted" role="status">正在读取 Sub2API 分组…</p>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        <template v-if="loaded">
          <details class="advanced-config" :open="!saved.length">
            <summary>配置 Sub2API 高级分组 <span class="muted small">已保存 {{ saved.length }} 个</span></summary>
            <p class="small muted">勾选作为“高级分组”的 GPT / OpenAI 分组。保存配置不会修改账号归属。</p>
            <input v-model="search" type="search" aria-label="搜索高级分组" placeholder="搜索分组名称或 ID"/>
            <div class="advanced-group-options">
              <label v-for="group in visibleGroups" :key="group.id" class="check-row"><input v-model="draft" type="checkbox" :value="group.id" :disabled="disabled || (!draft.includes(group.id) && draft.length >= 200)"/><span>{{ group.name }} <small class="muted">#{{ group.id }}{{ group.status === 'active' ? '' : ' · 不可移入' }}</small></span></label>
              <p v-if="!visibleGroups.length" class="small muted">暂无匹配分组</p>
            </div>
            <div class="advanced-toolbar"><span class="small muted">已选 {{ draft.length }} / 200 个</span><button type="button" class="button" :disabled="disabled || !dirty" @click="save">保存高级分组配置</button></div>
          </details>
          <p v-if="dirty" class="small orange-text">配置尚未保存，请保存后再执行批量操作。<button type="button" class="text-button" :disabled="disabled" @click="draft = [...saved]">撤销修改</button></p>
          <fieldset class="advanced-pools" :disabled="disabled"><legend>选择 FlowPool 号池（可多选）</legend><label v-for="pool in pools" :key="pool.id" class="check-row"><input v-model="selectedPools" type="checkbox" :value="pool.id"/>{{ pool.name }} <small class="muted">{{ accounts.filter(account => account.pool === pool.id).length }} 个</small></label></fieldset>
          <div class="advanced-toolbar"><span class="small muted">已选 {{ selectedPools.length }} 个号池 · {{ selectedAccounts.length }} 个账号 · {{ saved.length }} 个高级分组</span><div class="advanced-actions"><button type="button" class="button" :disabled="!canApply" @click="prepare('remove')"><ArrowUpToLine :size="15"/>一键移出高级分组</button><button type="button" class="button primary" :disabled="!canAdd" @click="prepare('add')"><ArrowDownToLine :size="15"/>一键移入高级分组</button></div></div>
          <p class="small muted">移入会追加所配置的高级分组，移出只解除这些分组的关联。账号其它分组、调度开关及 FlowPool 归属保持不变。状态切换或仅生图冷却中的账号可能跳过；后续冷却恢复等操作仍按现有规则调整分组。</p>
        </template>
        <div v-if="outcome" class="advanced-outcome">
          <p role="status">{{ outcome.operation === 'add' ? '移入' : '移出' }}结果：更新 {{ outcome.changed }} · 无需修改 {{ outcome.unchanged }} · 跳过 {{ outcome.skipped }} · 失败 / 未确认 {{ outcome.failed }}</p>
          <div class="advanced-results"><div v-for="item in outcome.results" :key="item.id" class="advanced-result"><div><strong>{{ item.name }}</strong><small>#{{ item.id }} · {{ poolName(item.pool) }}</small></div><span :class="['badge', ['changed', 'unchanged'].includes(item.status) ? 'success' : 'warning']">{{ statusLabel(item.status) }}</span><p v-if="item.error">{{ item.error }}</p></div></div>
        </div>
      </template>
      </div>
    </dialog>
    <dialog ref="confirmation" class="modal advanced-confirm" aria-labelledby="advanced-confirm-title" @cancel="working && $event.preventDefault()">
      <template v-if="pending">
        <div class="modal-heading"><h2 id="advanced-confirm-title">确认{{ pending.operation === 'add' ? '移入' : '移出' }}高级分组</h2><button type="button" class="icon-button" :disabled="working" aria-label="关闭高级分组确认" @click="confirmation.close()"><X :size="18"/></button></div>
        <div class="modal-body">
          <p>FlowPool 号池：{{ pending.pools.map(poolName).join('、') }}</p>
          <p>Sub2API 高级分组：{{ pending.groups.map(group => `${group.name}（#${group.id}）`).join('、') }}</p>
          <p>将对以下 {{ pending.accounts.length }} 个账号{{ pending.operation === 'add' ? '追加' : '解除' }}上述高级分组关联。</p>
          <div class="advanced-preview"><div v-for="account in pending.accounts" :key="account.id">{{ account.name }} <small class="muted">#{{ account.id }} · {{ poolName(account.pool) }}</small></div></div>
          <p class="small muted">{{ pending.operation === 'remove' ? '若账号仅属于高级分组，移出后将没有关联分组。' : '' }}不会主动开启或停止账号调度。状态切换及仅生图冷却保护中的账号会单独反馈。</p>
          <p v-if="working" role="status" class="notice">正在逐批更新并核对结果，请等待完成…</p>
        </div>
        <div class="modal-footer"><button type="button" class="button" :disabled="working" @click="confirmation.close()">取消</button><button type="button" class="button primary" :disabled="disabled" @click="apply">{{ working ? '处理中…' : `确认${pending.operation === 'add' ? '移入' : '移出'} ${pending.accounts.length} 个账号` }}</button></div>
      </template>
    </dialog>
  </div>
</template>

<style scoped>
.advanced-groups { flex: 0 0 auto; }
.advanced-toggle { min-height: 38px; }
.advanced-toggle:disabled { cursor: wait; }
.advanced-dialog { margin: auto; padding: 0; color: var(--text); width: min(780px, calc(100vw - 24px)); max-height: 90dvh; overflow-y: auto; }
.advanced-dialog:not([open]) { display: none; }
.advanced-dialog::backdrop { background: #21304a3b; }
.advanced-dialog .modal-heading { position: sticky; top: 0; z-index: 1; background: #fff; }
.advanced-body { padding: 0 28px 24px; }
.advanced-toolbar,.advanced-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.advanced-toolbar { margin: 12px 0; }
.advanced-toolbar p { margin: 0; }
.advanced-config { border-block: 1px solid var(--border); padding: 12px 0; margin: 12px 0; }
.advanced-config summary { cursor: pointer; font-size: 14px; }
.advanced-config input[type=search] { width: 100%; margin-top: 10px; }
.advanced-group-options { max-height: 200px; overflow: auto; padding: 8px 2px; }
.advanced-group-options .check-row { padding: 7px 0; overflow-wrap: anywhere; }
.advanced-pools { margin: 14px 0; border: 0; padding: 0; display: flex; gap: 16px; flex-wrap: wrap; }
.advanced-pools legend { margin-bottom: 10px; font-size: 14px; font-weight: 600; }
.advanced-pools label { min-height: 36px; font-size: 14px; }
.advanced-pools input { margin: 0; }
.advanced-outcome > p { font-size: 14px; }
.advanced-results { max-height: 280px; overflow: auto; margin-top: 10px; }
.advanced-result { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 10px; border-bottom: 1px solid var(--border); background: #fff; }
.advanced-result > div { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.advanced-result strong { font-size: 13px; }
.advanced-result small { display: block; color: var(--muted); margin-top: 4px; }
.advanced-result p { flex-basis: 100%; margin: 0; font-size: 13px; color: var(--warning); overflow-wrap: anywhere; }
.advanced-confirm { margin: auto; padding: 0; color: var(--text); width: min(620px, calc(100vw - 24px)); max-height: 90dvh; overflow-y: auto; }
.advanced-confirm:not([open]) { display: none; }
.advanced-confirm::backdrop { background: #21304a3b; }
.advanced-confirm .modal-body { padding: 0 24px 20px; overflow-wrap: anywhere; }
.advanced-preview { max-height: 200px; overflow: auto; margin-top: 12px; padding: 10px; background: #f8fafc; border-radius: 8px; font-size: 13px; }
.advanced-preview > div { padding: 5px 0; }
@media(max-width:600px) {
  .advanced-groups { margin-inline: 0; }
  .advanced-pools { gap: 8px 16px; }
  .advanced-actions { width: 100%; }
  .advanced-actions .button { flex: 1; white-space: normal; min-height: 44px; }
  .advanced-confirm .modal-body { padding-inline: 16px; }
}
</style>
