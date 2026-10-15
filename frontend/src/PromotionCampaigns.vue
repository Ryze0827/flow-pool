<script setup>
import { computed, onMounted, ref } from 'vue'
import { MoonStar, RefreshCw } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const groups = ref([])
const loading = ref(false)
const updating = ref(null)
const error = ref('')
const enabledCount = computed(() => groups.value.filter(group => group.status === 'active').length)

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try { groups.value = (await api('/promotions/welfare-groups')).items }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}

async function toggle(group) {
  if (updating.value !== null) return
  const enabled = group.status !== 'active'
  updating.value = group.id
  error.value = ''
  try {
    const result = await api(`/promotions/welfare-groups/${group.id}/status`, 'PUT', { enabled })
    if (!result.verified) throw new Error(result.error || '福利分组状态未确认，请刷新后重试')
    group.status = result.status
    emit('notify', `${group.name}已${enabled ? '启用' : '停用'}`)
  } catch (cause) {
    const message = cause.message
    await load()
    error.value = message
  } finally { updating.value = null }
}

onMounted(load)
</script>

<template>
  <div class="promotion-campaigns">
    <header class="promotion-heading">
      <div><h1>促销活动</h1><p>管理促销活动关联的 GPT / OpenAI 分组状态。</p></div>
      <button class="button" :disabled="loading || updating !== null" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新分组</button>
    </header>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>

    <section class="panel welfare-panel" aria-labelledby="welfare-title">
      <div class="welfare-heading">
        <div class="welfare-icon"><MoonStar :size="21"/></div>
        <div><h2 id="welfare-title">夜间福利分组</h2><p>名称中包含“福利”的 OpenAI 平台分组，开关直接对应分组的启用状态。</p></div>
        <span v-if="groups.length" class="badge neutral">{{ enabledCount }} / {{ groups.length }} 已启用</span>
      </div>

      <div v-if="groups.length" class="welfare-list">
        <div v-for="group in groups" :key="group.id" class="welfare-row">
          <div><strong>{{ group.name }}</strong><small>#{{ group.id }} · {{ group.status === 'active' ? '正常' : group.status === 'inactive' ? '已停用' : group.status || '状态未知' }}</small></div>
          <button type="button" class="welfare-switch" role="switch" :aria-checked="group.status === 'active'" :aria-label="`${group.status === 'active' ? '停用' : '启用'}${group.name}`" :disabled="loading || updating !== null || !['active', 'inactive'].includes(group.status)" @click="toggle(group)"><span/></button>
        </div>
      </div>
      <p v-else class="welfare-empty" role="status">{{ loading ? '正在读取福利分组…' : '没有名称中包含“福利”的 OpenAI 平台分组。' }}</p>
    </section>
  </div>
</template>

<style scoped>
.promotion-campaigns { max-width: 76rem; margin-inline: auto; }
.promotion-heading { display: flex; align-items: center; justify-content: space-between; gap: 1.5rem; margin-bottom: 1.75rem; }
.promotion-heading h1 { font-size: 1.6rem; }
.promotion-heading p, .welfare-heading p { color: var(--muted); font-size: .875rem; }
.welfare-panel { overflow: hidden; }
.welfare-heading { display: flex; align-items: center; gap: 1rem; padding: 1.5rem; }
.welfare-heading > div:nth-child(2) { flex: 1; min-width: 0; }
.welfare-heading h2 { font-size: 1rem; }
.welfare-icon { display: grid; place-items: center; width: 2.75rem; height: 2.75rem; flex-shrink: 0; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border-radius: .75rem; }
.welfare-list { border-top: 1px solid var(--border); }
.welfare-row { display: flex; align-items: center; gap: 1rem; min-height: 4.5rem; padding: .9rem 1.5rem; border-bottom: 1px solid var(--border); }
.welfare-row:last-child { border-bottom: 0; }
.welfare-row > div { flex: 1; min-width: 0; }
.welfare-row strong, .welfare-row small { display: block; overflow-wrap: anywhere; }
.welfare-row strong { font-size: .9rem; }
.welfare-row small { margin-top: .35rem; color: var(--muted); font-size: .8rem; }
.welfare-switch { width: 2.8rem; height: 1.6rem; padding: .2rem; border: 0; border-radius: 2rem; background: var(--control-border); flex-shrink: 0; }
.welfare-switch span { display: block; width: 1.2rem; height: 1.2rem; border-radius: 50%; background: Canvas; }
.welfare-switch[aria-checked='true'] { background: var(--accent); }
.welfare-switch[aria-checked='true'] span { transform: translateX(1.2rem); }
.welfare-switch:disabled { opacity: .55; cursor: not-allowed; }
.welfare-empty { margin: 0; padding: 2.5rem 1.5rem; border-top: 1px solid var(--border); color: var(--muted); text-align: center; font-size: .875rem; }
@media (max-width: 600px) { .promotion-heading { align-items: flex-start; } .promotion-heading h1 { font-size: 1.35rem; } .welfare-heading, .welfare-row { padding-inline: 1.1rem; } .welfare-icon { display: none; } }
</style>
