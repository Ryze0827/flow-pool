<script setup>
import { computed, ref } from 'vue'
import { Plus, RefreshCw, X } from 'lucide-vue-next'
import { api } from './api'
const props = defineProps({ model: Object, groups: Array, proxies: Array, busy: Boolean })
defineEmits(['refresh'])
const syncingModels = ref(false)
const modelsError = ref('')

async function syncModels() {
  if (syncingModels.value) return
  syncingModels.value = true
  modelsError.value = ''
  try {
    const { models } = await api('/import/model-whitelist')
    const combined = [...new Set([...(props.model.model_whitelist || []).map(name => name.trim()).filter(Boolean), ...models])]
    if (combined.length > 200) throw new Error('模型白名单最多支持 200 项，请先删除不需要的模型')
    props.model.model_whitelist = combined
  } catch (error) { modelsError.value = error.message }
  finally { syncingModels.value = false }
}

const allGroupsSelected = computed(() => props.groups.length > 0 && props.groups.every(group => props.model.group_ids.includes(group.id)))

function toggleAllGroups() {
  const ids = new Set(props.groups.map(group => group.id))
  props.model.group_ids = allGroupsSelected.value
    ? props.model.group_ids.filter(id => !ids.has(id))
    : [...new Set([...props.model.group_ids, ...ids])]
}

function applyPresetMappings() {
  const mappings = props.model.model_mappings ||= []
  for (const [source, target] of [['gpt-5.6-luna', 'gpt-5.6-sol'], ['gpt-5.6-terra', 'gpt-5.6-sol'], ['gpt-6-luna', 'gpt-6-sol']]) {
    const existing = mappings.find(item => item.source === source)
    if (existing) existing.target = target
    else if (mappings.length < 200) mappings.push({ source, target })
  }
}
</script>

<template>
  <div class="form-grid">
    <label class="span-2">名称模板 <small>{email} / {id} / {index} / {workspace}</small><input v-model="model.name_template" placeholder="gpt-{email}" required /></label>
    <label>并发<input v-model.number="model.concurrency" type="number" min="1" max="1000" required /></label>
    <label>优先级<input v-model.number="model.priority" type="number" min="0" max="10000" required /></label>
    <label>计费倍率<input v-model.number="model.rate_multiplier" type="number" min="0" step="0.01" required /></label>
    <label>负载因子<input v-model.number="model.load_factor" type="number" min="1" max="10000" required /></label>
    <label>本地号池<select v-model="model.pool"><option value="priority">高权重组</option><option value="risk">风控组</option><option value="third_party">三方账号组</option></select></label>
    <label>Sub2API 代理<select v-model="model.proxy_id"><option :value="null">不设置</option><option v-for="proxy in proxies" :key="proxy.id" :value="proxy.id">{{ proxy.name }}</option></select></label>
    <div class="span-2"><div class="field-title">Sub2API 分组 <small>GPT 平台，可多选</small><button class="text-button" type="button" :disabled="busy || !groups.length" @click="toggleAllGroups">{{ allGroupsSelected ? '取消全选' : '全选' }}</button><button class="text-button" type="button" :disabled="busy" @click="$emit('refresh')"><RefreshCw :size="13" />读取分组 / 代理</button></div>
      <div class="group-picker"><label v-for="group in groups" :key="group.id" class="check-row"><input v-model="model.group_ids" type="checkbox" :value="group.id"/><span>{{ group.name }}</span><small>#{{ group.id }}{{ group.status === 'inactive' ? ' · 已停用' : '' }}</small></label><p v-if="!groups.length" class="muted">先连接 Sub2API，读取 GPT 平台分组。</p></div>
    </div>
    <div class="span-2 model-mappings">
      <div class="field-title">模型白名单 <small>按原模型名称调用</small><button class="text-button" type="button" :disabled="busy || syncingModels" @click="syncModels"><RefreshCw :size="13" :class="{ spinning: syncingModels }"/>同步最新支持模型</button><button class="text-button" type="button" :disabled="busy || syncingModels || model.model_whitelist?.length >= 200" @click="(model.model_whitelist ||= []).push('')"><Plus :size="13" />添加模型</button></div>
      <p v-if="modelsError" class="notice error" role="alert">{{ modelsError }}</p>
      <div v-for="(name, index) in model.model_whitelist" :key="index" class="whitelist-row">
        <input v-model.trim="model.model_whitelist[index]" :aria-label="`第 ${index + 1} 个白名单模型`" placeholder="完整模型名称，如 gpt-6-sol" maxlength="200" :disabled="busy || syncingModels" required />
        <button class="icon-button" type="button" :disabled="busy || syncingModels" :aria-label="`删除第 ${index + 1} 个白名单模型`" title="删除模型" @click="model.model_whitelist.splice(index, 1)"><X :size="14" /></button>
      </div>
      <small>默认填入内置 OpenAI 模型，按钮补齐内置清单并保留自定义模型。确认推送时自动合并内置清单、该账号上游实时支持模型及自定义项；同步失败会提示重试。填写完整模型名称，不支持通配符。白名单与下方映射共同限定本批账号可接收的模型；同名冲突以映射为准。JSON / 账密上号共用，保存后用于新批次。</small>
    </div>
    <div class="span-2 model-mappings">
      <button class="text-button" type="button" :disabled="busy" @click="applyPresetMappings">应用预置映射</button>
      <div class="field-title">模型映射 <small>JSON / 账密上号共用</small><button class="text-button" type="button" :disabled="busy || model.model_mappings?.length >= 200" @click="(model.model_mappings ||= []).push({ source: '', target: '' })"><Plus :size="13" />添加映射</button></div>
      <div v-for="(mapping, index) in model.model_mappings" :key="index" class="mapping-row">
        <input v-model="mapping.source" :aria-label="`第 ${index + 1} 条请求模型`" placeholder="请求模型，如 gpt-*" maxlength="200" :disabled="busy" required />
        <span>→</span>
        <input v-model="mapping.target" :aria-label="`第 ${index + 1} 条目标模型`" placeholder="目标模型名称" maxlength="200" :disabled="busy" required />
        <button class="icon-button" type="button" :disabled="busy" :aria-label="`删除第 ${index + 1} 条映射`" title="删除映射" @click="model.model_mappings.splice(index, 1)"><X :size="14" /></button>
      </div>
      <small>白名单与映射均为空时保留 JSON 原有配置；任一填写后，二者合并覆盖本批账号的模型配置，确认入池时生效。请求模型支持末尾 *，目标模型需填写完整名称。</small>
    </div>
    <label class="span-2">备注<input v-model="model.notes" placeholder="来自 FlowPool 账号调控平台" /></label>
    <label class="span-2">重复账号处理<select v-model="model.duplicate"><option value="skip">跳过同名 / 同邮箱账号</option><option value="update">原位更新已有账号（保留账号 ID 和调用记录）</option></select></label>
    <label class="check-row span-2"><input v-model="model.auto_pause_on_expired" type="checkbox" />到期自动暂停（使用 JSON 中的账号到期时间）</label>
    <label class="check-row span-2"><input v-model="model.confirm_mixed_channel_risk" type="checkbox" />确认混合渠道风险</label>
  </div>
</template>

<style scoped>
.model-mappings { display: grid; gap: 10px; }
.mapping-row { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto; align-items: center; gap: 8px; }
.mapping-row input { min-width: 0; }
.whitelist-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; }
.whitelist-row input { min-width: 0; }
</style>
