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
      <div v-if="statusError"><p class="notice error" role="alert">{{ statusError }}</p><a-button class="full-width" @click="emit('retry-status')">重新连接登录服务</a-button></div>
      <p v-else-if="!configured" class="notice error">登录服务尚未配置，请在服务器设置 FLOWPOOL_SUB2API_URL 后重启。</p>
      <form v-else @submit.prevent="login">
        <template v-if="totp">
          <p>请输入 {{ totp.user_email_masked }} 的身份验证器动态码。</p>
          <label>二步验证码<a-input v-model:value="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required autofocus /></label>
        </template>
        <template v-else>
          <label>管理员邮箱<a-input v-model:value="email" type="email" autocomplete="username" autofocus required maxlength="254" placeholder="输入管理员邮箱" @blur="email = email.trim()"><template #prefix><LogIn :size="15" /></template></a-input></label>
          <label>密码<a-input-password v-model:value="password" autocomplete="current-password" required maxlength="512" placeholder="输入密码"><template #prefix><LockKeyhole :size="15" /></template></a-input-password></label>
          <TurnstileWidget v-if="settings?.turnstile_enabled" :key="turnstileReset" :site-key="settings.turnstile_site_key" @token="turnstileToken = $event"/>
        </template>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        <a-button v-if="!settings && !settingsLoading" html-type="button" class="full-width" @click="loadSettings">重新加载登录设置</a-button>
        <a-button type="primary" html-type="submit" class="full-width" :loading="busy" :disabled="settingsLoading || !settings || (!totp && settings.turnstile_enabled && !turnstileToken)"><LogIn :size="16"/>{{ busy ? '正在验证…' : totp ? '验证并登录' : '登录管理后台' }}</a-button>
        <a-button v-if="totp" type="link" html-type="button" :disabled="busy" @click="cancelTotp">返回邮箱登录</a-button>
      </form>
      <small class="auth-footnote">仅允许管理员登录，不开放注册。登录状态自动续期，连续 30 天未使用后过期。</small>
    </section>
  </main>
</template>
