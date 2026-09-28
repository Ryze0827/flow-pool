<script setup>
import { onMounted, ref, watch } from 'vue'
import { LockKeyhole, LogIn, ShieldCheck } from 'lucide-vue-next'
import { api } from './api'
import TurnstileWidget from './TurnstileWidget.vue'

const props = defineProps({ configured: Boolean, statusError: String })
const emit = defineEmits(['authenticated', 'retry-status'])
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const settings = ref(null)
const settingsLoading = ref(false)
const totp = ref(null)
const code = ref('')
const turnstileToken = ref('')
const turnstileReset = ref(0)

async function loadSettings() {
  settingsLoading.value = true
  error.value = ''
  try { settings.value = await api('/auth/public-settings') }
  catch (cause) { error.value = cause.message }
  finally { settingsLoading.value = false }
}
onMounted(() => { if (props.configured) void loadSettings() })
watch(() => props.configured, value => { if (value && !settings.value) void loadSettings() })

async function login() {
  if (busy.value || !settings.value) return
  busy.value = true
  error.value = ''
  try {
    const result = totp.value
      ? await api('/auth/login/2fa', 'POST', { temp_token: totp.value.temp_token, totp_code: code.value })
      : await api('/auth/login', 'POST', { email: email.value, password: password.value, turnstile_token: turnstileToken.value })
    password.value = ''
    if (result.requires_2fa) { totp.value = result; return }
    code.value = ''
    emit('authenticated', result)
  } catch (cause) {
    error.value = cause.message
    turnstileToken.value = ''
    turnstileReset.value += 1
  } finally { busy.value = false }
}
function cancelTotp() {
  totp.value = null
  code.value = ''
  error.value = ''
  turnstileToken.value = ''
  turnstileReset.value += 1
}
</script>

<template>
  <main class="auth-shell">
    <section class="auth-card panel">
      <div class="auth-brand"><img src="/favicon.svg" alt="" width="46" height="46"/><div><strong>FlowPool</strong><small>GPT 账号调控</small></div></div>
      <div class="auth-heading"><div class="tinted-icon"><ShieldCheck :size="22"/></div><div><h1>管理员登录</h1><p>使用与 erxinai 相同的管理员账号</p></div></div>
      <div v-if="statusError"><p class="notice error" role="alert">{{ statusError }}</p><button class="button full-width" @click="emit('retry-status')">重新连接登录服务</button></div>
      <p v-else-if="!configured" class="notice error">登录服务尚未配置，请在服务器设置 FLOWPOOL_SUB2API_URL 后重启。</p>
      <form v-else @submit.prevent="login">
        <template v-if="totp">
          <p>请输入 {{ totp.user_email_masked }} 的身份验证器动态码。</p>
          <label>二步验证码<input v-model="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required autofocus/></label>
        </template>
        <template v-else>
          <label>管理员邮箱<input v-model.trim="email" type="email" autocomplete="username" autofocus required maxlength="254" placeholder="输入管理员邮箱"/></label>
          <label>密码<div class="auth-password"><LockKeyhole :size="16"/><input v-model="password" type="password" autocomplete="current-password" required maxlength="512" placeholder="输入密码"/></div></label>
          <TurnstileWidget v-if="settings?.turnstile_enabled" :key="turnstileReset" :site-key="settings.turnstile_site_key" @token="turnstileToken = $event"/>
        </template>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        <button v-if="!settings && !settingsLoading" type="button" class="button full-width" @click="loadSettings">重新加载登录设置</button>
        <button class="button primary full-width" :disabled="busy || settingsLoading || !settings || (!totp && settings.turnstile_enabled && !turnstileToken)"><LogIn :size="16"/>{{ busy ? '正在验证…' : totp ? '验证并登录' : '登录管理后台' }}</button>
        <button v-if="totp" type="button" class="text-button" :disabled="busy" @click="cancelTotp">返回邮箱登录</button>
      </form>
      <small class="auth-footnote">仅允许管理员登录，不开放注册。会话有效期最长 12 小时。</small>
    </section>
  </main>
</template>
