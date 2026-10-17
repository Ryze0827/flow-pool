<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { CalendarCheck, Clock3, Copy, MoonStar, RefreshCw, Save, UserMinus, Users, X } from 'lucide-vue-next'
import { api } from './api'
import { useRefreshCountdown } from './refreshCountdown'

const emit = defineEmits(['notify'])
const groups = ref([])
const sourceGroups = ref([])
const copySourceByTarget = ref({})
const loading = ref(false)
const updating = ref(null)
const savingSchedule = ref(false)
const savingCheckin = ref(false)
const error = ref('')
const removeDialog = ref(null)
const removeTarget = ref(null)
const members = ref([])
const membersLoading = ref(false)
const removing = ref(false)
const copying = ref(null)
const removeError = ref('')
const removeOutcome = ref(null)
const removeProblems = computed(() => (removeOutcome.value?.results || []).filter(item => ['skipped', 'failed'].includes(item.status)))
const schedule = ref(null)
const checkin = ref(null)
const checkinDraft = ref({ enabled: false, min_streak_days: 1, max_streak_days: 7 })
const scheduleDraft = ref({ enabled: false, show_announcement: true, start_hour: 22, end_hour: 8, start_minute: 1320, end_minute: 480, timezone: 'Asia/Shanghai', countdown_copy: { zh_opening: '', zh_closing: '', en_opening: '', en_closing: '' } })
const enabledCount = computed(() => groups.value.filter(group => group.status === 'active').length)
const scheduleDirty = computed(() => schedule.value && JSON.stringify(scheduleDraft.value) !== JSON.stringify({ enabled: schedule.value.enabled, show_announcement: schedule.value.show_announcement, start_hour: schedule.value.start_hour, end_hour: schedule.value.end_hour, start_minute: schedule.value.start_minute, end_minute: schedule.value.end_minute, timezone: schedule.value.timezone, countdown_copy: schedule.value.countdown_copy }))
const checkinDirty = computed(() => checkin.value && JSON.stringify(checkinDraft.value) !== JSON.stringify(checkin.value.settings))
const checkinPeak = computed(() => Math.max(1, checkin.value?.today_count || 0, ...(checkin.value?.daily_counts || []).map(item => item.count)))
const busy = computed(() => loading.value || updating.value !== null || savingSchedule.value || savingCheckin.value || removing.value || copying.value !== null)
const scheduleState = computed(() => !schedule.value?.enabled ? '未开启定时控制' : schedule.value.active_now ? '当前处于福利时段' : '当前不在福利时段')
const transitionText = computed(() => schedule.value?.next_transition_at ? new Date(schedule.value.next_transition_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '')
const refreshIntervalSeconds = 60
const { text: refreshCountdownText, reset: resetRefreshCountdown } = useRefreshCountdown(refreshIntervalSeconds)
let polling

async function load() {
  if (loading.value) return
  resetRefreshCountdown()
  loading.value = true
  error.value = ''
  try {
    const result = await api('/promotions/welfare-groups')
    groups.value = result.items
    sourceGroups.value = result.source_groups || result.items
    schedule.value = result.schedule
    scheduleDraft.value = { enabled: result.schedule.enabled, show_announcement: result.schedule.show_announcement, start_hour: result.schedule.start_hour, end_hour: result.schedule.end_hour, start_minute: result.schedule.start_minute, end_minute: result.schedule.end_minute, timezone: result.schedule.timezone, countdown_copy: { ...result.schedule.countdown_copy } }
    checkin.value = await api('/promotions/check-in')
    checkinDraft.value = { ...checkin.value.settings }
  }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}

async function saveCheckin() {
  if (savingCheckin.value || !checkinDirty.value) return
  const min = Number(checkinDraft.value.min_streak_days)
  const max = Number(checkinDraft.value.max_streak_days)
  if (!Number.isInteger(min) || !Number.isInteger(max) || min < 1 || max < min) {
    error.value = '请输入有效的连续签到天数区间'
    return
  }
  savingCheckin.value = true
  error.value = ''
  try {
    await api('/promotions/check-in', 'PUT', { enabled: checkinDraft.value.enabled, min_streak_days: min, max_streak_days: max })
    await load()
    emit('notify', '签到活动配置已保存')
  } catch (cause) { error.value = cause.message }
  finally { savingCheckin.value = false }
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

async function openRemove(group) {
  if (busy.value) return
  removeTarget.value = group
  members.value = []
  removeError.value = ''
  removeOutcome.value = null
  membersLoading.value = true
  removeDialog.value.showModal()
  try { members.value = (await api(`/promotions/welfare-groups/${group.id}/members`)).items }
  catch (cause) { removeError.value = cause.message }
  finally { membersLoading.value = false }
}

async function removeMembers() {
  if (removing.value || !members.value.length) return
  removing.value = true
  removeError.value = ''
  try {
    const result = await api(`/promotions/welfare-groups/${removeTarget.value.id}/members/remove`, 'POST')
    removeOutcome.value = result
    emit('notify', `${result.group.name}：更新 ${result.changed} 个，无需修改 ${result.unchanged} 个，跳过 ${result.skipped} 个，失败或未确认 ${result.failed} 个`, result.failed + result.skipped > 0)
  } catch (cause) { removeError.value = cause.message }
  finally { removing.value = false }
}

async function copyMembers(group) {
  const sourceGroupId = Number(copySourceByTarget.value[group.id])
  if (!sourceGroupId || copying.value !== null) return
  copying.value = group.id
  error.value = ''
  try {
    const result = await api(`/promotions/welfare-groups/${group.id}/members/copy`, 'POST', { source_group_id: sourceGroupId })
    emit('notify', `${result.source_group.name} → ${result.group.name}：更新 ${result.changed} 个，无需修改 ${result.unchanged} 个，跳过 ${result.skipped} 个，失败或未确认 ${result.failed} 个`, result.failed + result.skipped > 0)
  } catch (cause) { error.value = cause.message }
  finally { copying.value = null }
}

async function saveSchedule() {
  if (savingSchedule.value || !scheduleDirty.value) return
  if (scheduleDraft.value.start_minute === scheduleDraft.value.end_minute) {
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

function minuteText(value) {
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

function setMinute(key, value) {
  const [hour, minute] = value.split(':').map(Number)
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return
  scheduleDraft.value[`${key}_minute`] = hour * 60 + minute
  scheduleDraft.value[`${key}_hour`] = hour
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
      <button class="button" :disabled="busy" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新分组 · <span class="refresh-countdown">{{ refreshCountdownText }}</span></button>
    </header>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>

    <section class="panel checkin-panel" aria-labelledby="checkin-title">
      <div class="welfare-heading">
        <div class="welfare-icon"><CalendarCheck :size="21"/></div>
        <div><h2 id="checkin-title">每日签到</h2><p>记录 erxinai 用户每日签到，并按 Asia/Shanghai 统计每天参与人数。</p></div>
        <span v-if="checkin" class="badge" :class="checkin.settings.enabled ? 'success' : 'neutral'">{{ checkin.settings.enabled ? '活动进行中' : '未开启' }}</span>
      </div>
      <form v-if="checkin" class="checkin-config" @submit.prevent="saveCheckin">
        <div class="schedule-toggle-copy"><CalendarCheck :size="18"/><div><strong>开启签到活动</strong><small>{{ checkin.settings.enabled ? '用户可以在 erxinai 顶部签到' : '开启后用户即可签到' }}</small></div></div>
        <button type="button" class="welfare-switch" role="switch" :aria-checked="checkinDraft.enabled" aria-label="开启签到活动" :disabled="busy" @click="checkinDraft.enabled = !checkinDraft.enabled"><span/></button>
        <label>连续签到最少天数<input v-model.number="checkinDraft.min_streak_days" type="number" min="1" max="3650" required :disabled="busy" /></label>
        <label>连续签到最多天数<input v-model.number="checkinDraft.max_streak_days" type="number" min="1" max="3650" required :disabled="busy" /></label>
        <button class="button primary" type="submit" :disabled="busy || !checkinDirty"><Save :size="15"/> {{ savingCheckin ? '保存中…' : '保存签到配置' }}</button>
        <p>连续签到区间用于展示活动目标，签到记录会按用户和日期去重。</p>
      </form>
      <div v-if="checkin" class="checkin-summary">
        <div class="checkin-stat"><Users :size="18"/><span>今日签到人数</span><strong>{{ checkin.today_count }}</strong></div>
        <div class="checkin-stat"><CalendarCheck :size="18"/><span>累计签到次数</span><strong>{{ checkin.total_checkins }}</strong></div>
      </div>
      <div v-if="checkin?.daily_counts?.length" class="checkin-counts">
        <div class="checkin-counts-heading"><strong>每日签到人数</strong><small>最近 {{ checkin.daily_counts.length }} 天</small></div>
        <div v-for="item in checkin.daily_counts" :key="item.date" class="checkin-count-row"><time>{{ item.date }}</time><div><i :style="{ width: `${item.count / checkinPeak * 100}%` }"/></div><strong>{{ item.count }}</strong></div>
      </div>
      <p v-else-if="checkin" class="welfare-empty">暂时还没有签到记录。</p>
    </section>

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
        <label>开始时间<input type="time" :value="minuteText(scheduleDraft.start_minute)" required :disabled="busy" @input="setMinute('start', $event.target.value)" /></label>
        <label>结束时间<input type="time" :value="minuteText(scheduleDraft.end_minute)" required :disabled="busy" @input="setMinute('end', $event.target.value)" /></label>
        <div class="schedule-timezone"><small>时区</small><strong>Asia/Shanghai</strong></div>
        <div class="schedule-copy-grid">
          <label>中文开启文案<input v-model.trim="scheduleDraft.countdown_copy.zh_opening" maxlength="100" required /></label>
          <label>中文关闭文案<input v-model.trim="scheduleDraft.countdown_copy.zh_closing" maxlength="100" required /></label>
          <label>英文开启文案<input v-model.trim="scheduleDraft.countdown_copy.en_opening" maxlength="100" required /></label>
          <label>英文关闭文案<input v-model.trim="scheduleDraft.countdown_copy.en_closing" maxlength="100" required /></label>
        </div>
        <button class="button primary" type="submit" :disabled="busy || !scheduleDirty"><Save :size="15"/>{{ savingSchedule ? '保存中…' : '保存时间段' }}</button>
        <p>支持精确到分钟和跨午夜，例如 22:30–08:15。开始时间生效，结束时间停用；后台每 {{ schedule.interval_seconds }} 秒检查一次。</p>
      </form>

      <p v-if="schedule?.error" class="notice error schedule-error">{{ schedule.error }}</p>

      <div v-if="groups.length" class="welfare-list">
        <div v-for="group in groups" :key="group.id" class="welfare-row">
          <div><strong>{{ group.name }}</strong><small>#{{ group.id }} · {{ group.status === 'active' ? '正常' : group.status === 'inactive' ? '已停用' : group.status || '状态未知' }}</small></div>
          <div class="welfare-copy">
            <select :value="copySourceByTarget[group.id] || ''" :disabled="busy" :aria-label="`选择复制到${group.name}的来源分组`" @change="copySourceByTarget[group.id] = Number($event.target.value) || null">
              <option value="">选择来源分组</option>
              <template v-for="source in sourceGroups" :key="source.id"><option v-if="source.id !== group.id" :value="source.id">{{ source.name }}（#{{ source.id }}）</option></template>
            </select>
            <button type="button" class="button welfare-copy-button" :disabled="busy || !copySourceByTarget[group.id]" @click="copyMembers(group)"><Copy :size="15"/>拷贝账号</button>
          </div>
          <button type="button" class="button welfare-remove" :disabled="busy" @click="openRemove(group)"><UserMinus :size="15"/>移除全部账号</button>
          <button type="button" class="welfare-switch" role="switch" :aria-checked="group.status === 'active'" :aria-label="`${group.status === 'active' ? '停用' : '启用'}${group.name}`" :title="schedule?.enabled ? '定时控制开启期间不能手动修改' : ''" :disabled="busy || schedule?.enabled || !['active', 'inactive'].includes(group.status)" @click="toggle(group)"><span/></button>
        </div>
      </div>
      <p v-else class="welfare-empty" role="status">{{ loading ? '正在读取福利分组…' : '没有名称中包含“福利”的 OpenAI 平台分组。' }}</p>
    </section>

    <dialog ref="removeDialog" class="modal welfare-remove-dialog" aria-labelledby="welfare-remove-title" @cancel="removing && $event.preventDefault()">
      <template v-if="removeTarget">
        <div class="modal-heading"><h2 id="welfare-remove-title">移除“{{ removeTarget.name }}”的全部账号</h2><button type="button" class="icon-button" :disabled="removing" aria-label="关闭移除账号确认" @click="removeDialog.close()"><X :size="18"/></button></div>
        <div class="modal-body">
          <template v-if="!removeOutcome">
            <p>将从 Sub2API 分组“{{ removeTarget.name }}”（#{{ removeTarget.id }}）解除下列账号的关联。账号的其它分组、调度开关和 FlowPool 归属保持不变，账号本身不会被删除。</p>
            <p v-if="membersLoading" class="small muted" role="status">正在读取分组账号…</p>
            <p v-else-if="members.length" class="small">共 {{ members.length }} 个账号</p>
            <p v-else-if="!removeError" class="small muted" role="status">该分组当前没有账号。</p>
            <div v-if="members.length" class="welfare-member-list"><div v-for="member in members" :key="member.id">{{ member.name }} <small class="muted">#{{ member.id }}</small></div></div>
            <p class="small muted">仅生图冷却保护或正在切换调度状态的账号会被跳过，并在结果中反馈。</p>
          </template>
          <template v-else>
            <p role="status">移除结果：更新 {{ removeOutcome.changed }} · 无需修改 {{ removeOutcome.unchanged }} · 跳过 {{ removeOutcome.skipped }} · 失败 / 未确认 {{ removeOutcome.failed }}</p>
            <div v-if="removeProblems.length" class="welfare-member-list"><div v-for="item in removeProblems" :key="item.id">{{ item.name }} <small class="muted">#{{ item.id }} · {{ item.error }}</small></div></div>
          </template>
          <p v-if="removeError" class="notice error" role="alert">{{ removeError }}</p>
          <p v-if="removing" class="notice" role="status">正在逐批移除并核对结果，请等待完成…</p>
        </div>
        <div class="modal-footer">
          <template v-if="!removeOutcome">
            <button type="button" class="button" :disabled="removing" @click="removeDialog.close()">取消</button>
            <button type="button" class="button primary" :disabled="removing || membersLoading || !members.length" @click="removeMembers">{{ removing ? '处理中…' : `移除 ${members.length} 个账号` }}</button>
          </template>
          <button v-else type="button" class="button" @click="removeDialog.close()">关闭</button>
        </div>
      </template>
    </dialog>
  </div>
</template>

<style scoped>
.promotion-campaigns { max-width: 76rem; margin-inline: auto; }
.promotion-heading { display: flex; align-items: center; justify-content: space-between; gap: 1.5rem; margin-bottom: 1.75rem; }
.promotion-heading h1 { font-size: 1.6rem; }
.promotion-heading p, .welfare-heading p { color: var(--muted); font-size: .875rem; }
.welfare-panel { overflow: hidden; }
.checkin-panel { overflow: hidden; margin-bottom: 1.25rem; }
.checkin-config { display: grid; grid-template-columns: minmax(15rem, 1fr) auto minmax(10rem, .6fr) minmax(10rem, .6fr) auto; align-items: end; gap: 1rem; padding: 1.25rem 1.5rem; border-top: 1px solid var(--border); background: color-mix(in srgb, var(--accent) 3%, Canvas); }
.checkin-config label { display: grid; gap: .4rem; color: var(--muted); font-size: .78rem; }
.checkin-config > p { grid-column: 1 / -1; margin: 0; color: var(--muted); font-size: .78rem; line-height: 1.6; }
.checkin-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; border-top: 1px solid var(--border); background: var(--border); }
.checkin-stat { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: .25rem .65rem; padding: 1rem 1.5rem; background: Canvas; }
.checkin-stat svg { grid-row: span 2; color: var(--accent); }
.checkin-stat span { color: var(--muted); font-size: .78rem; }
.checkin-stat strong { font-size: 1.3rem; }
.checkin-counts { padding: 1.2rem 1.5rem 1.4rem; border-top: 1px solid var(--border); }
.checkin-counts-heading { display: flex; justify-content: space-between; gap: 1rem; margin-bottom: .9rem; }
.checkin-counts-heading small { color: var(--muted); font-size: .78rem; }
.checkin-count-row { display: grid; grid-template-columns: 7rem 1fr 3rem; align-items: center; gap: .8rem; min-height: 2rem; font-size: .8rem; }
.checkin-count-row > div { height: .45rem; overflow: hidden; border-radius: 1rem; background: color-mix(in srgb, var(--accent) 10%, Canvas); }
.checkin-count-row i { display: block; height: 100%; border-radius: inherit; background: var(--accent); }
.checkin-count-row strong { text-align: right; }
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
.welfare-remove { flex-shrink: 0; white-space: nowrap; }
.welfare-copy { display: flex; align-items: center; gap: .5rem; flex-shrink: 0; }
.welfare-copy select { width: 13rem; }
.welfare-copy-button { white-space: nowrap; }
.welfare-remove-dialog { margin: auto; padding: 0; color: var(--text); width: min(560px, calc(100vw - 24px)); max-height: 90dvh; overflow-y: auto; }
.welfare-remove-dialog:not([open]) { display: none; }
.welfare-remove-dialog::backdrop { background: #21304a3b; }
.welfare-remove-dialog .modal-body { padding: 0 24px 20px; overflow-wrap: anywhere; }
.welfare-member-list { max-height: 240px; overflow: auto; margin-top: 12px; padding: 8px 10px; border-radius: 8px; background: color-mix(in srgb, var(--accent) 3%, Canvas); font-size: 13px; }
.welfare-member-list > div { padding: 5px 0; }
@media (max-width: 1050px) { .welfare-schedule { grid-template-columns: minmax(12rem, 1fr) auto 1fr 1fr; } .schedule-timezone { grid-column: 1 / 3; } }
@media (max-width: 600px) { .promotion-heading { align-items: flex-start; } .promotion-heading h1 { font-size: 1.35rem; } .welfare-heading, .welfare-row { padding-inline: 1.1rem; } .welfare-row { flex-wrap: wrap; } .welfare-row > div:first-child { flex-basis: 100%; } .welfare-copy { flex: 1; min-width: 0; } .welfare-copy select { flex: 1; width: auto; min-width: 0; } .welfare-icon { display: none; } .welfare-schedule, .checkin-config { grid-template-columns: 1fr auto; padding-inline: 1.1rem; } .welfare-schedule label, .checkin-config label { grid-column: span 1; } .schedule-timezone { grid-column: 1 / 2; } .schedule-copy-grid { grid-template-columns: 1fr; } .welfare-schedule > .button, .checkin-config > .button { grid-column: 1 / -1; } .checkin-summary { grid-template-columns: 1fr; } .checkin-counts { padding-inline: 1.1rem; } .checkin-count-row { grid-template-columns: 6.5rem 1fr 2rem; gap: .5rem; } }
</style>
