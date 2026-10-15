<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Clock3, MoonStar, RefreshCw, Save } from 'lucide-vue-next'
import { api } from './api'

const emit = defineEmits(['notify'])
const groups = ref([])
const loading = ref(false)
const updating = ref(null)
const savingSchedule = ref(false)
const error = ref('')
const schedule = ref(null)
const scheduleDraft = ref({ enabled: false, show_announcement: true, start_hour: 22, end_hour: 8, timezone: 'Asia/Shanghai', countdown_copy: { zh_opening: '', zh_closing: '', en_opening: '', en_closing: '' } })
const hours = Array.from({ length: 24 }, (_, value) => ({ value, label: `${String(value).padStart(2, '0')}:00` }))
const enabledCount = computed(() => groups.value.filter(group => group.status === 'active').length)
const scheduleDirty = computed(() => schedule.value && JSON.stringify(scheduleDraft.value) !== JSON.stringify({ enabled: schedule.value.enabled, show_announcement: schedule.value.show_announcement, start_hour: schedule.value.start_hour, end_hour: schedule.value.end_hour, timezone: schedule.value.timezone, countdown_copy: schedule.value.countdown_copy }))
const busy = computed(() => loading.value || updating.value !== null || savingSchedule.value)
const scheduleState = computed(() => !schedule.value?.enabled ? '未开启定时控制' : schedule.value.active_now ? '当前处于福利时段' : '当前不在福利时段')
const transitionText = computed(() => schedule.value?.next_transition_at ? new Date(schedule.value.next_transition_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '')
let polling

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await api('/promotions/welfare-groups')
    groups.value = result.items
    schedule.value = result.schedule
    scheduleDraft.value = { enabled: result.schedule.enabled, show_announcement: result.schedule.show_announcement, start_hour: result.schedule.start_hour, end_hour: result.schedule.end_hour, timezone: result.schedule.timezone, countdown_copy: { ...result.schedule.countdown_copy } }
  }
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

async function saveSchedule() {
  if (savingSchedule.value || !scheduleDirty.value) return
  if (scheduleDraft.value.start_hour === scheduleDraft.value.end_hour) {
    error.value = '开始时间和结束时间不能相同'
    return
  }
  savingSchedule.value = true
  error.value = ''
  try {
    const result = await api('/promotions/welfare-schedule', 'PUT', scheduleDraft.value)
    await load()
    emit('notify', result.error ? '定时配置已保存，分组状态将在下轮自动重试' : '福利分组定时配置已保存', Boolean(result.error))
  } catch (cause) { error.value = cause.message }
  finally { savingSchedule.value = false }
}

onMounted(() => {
  void load()
  polling = setInterval(() => {
    if (!busy.value && !scheduleDirty.value) void load()
  }, 60000)
})
onUnmounted(() => clearInterval(polling))
</script>

<template>
  <div class="promotion-campaigns">
    <header class="promotion-heading">
      <div><h1>促销活动</h1><p>管理促销活动关联的 GPT / OpenAI 分组状态。</p></div>
      <button class="button" :disabled="busy" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新分组</button>
    </header>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>

    <section class="panel welfare-panel" aria-labelledby="welfare-title">
      <div class="welfare-heading">
        <div class="welfare-icon"><MoonStar :size="21"/></div>
        <div><h2 id="welfare-title">夜间福利分组</h2><p>名称中包含“福利”的 OpenAI 平台分组，开关直接对应分组的启用状态。</p></div>
        <span v-if="groups.length" class="badge neutral">{{ enabledCount }} / {{ groups.length }} 已启用</span>
      </div>

      <form v-if="schedule" class="welfare-schedule" @submit.prevent="saveSchedule">
        <div class="schedule-toggle-copy"><Clock3 :size="18"/><div><strong>定时控制</strong><small>{{ scheduleState }}<template v-if="transitionText"> · 下次切换 {{ transitionText }}</template></small></div></div>
        <button type="button" class="welfare-switch" role="switch" :aria-checked="scheduleDraft.enabled" aria-label="启用福利分组定时控制" :disabled="busy" @click="scheduleDraft.enabled = !scheduleDraft.enabled"><span/></button>
        <label class="schedule-announcement-toggle"><span>erxinai 公告</span><button type="button" class="welfare-switch" role="switch" :aria-checked="scheduleDraft.show_announcement" aria-label="在 erxinai 展示福利分组公告" :disabled="busy" @click="scheduleDraft.show_announcement = !scheduleDraft.show_announcement"><span/></button></label>
        <label>开始时间<select v-model.number="scheduleDraft.start_hour" required :disabled="busy"><option v-for="item in hours" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <label>结束时间<select v-model.number="scheduleDraft.end_hour" required :disabled="busy"><option v-for="item in hours" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <div class="schedule-timezone"><small>时区</small><strong>Asia/Shanghai</strong></div>
        <div class="schedule-copy-grid">
          <label>中文开启文案<input v-model.trim="scheduleDraft.countdown_copy.zh_opening" maxlength="100" required /></label>
          <label>中文关闭文案<input v-model.trim="scheduleDraft.countdown_copy.zh_closing" maxlength="100" required /></label>
          <label>英文开启文案<input v-model.trim="scheduleDraft.countdown_copy.en_opening" maxlength="100" required /></label>
          <label>英文关闭文案<input v-model.trim="scheduleDraft.countdown_copy.en_closing" maxlength="100" required /></label>
        </div>
        <button class="button primary" type="submit" :disabled="busy || !scheduleDirty"><Save :size="15"/>{{ savingSchedule ? '保存中…' : '保存时间段' }}</button>
        <p>支持跨午夜，例如 22:00–08:00。开始时间生效，结束时间停用；后台每 {{ schedule.interval_seconds }} 秒检查一次。</p>
      </form>

      <p v-if="schedule?.error" class="notice error schedule-error">{{ schedule.error }}</p>

      <div v-if="groups.length" class="welfare-list">
        <div v-for="group in groups" :key="group.id" class="welfare-row">
          <div><strong>{{ group.name }}</strong><small>#{{ group.id }} · {{ group.status === 'active' ? '正常' : group.status === 'inactive' ? '已停用' : group.status || '状态未知' }}</small></div>
          <button type="button" class="welfare-switch" role="switch" :aria-checked="group.status === 'active'" :aria-label="`${group.status === 'active' ? '停用' : '启用'}${group.name}`" :title="schedule?.enabled ? '定时控制开启期间不能手动修改' : ''" :disabled="busy || schedule?.enabled || !['active', 'inactive'].includes(group.status)" @click="toggle(group)"><span/></button>
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
.welfare-schedule { display: grid; grid-template-columns: minmax(15rem, 1fr) auto minmax(8rem, .45fr) minmax(8rem, .45fr) auto auto; align-items: end; gap: 1rem; padding: 1.25rem 1.5rem; border-top: 1px solid var(--border); background: color-mix(in srgb, var(--accent) 3%, Canvas); }
.schedule-toggle-copy { display: flex; align-items: center; gap: .7rem; align-self: center; min-width: 0; }
.schedule-toggle-copy > svg { flex-shrink: 0; color: var(--accent); }
.schedule-toggle-copy strong, .schedule-toggle-copy small { display: block; }
.schedule-toggle-copy small { margin-top: .3rem; color: var(--muted); font-size: .78rem; line-height: 1.5; }
.welfare-schedule label { display: grid; gap: .4rem; color: var(--muted); font-size: .78rem; }
.welfare-schedule select { min-width: 0; }
.schedule-timezone { align-self: center; white-space: nowrap; }
.schedule-timezone small, .schedule-timezone strong { display: block; }
.schedule-timezone small { margin-bottom: .35rem; color: var(--muted); font-size: .78rem; }
.schedule-timezone strong { font-size: .82rem; }
.schedule-announcement-toggle { display: flex !important; align-items: center; gap: .6rem; white-space: nowrap; }
.schedule-copy-grid { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.schedule-copy-grid input { width: 100%; }
.welfare-schedule > p { grid-column: 1 / -1; margin: 0; color: var(--muted); font-size: .78rem; line-height: 1.6; }
.schedule-error { margin: 0 1.5rem 1.25rem; }
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
@media (max-width: 1050px) { .welfare-schedule { grid-template-columns: minmax(12rem, 1fr) auto 1fr 1fr; } .schedule-timezone { grid-column: 1 / 3; } }
@media (max-width: 600px) { .promotion-heading { align-items: flex-start; } .promotion-heading h1 { font-size: 1.35rem; } .welfare-heading, .welfare-row { padding-inline: 1.1rem; } .welfare-icon { display: none; } .welfare-schedule { grid-template-columns: 1fr auto; padding-inline: 1.1rem; } .welfare-schedule label { grid-column: span 1; } .schedule-timezone { grid-column: 1 / 2; } .schedule-copy-grid { grid-template-columns: 1fr; } .welfare-schedule > .button { grid-column: 1 / -1; } }
</style>
