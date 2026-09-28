<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { CloudUpload, LoaderCircle, X } from 'lucide-vue-next'
import { api } from './api'
import { parseLoginRows } from './loginRows'

const props = defineProps({ batch: Object, item: Object, source: String })
const emit = defineEmits(['close', 'loading', 'updated'])
const dialog = ref(null)
const source = ref(props.source || '')
const provider = ref('session_studio')
const loading = ref(false)
const checking = ref(true)
const ready = ref(false)
const savedAvailable = ref(false)
const manual = ref(false)
const useSaved = computed(() => savedAvailable.value && !manual.value)
const error = ref('')
let mounted = true

async function loadSaved() {
  checking.value = true
  ready.value = false
  error.value = ''
  try {
    const result = await api(`/import/${props.batch.id}/items/${props.item.index}/relogin`)
    if (!mounted) return
    savedAvailable.value = result.saved_available
    manual.value = !result.saved_available
    if (result.provider) provider.value = result.provider
    ready.value = true
  } catch (failure) {
    if (mounted) error.value = failure.message
  } finally {
    if (mounted) checking.value = false
  }
}

async function submit() {
  if (loading.value || checking.value || !ready.value) return
  error.value = ''
  let row
  try {
    if (!useSaved.value) {
      const rows = parseLoginRows(source.value)
      if (rows.length !== 1) throw new Error('请只粘贴当前账号的一行登录信息')
      row = rows[0]
      if (props.item.email && row.email.toLowerCase() !== props.item.email.toLowerCase()) throw new Error('登录邮箱必须与所选账号一致')
    }
  } catch (failure) { error.value = failure.message; return }
  loading.value = true
  emit('loading', true)
  try {
    const result = await api(`/import/${props.batch.id}/items/${props.item.index}/relogin`, 'POST', useSaved.value ? {
      use_saved: true, provider: provider.value
    } : {
      email: row.email, password: row.password, totp_secret: row.totp_secret, provider: provider.value
    })
    if (!mounted) return
    const item = result.items.find(item => item.index === props.item.index)
    emit('updated', result)
    if (item?.status === 'done') emit('close')
    else error.value = `登录完成，但推送未完成：${item?.message || '请查看批次状态'}。关闭弹框后可重试未完成项，无需再次登录。`
  } catch (failure) {
    if (mounted) error.value = failure.message
  } finally {
    if (row) { row.password = ''; row.totp_secret = '' }
    loading.value = false
    if (mounted) emit('loading', false)
  }
}

onMounted(async () => { await nextTick(); dialog.value?.showModal(); await loadSaved() })
onUnmounted(() => { mounted = false; source.value = ''; emit('loading', false) })
</script>

<template>
  <dialog ref="dialog" class="modal import-settings-dialog relogin-dialog" aria-labelledby="relogin-title" @click.self="!loading && $emit('close')" @cancel.prevent="!loading && $emit('close')">
    <form @submit.prevent="submit">
      <div class="modal-heading"><div><h2 id="relogin-title">重新登录并推送</h2><p>{{ item.name }}<template v-if="item.email"> · {{ item.email }}</template></p></div><button class="icon-button" type="button" :disabled="loading" aria-label="关闭重新登录" @click="$emit('close')"><X :size="20"/></button></div>
      <div class="modal-body form-panel">
        <p class="small muted">仅处理此账号，沿用该批次的号池、分组和模型映射；已有账号原位更新并开启调度，其它账号不重复推送。</p>
        <p v-if="checking" class="small muted" role="status">正在检查已保存的登录记录…</p>
        <template v-if="ready">
          <p v-if="useSaved" class="notice compact">将使用已加密保存的账号、密码和 2FA，无需重新填写。凭据不会回传浏览器。</p>
          <p v-else-if="!savedAvailable" class="small muted">未找到完整可复用的登录记录，请填写当前账号的信息；未开启 2FA 时密钥留空。</p>
          <label>登录通道<select v-model="provider" :disabled="loading"><option value="session_studio">Session Studio · session.ameng2027.xyz</option><option value="local">本地登录</option></select></label>
          <p v-if="provider === 'session_studio'" class="notice compact">确认后将此账号的邮箱、密码和 2FA 发送给 session.ameng2027.xyz，登录成功后直接推送入池。</p>
          <button v-if="savedAvailable" type="button" class="text-button" :disabled="loading" @click="manual = !manual">{{ manual ? '使用已保存的登录记录' : '更新登录信息' }}</button>
          <div v-if="!useSaved" class="quick-parse"><label>当前账号登录信息<small>邮箱----密码----2FA密钥</small><textarea v-model="source" rows="3" :disabled="loading" spellcheck="false" autocomplete="off" aria-label="重新登录账号信息" required></textarea><small>成功入池后加密保存本次账密。六位动态验证码不可复用，需要重新填写。</small></label></div>
        </template>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
        <button v-if="!ready && !checking" class="button" type="button" @click="loadSaved">重新检查登录记录</button>
        <p v-if="loading" class="small muted" role="status">正在重新登录并推送，请保持页面打开。{{ provider === 'session_studio' ? '第三方排队和登录最长约 26 分钟。' : '本地登录最长约 3 分半钟。' }}</p>
      </div>
      <div class="modal-footer"><button class="button" type="button" :disabled="loading" @click="$emit('close')">取消</button><button class="button primary" :disabled="loading || checking || !ready || (!useSaved && !source.trim())"><LoaderCircle v-if="loading" class="spinning" :size="16"/><CloudUpload v-else :size="16"/>{{ loading ? '正在登录并推送…' : '确认重新登录并推送' }}</button></div>
    </form>
  </dialog>
</template>
