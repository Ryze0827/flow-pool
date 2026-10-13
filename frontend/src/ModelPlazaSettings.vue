<script setup>
import { onMounted, ref } from 'vue'
import { api } from './api'
const emit = defineEmits(['notify'])
const settings = ref(null)
const hiddenGroups = ref('')
const busy = ref(false)
const error = ref('')
async function load() {
  busy.value = true
  error.value = ''
  try {
    settings.value = await api('/model-plaza/settings')
    hiddenGroups.value = settings.value.hidden_group_ids.join(', ')
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function save() {
  error.value = ''
  const values = hiddenGroups.value.split(/[,，\s]+/).filter(Boolean)
  if (values.some(value => !/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)))) {
    error.value = '分组 ID 必须是正整数，以逗号分隔'; return
  }
  busy.value = true
  try {
    settings.value = await api('/model-plaza/settings', 'PUT', { ...settings.value, hidden_group_ids: values.map(Number) })
    emit('notify', '模型广场配置已保存')
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <section class="card">
    <div class="card-title"><div><h2>模型广场</h2><p>管理 erxinai 模型广场展示。模型、价格及用户可见分组实时取自 Sub2API。</p></div><button :disabled="busy" @click="load">刷新</button></div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <form v-if="settings" @submit.prevent="save">
      <fieldset :disabled="busy">
        <label class="check-row"><input v-model="settings.enabled" type="checkbox"/>启用模型广场</label>
        <label>价格说明（Markdown，留空沿用上游）<textarea v-model="settings.description" rows="6" maxlength="20000"/></label>
        <label>隐藏分组 ID<input v-model="hiddenGroups" placeholder="例如：2, 5"/></label>
        <p class="small muted">隐藏仅影响展示，不修改用户权限或实际计费。Sub2API 需支持并启用模型广场接口；用户必须登录 erxinai 才能访问。</p>
        <button class="primary" type="submit">{{ busy ? '保存中…' : '保存配置' }}</button>
      </fieldset>
    </form>
  </section>
</template>
