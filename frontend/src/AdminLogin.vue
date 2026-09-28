<script setup>
import { ref } from 'vue'
import { LockKeyhole, LogIn, ShieldCheck } from 'lucide-vue-next'
import { api } from './api'

defineProps({ configured: Boolean })
const emit = defineEmits(['authenticated'])
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function login() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api('/auth/login', 'POST', { username: username.value, password: password.value })
    password.value = ''
    emit('authenticated', result)
  } catch (cause) {
    error.value = cause.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-shell">
    <section class="auth-card panel">
      <div class="auth-brand"><img src="/favicon.svg" alt="" width="46" height="46"/><div><strong>FlowPool</strong><small>GPT 账号调控</small></div></div>
      <div class="auth-heading"><div class="tinted-icon"><ShieldCheck :size="22"/></div><div><h1>管理员登录</h1><p>登录后管理本地调度工作空间</p></div></div>
      <p v-if="!configured" class="notice error">管理员尚未配置，请在服务器的 .env 中设置管理员账号和密码后重启服务。</p>
      <form v-else @submit.prevent="login">
        <label>管理员账号<input v-model.trim="username" autocomplete="username" autofocus required maxlength="128" placeholder="输入管理员账号"/></label>
        <label>密码<div class="auth-password"><LockKeyhole :size="16"/><input v-model="password" type="password" autocomplete="current-password" required maxlength="512" placeholder="输入密码"/></div></label>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        <button class="button primary full-width" :disabled="busy"><LogIn :size="16"/>{{ busy ? '正在登录…' : '登录管理后台' }}</button>
      </form>
      <small class="auth-footnote">登录会话默认有效 12 小时。</small>
    </section>
  </main>
</template>
