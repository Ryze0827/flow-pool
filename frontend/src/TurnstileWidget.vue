<script>
// Port of erxinai/src/auth/TurnstileWidget.jsx.
let scriptPromise = null
function loadTurnstile() {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  if (scriptPromise) return scriptPromise
  scriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
    script.async = true
    script.onload = () => window.turnstile ? resolve(window.turnstile) : fail()
    const fail = () => { scriptPromise = null; script.remove(); reject(new Error('人机校验加载失败，请重试')) }
    script.onerror = fail
    document.head.appendChild(script)
  })
  return scriptPromise
}
</script>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
const props = defineProps({ siteKey: String })
const emit = defineEmits(['token'])
const container = ref(null)
const error = ref('')
let widget = null
let active = true
onMounted(async () => {
  if (!props.siteKey) { error.value = '登录服务尚未配置人机校验'; return }
  try {
    const turnstile = await loadTurnstile()
    if (!active) return
    widget = turnstile.render(container.value, {
      sitekey: props.siteKey, size: 'flexible',
      callback: token => { error.value = ''; emit('token', token) },
      'expired-callback': () => emit('token', ''),
      'error-callback': () => { emit('token', ''); error.value = '人机校验失败，请重试' }
    })
  } catch (cause) { error.value = cause.message }
})
onUnmounted(() => {
  active = false
  if (widget !== null && window.turnstile) window.turnstile.remove(widget)
})
</script>

<template><div ref="container"></div><p v-if="error" class="notice error" role="alert">{{ error }}</p></template>
