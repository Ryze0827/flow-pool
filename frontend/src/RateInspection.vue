<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Check, Plus, RefreshCw, Search, Trash2, X } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const rules = ref(null)
const draftRules = ref(null)
const savingRules = ref(false)
const rulesError = ref('')
const groups = ref([])
const defaultGroupIds = ref([])
const groupsLoading = ref(false)
const groupsError = ref('')
const groupSearch = ref('')
const chosenGroupIds = computed(() => draftRules.value?.group_ids ?? defaultGroupIds.value)
const matchingGroups = computed(() => groups.value.filter(group => `${group.name} ${group.id} ${group.platform}`.toLowerCase().includes(groupSearch.value.trim().toLowerCase())))
const missingGroupIds = computed(() => chosenGroupIds.value.filter(id => !groups.value.some(group => group.id === id)))
const savedGroupNames = computed(() => (rules.value?.group_ids ?? defaultGroupIds.value).map(id => groups.value.find(group => group.id === id)?.name || `#${id}（不可用）`).join('、'))
const rulesDirty = computed(() => JSON.stringify(draftRules.value) !== JSON.stringify(rules.value))
const lowestMinimum = computed(() => rules.value?.tiers.length ? Math.min(...rules.value.tiers.map(tier => tier.minimum)) : null)
const clone = value => JSON.parse(JSON.stringify(value))
async function loadRules() {
  rulesError.value = ''
  try {
    rules.value = await api('/rate-inspection/rules')
    draftRules.value = clone(rules.value)
  } catch (cause) { rulesError.value = cause.message }
}
async function loadGroups() {
  groupsLoading.value = true; groupsError.value = ''
  try {
    const result = await api('/rate-inspection/groups')
    groups.value = result.items
    defaultGroupIds.value = result.default_group_ids
  } catch (cause) { groupsError.value = cause.message }
  finally { groupsLoading.value = false }
}
function toggleGroup(id, checked) {
  const ids = chosenGroupIds.value.filter(value => value !== id)
  draftRules.value.group_ids = (checked ? [...ids, id] : ids).sort((a, b) => a - b)
}
async function saveRules() {
  if (savingRules.value || busy.value || correcting.value || groupsLoading.value || groupsError.value || !chosenGroupIds.value.length || missingGroupIds.value.length) return
  savingRules.value = true; rulesError.value = ''
  try {
    rules.value = await api('/rate-inspection/rules', 'PUT', { ...draftRules.value, group_ids: chosenGroupIds.value })
    draftRules.value = clone(rules.value)
    result.value = null; selected.value = []; correction.value = null
    emit('notify', '充值档位规则已保存，下一次巡检立即生效')
  } catch (cause) { rulesError.value = cause.message }
  finally { savingRules.value = false }
}
function acceptInspection(value) {
  result.value = value
  // The server returns the exact rules used, including changes made in another tab.
  rules.value = value.rules
  draftRules.value = clone(value.rules)
}
onMounted(() => { void loadRules(); void loadGroups() })
const emails = ref('')
const result = ref(null)
const selected = ref([])
const busy = ref(false)
const correcting = ref(false)
const error = ref('')
const correction = ref(null)
const confirmation = ref(null)
const page = ref(1)
let controller = null
const rows = computed(() => result.value?.rows || [])
const visible = computed(() => rows.value.slice((page.value - 1) * 50, page.value * 50))
const pages = computed(() => Math.max(1, Math.ceil(rows.value.length / 50)))
const allSelected = computed(() => rows.value.length > 0 && selected.value.length === rows.value.length)
const selectedRows = computed(() => rows.value.filter(row => selected.value.includes(row.id)))
const format = value => value === null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 8 }).format(value)

async function inspect() {
  if (busy.value || correcting.value || savingRules.value || groupsLoading.value || groupsError.value || rulesDirty.value || !rules.value || !chosenGroupIds.value.length || missingGroupIds.value.length) return
  busy.value = true
  error.value = ''
  result.value = null
  correction.value = null
  selected.value = []
  page.value = 1
  const current = new AbortController()
  controller = current
  try { acceptInspection(await api('/rate-inspection', 'POST', { emails: emails.value }, { signal: current.signal })) }
  catch (cause) { if (!current.signal.aborted) error.value = cause.message }
  finally { if (controller === current) { busy.value = false; controller = null } }
}
function cancel() {
  controller?.abort()
  controller = null
  busy.value = false
  error.value = '巡检已取消，请重新开始以获取完整结果。'
}
function selectAll(event) { selected.value = event.target.checked ? rows.value.map(row => row.id) : [] }
async function correct() {
  if (correcting.value || savingRules.value || rulesDirty.value || !selected.value.length) return
  confirmation.value.close()
  correcting.value = true
  error.value = ''
  const inspectionId = result.value.inspection_id
  // Unknown write outcomes must never reuse the same snapshot.
  result.value.inspection_id = null
  try {
    correction.value = await api('/rate-inspection/correct', 'POST', { inspection_id: inspectionId, row_ids: selected.value })
    emit('notify', `确认纠正 ${correction.value.succeeded} 条，未确认 ${correction.value.failed} 条`, correction.value.failed > 0)
    acceptInspection(await api('/rate-inspection', 'POST', { emails: emails.value }))
    page.value = 1
  } catch (cause) { error.value = cause.message }
  finally {
    correcting.value = false
    selected.value = []
  }
}
onUnmounted(() => controller?.abort())
</script>

<template>
  <div class="rate-inspection">
    <section class="panel form-panel">
      <div class="card-title"><div><h2>充值档位规则</h2><p>按历史累计正向充值匹配最高档位，包含管理员加款，扣款不冲减累计值。</p></div></div>
      <p class="muted">仅展示和检查 GPT / OpenAI 分组，尊重用户分组访问权限。<template v-if="rules">当前已保存规则：未满 {{ format(lowestMinimum) }} 美元不调整倍率。</template></p>
      <p v-if="rulesError" class="notice error" role="alert">{{ rulesError }} <button v-if="!rules" class="text-button" @click="loadRules">重试</button></p>
      <p v-if="!rules && !rulesError" class="muted">正在读取充值档位…</p>
      <form v-if="draftRules" @submit.prevent="saveRules">
        <fieldset class="group-picker" :disabled="busy || correcting || savingRules || groupsLoading">
          <legend>适用分组 · 已选 {{ chosenGroupIds.length }} 个</legend>
          <div class="group-tools"><input v-model="groupSearch" type="search" placeholder="搜索 GPT 分组名称或 ID" aria-label="搜索适用分组"/><button class="button" type="button" @click="loadGroups"><RefreshCw :size="15"/>刷新分组</button></div>
          <p v-if="groupsLoading" class="small muted" role="status">正在读取分组…</p>
          <p v-if="groupsError" class="notice error" role="alert">{{ groupsError }}</p>
          <div class="group-options">
            <label v-for="group in matchingGroups" :key="group.id" class="group-option"><input type="checkbox" :checked="chosenGroupIds.includes(group.id)" :disabled="!chosenGroupIds.includes(group.id) && chosenGroupIds.length >= 200" @change="toggleGroup(group.id, $event.target.checked)"/><span>{{ group.name }}<small>#{{ group.id }}<template v-if="group.platform"> · {{ group.platform }}</template></small></span></label>
          </div>
          <p v-if="!groupsLoading && !groupsError && !matchingGroups.length" class="small muted">{{ groups.length ? '没有匹配的分组。' : '没有可用分组，请先在 Sub2API 中创建或启用分组。' }}</p>
          <p v-for="id in missingGroupIds" :key="id" class="notice error">分组 #{{ id }} 已停用、不存在或非 GPT 分组。<button type="button" class="text-button" @click="toggleGroup(id, false)">移除选择</button></p>
          <p class="small muted">至少选择 1 个分组，最多 200 个；所选分组共用下方充值档位。</p>
          <p v-if="!groupsLoading && !groupsError" class="small muted">{{ rules.group_ids === null ? '默认适用分组' : '已保存适用分组' }}：{{ savedGroupNames || '未选择' }}</p>
        </fieldset>
        <div class="tier-editor">
          <div v-for="(tier, index) in draftRules.tiers" :key="index" class="tier-row">
            <label>累计充值门槛（USD）<input v-model.number="tier.minimum" type="number" min="0" max="1000000000000" step="any" required :disabled="busy || correcting || savingRules" :aria-label="`第 ${index + 1} 档充值门槛`"/></label>
            <label>用户倍率<input v-model.number="tier.rate" type="number" min="0.0001" max="1000" step="0.0001" required :disabled="busy || correcting || savingRules" :aria-label="`第 ${index + 1} 档倍率`"/></label>
            <button type="button" class="button" :disabled="busy || correcting || savingRules || draftRules.tiers.length <= 1" :aria-label="`删除第 ${index + 1} 档`" @click="draftRules.tiers.splice(index, 1)"><Trash2 :size="16"/>删除</button>
          </div>
        </div>
        <p class="small muted">门槛不能重复，倍率最多 4 位小数；匹配已达到的最高门槛。编辑后请先保存，已有巡检结果需重新检查。</p>
        <div class="form-actions">
          <button type="button" class="button" :disabled="busy || correcting || savingRules || draftRules.tiers.length >= 50" @click="draftRules.tiers.push({ minimum: '', rate: '' })"><Plus :size="16"/>新增档位</button>
          <button class="button primary" :disabled="busy || correcting || savingRules || groupsLoading || !!groupsError || !chosenGroupIds.length || !!missingGroupIds.length || !rulesDirty"><Check :size="16"/>{{ savingRules ? '正在保存…' : '保存档位规则' }}</button>
          <button type="button" class="button" :disabled="busy || correcting || savingRules || !rulesDirty" @click="draftRules = clone(rules)">撤销未保存修改</button>
        </div>
        <p v-if="rulesDirty" class="notice">档位或分组有未保存修改，请保存或撤销后再巡检和纠正。</p>
      </form>
    </section>
    <section class="panel form-panel">
      <div class="card-title"><div><h2>用户倍率巡检</h2><p>留空巡检全部用户，也可填写指定邮箱。</p></div></div>
      <label>巡检用户<textarea v-model="emails" rows="3" :disabled="busy || correcting" placeholder="支持换行、逗号、分号或空格分隔"/></label>
      <div class="form-actions">
        <button class="button primary" :disabled="busy || correcting || savingRules || groupsLoading || !!groupsError || rulesDirty || !rules || !chosenGroupIds.length || !!missingGroupIds.length" @click="inspect"><Search :size="16"/>{{ busy ? '正在巡检…' : '开始巡检' }}</button>
        <button v-if="busy" class="button" @click="cancel"><X :size="16"/>取消巡检</button>
        <button class="button" :disabled="busy || correcting || savingRules || rulesDirty || !selected.length || !result?.inspection_id || selected.length > 10000" @click="confirmation.showModal()"><Check :size="16"/>{{ correcting ? '正在纠正并复核…' : `批量纠正 (${selected.length})` }}</button>
      </div>
      <p v-if="selected.length > 10000" class="notice error">单次最多纠正 10,000 条，请减少选择。</p>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <template v-if="result">
        <div class="rate-summary"><span>已处理 <b>{{ result.processed }}</b> 位用户</span><span>已检查 <b>{{ result.checked }}</b> 个用户分组</span><span>无适用规则 <b>{{ result.skipped }}</b> 位</span><span>异常用户 <b>{{ result.abnormal_users }}</b> 位</span></div>
        <p class="small muted">巡检时间：{{ new Date(result.completed_at).toLocaleString('zh-CN') }} · 结果有效期 30 分钟</p>
        <p v-if="result.missing_emails.length" class="notice error">未找到邮箱：{{ result.missing_emails.join('、') }}</p>
        <p v-if="!result.inspection_id && !correcting" class="notice">本次纠正已结束，请重新巡检查看最新状态。</p>
      </template>
      <div v-if="correction" class="notice" role="status">已确认纠正 {{ correction.succeeded }} 条，未确认 {{ correction.failed }} 条。<p v-for="item in correction.results.filter(item => item.error)" :key="item.id">{{ item.id }}：{{ item.error }}</p></div>
      <div class="rate-table-wrap" v-if="rows.length">
        <table class="rate-table"><thead><tr><th><input type="checkbox" aria-label="全选异常倍率" :checked="allSelected" :indeterminate="selected.length > 0 && !allSelected" :disabled="correcting || !result.inspection_id" @change="selectAll"/></th><th>用户</th><th>分组</th><th>历史总充值</th><th>当前倍率</th><th>预期倍率</th><th>来源</th><th>结果</th></tr></thead>
          <tbody><tr v-for="row in visible" :key="row.id"><td><input v-model="selected" type="checkbox" :value="row.id" :aria-label="`${row.email} · ${row.group}`" :disabled="correcting || !result.inspection_id"/></td><td><strong>{{ row.username || row.email }}</strong><small>#{{ row.user_id }} · {{ row.email }}</small></td><td>{{ row.group }}</td><td>${{ format(row.total) }}</td><td>{{ format(row.current) }}×</td><td><strong>{{ format(row.expected) }}×</strong></td><td>{{ row.custom ? '用户专属' : '分组默认' }}</td><td><span class="badge warning">{{ row.status === 'invalid' ? '倍率数据缺失' : '倍率不符' }}</span></td></tr></tbody>
        </table>
      </div>
      <p v-else-if="result" class="muted">{{ result.checked ? '未发现倍率异常。' : '没有符合充值门槛及分组访问权限的用户。' }}</p>
      <p v-else-if="!busy" class="muted">点击「开始巡检」检查用户倍率。</p>
      <div v-if="pages > 1" class="form-actions"><button class="button" :disabled="page <= 1" @click="page--">上一页</button><span>{{ page }} / {{ pages }} · 共 {{ rows.length }} 条</span><button class="button" :disabled="page >= pages" @click="page++">下一页</button></div>
    </section>
    <dialog ref="confirmation" class="rate-confirm" aria-labelledby="rate-confirm-title">
      <form @submit.prevent="correct"><h2 id="rate-confirm-title">确认纠正 {{ selected.length }} 条倍率</h2><p>将以下用户分组调整为预期倍率。提交前会重新核对充值和倍率，结果变化时需重新巡检。</p>
        <div class="rate-confirm-list"><p v-for="row in selectedRows" :key="row.id"><strong>{{ row.email }}</strong> · {{ row.group }}<br/>{{ format(row.current) }}× → {{ format(row.expected) }}×</p></div>
        <div class="form-actions"><button type="button" class="button" @click="confirmation.close()">取消</button><button class="button primary">确认纠正</button></div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.rate-inspection { display: grid; gap: 20px; }
.rate-inspection textarea { width: 100%; resize: vertical; border: 1px solid var(--control-border); border-radius: 9px; padding: 12px; background: white; color: var(--text); }
.group-picker { margin: 20px 0 0; padding: 16px; border: 1px solid var(--border); border-radius: 10px; min-width: 0; }
.group-picker legend { padding: 0 6px; font-weight: 600; }
.group-tools { display: flex; gap: 12px; }
.group-tools input { min-width: 0; flex: 1; }
.group-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; max-height: 260px; overflow: auto; margin: 12px 0; }
.group-option { display: flex; flex-direction: row; align-items: center; gap: 10px; padding: 10px; margin: 0; border-radius: 8px; background: var(--bg); cursor: pointer; }
.group-option input { width: 16px; height: 16px; flex: 0 0 auto; }
.group-option span { min-width: 0; overflow-wrap: anywhere; }
.group-option small { display: block; color: var(--muted); margin-top: 3px; }
.tier-editor { display: grid; gap: 12px; margin-top: 20px; }
.tier-row { display: grid; grid-template-columns: 1fr 1fr auto; align-items: end; gap: 16px; }
.tier-row label { margin: 0; }
.rate-summary { display: flex; gap: 24px; flex-wrap: wrap; margin: 20px 0 12px; }
.rate-table-wrap { overflow-x: auto; margin-top: 20px; }
.rate-table { width: 100%; border-collapse: collapse; text-align: left; white-space: nowrap; }
.rate-table th, .rate-table td { padding: 14px 12px; border-bottom: 1px solid var(--border); }
.rate-table th { color: var(--muted); font-size: 12px; }
.rate-table small { display: block; margin-top: 4px; color: var(--muted); }
.rate-table input { width: 16px; height: 16px; }
.rate-confirm { width: min(640px, calc(100vw - 32px)); border: 0; border-radius: 16px; padding: 28px; }
.rate-confirm::backdrop { background: #11182766; }
.rate-confirm-list { max-height: 45vh; overflow: auto; }
@media (max-width: 680px) { .tier-row { grid-template-columns: 1fr; gap: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--border); } }
</style>
