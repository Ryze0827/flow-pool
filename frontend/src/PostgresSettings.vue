<script setup>
import { onMounted, ref } from 'vue'
import { Check, Database, RefreshCw } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const settings = ref(null)
const busy = ref(false)
const error = ref('')
const message = ref('')
async function load() {
  error.value = ''
  try { settings.value = { ...await api('/postgres/settings'), clear_password: false } }
  catch (cause) { error.value = cause.message }
}
async function perform(save) {
  if (busy.value) return
  busy.value = true
  error.value = ''; message.value = ''
  try {
    if (save) {
      settings.value = { ...await api('/postgres/settings', 'PUT', settings.value), clear_password: false }
      message.value = 'PostgreSQL 配置已加密保存，即时生效；原巡检结果需重新检查。'
      emit('notify', message.value)
    } else {
      const result = await api('/postgres/test', 'POST', settings.value)
      message.value = result.message + '。测试未保存配置。'
    }
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="panel form-panel postgres-settings">
    <div class="card-title"><div class="tinted-icon"><Database :size="21"/></div><div><h2>Sub2API PostgreSQL</h2><p>用于调用记录、号池统计、调度采样和用户倍率聚合；未配置时使用 API。</p></div><span v-if="settings" class="badge neutral">{{ { saved: '本地已保存', environment: '来自环境变量', default: '待配置' }[settings.source] }}</span></div>
    <p v-if="!settings && !error" class="muted">正在读取数据库配置…</p>
    <p v-if="error" class="notice error" role="alert">{{ error }} <button v-if="!settings" class="text-button" @click="load">重试</button></p>
    <form v-if="settings" @submit.prevent="perform(true)" @input="message = ''">
      <div class="form-grid">
        <label>数据库主机<input v-model.trim="settings.host" required maxlength="253" placeholder="127.0.0.1" :disabled="busy"/></label>
        <label>数据库端口<input v-model.number="settings.port" type="number" min="1" max="65535" required :disabled="busy"/></label>
        <label>数据库名称<input v-model.trim="settings.database" required maxlength="128" placeholder="sub2api" :disabled="busy"/></label>
        <label>数据库用户名<input v-model.trim="settings.username" maxlength="128" autocomplete="off" :disabled="busy"/><small>清空用户名并保存可停用 PG，同时清除保存的密码，恢复 API 查询。</small></label>
        <label>数据库密码<input v-model="settings.password" type="password" maxlength="4096" autocomplete="new-password" :placeholder="settings.password_configured ? '已保存，留空保持不变' : '输入数据库密码'" :disabled="busy || settings.clear_password"/><small>密码加密保存且不回显；改变连接目标时需要重新填写。</small></label>
        <label>SSL 模式<select v-model="settings.sslmode" :disabled="busy"><option value="disable">disable · 不使用 SSL</option><option value="allow">allow · 优先普通连接</option><option value="prefer">prefer · 优先 SSL</option><option value="require">require · 必须 SSL</option><option value="verify-ca">verify-ca · 验证证书</option><option value="verify-full">verify-full · 验证证书及主机名</option></select></label>
        <label>连接超时（秒）<input v-model.number="settings.connect_timeout" type="number" min="1" max="30" required :disabled="busy"/></label>
        <label>查询超时（秒）<input v-model.number="settings.statement_timeout" type="number" min="1" max="300" required :disabled="busy"/></label>
      </div>
      <label class="check-row"><input v-model="settings.clear_password" type="checkbox" :disabled="busy" @change="settings.password = ''"/>不使用密码（清除已保存密码）</label>
      <p class="small muted">先保存上方 Sub2API 管理员 Key，再测试数据库。页面保存的 PG 配置优先于环境变量；查询强制只读，修改仍通过 Sub2API 接口完成。</p>
      <p v-if="message" class="notice" role="status">{{ message }}</p>
      <div class="form-actions"><button class="button primary" :disabled="busy"><Check :size="16"/>保存数据库配置</button><button type="button" class="button" :disabled="busy" @click="perform(false)"><RefreshCw :size="16"/>{{ busy ? '处理中…' : '测试数据库连接' }}</button></div>
    </form>
  </section>
</template>

<style scoped>
.postgres-settings { grid-column: 1 / -1; }
.postgres-settings .check-row { margin: 16px 0; }
</style>
