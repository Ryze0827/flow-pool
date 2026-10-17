<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { CalendarCheck, ChevronDown, Clock3, Copy, MoonStar, Plus, RefreshCw, Save, Trash2, UserMinus, Users, X } from 'lucide-vue-next'
import { api } from './api'
import { useRefreshCountdown } from './refreshCountdown'

const emit = defineEmits(['notify'])
const groups = ref([])
const sourceGroups = ref([])
const copySourceByTarget = ref({})
const rateDraft = ref({})
const sourceMenuGroupId = ref(null)
const sourceMenuPlacement = ref('down')
const loading = ref(false)
const updating = ref(null)
const savingSchedule = ref(false)
const savingCheckin = ref(false)
const rateUpdating = ref(null)
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
const activeTab = ref('checkin')
const checkinDraft = ref({ enabled: false, reward_tiers: [{ min_streak_days: 1, max_streak_days: 7, reward_amount: 1 }] })
const scheduleDraft = ref({ enabled: false, show_announcement: true, start_hour: 22, end_hour: 8, start_minute: 1320, end_minute: 480, timezone: 'Asia/Shanghai', countdown_copy: { zh_opening: '', zh_closing: '', en_opening: '', en_closing: '' } })
const enabledCount = computed(() => groups.value.filter(group => group.status === 'active').length)
const scheduleDirty = computed(() => schedule.value && JSON.stringify(scheduleDraft.value) !== JSON.stringify({ enabled: schedule.value.enabled, show_announcement: schedule.value.show_announcement, start_hour: schedule.value.start_hour, end_hour: schedule.value.end_hour, start_minute: schedule.value.start_minute, end_minute: schedule.value.end_minute, timezone: schedule.value.timezone, countdown_copy: schedule.value.countdown_copy }))
const checkinDirty = computed(() => checkin.value && JSON.stringify(checkinDraft.value) !== JSON.stringify(normalizeCheckinSettings(checkin.value.settings)))
const checkinPeak = computed(() => Math.max(1, checkin.value?.today_count || 0, ...(checkin.value?.daily_counts || []).map(item => item.count)))
const busy = computed(() => loading.value || updating.value !== null || rateUpdating.value !== null || savingSchedule.value || savingCheckin.value || removing.value || copying.value !== null)
const scheduleState = computed(() => !schedule.value?.enabled ? '未开启定时控制' : schedule.value.active_now ? '当前处于福利时段' : '当前不在福利时段')
const transitionText = computed(() => schedule.value?.next_transition_at ? new Date(schedule.value.next_transition_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '')
const refreshIntervalSeconds = 60
const { text: refreshCountdownText, reset: resetRefreshCountdown } = useRefreshCountdown(refreshIntervalSeconds)
let polling

function defaultCheckinTier() {
  return { min_streak_days: 1, max_streak_days: 7, reward_amount: 1 }
}

function normalizeCheckinSettings(settings) {
  const rewardTiers = settings.reward_tiers?.length ? settings.reward_tiers : [{
    min_streak_days: settings.min_streak_days ?? 1,
    max_streak_days: settings.max_streak_days ?? 7,
    reward_amount: settings.reward_amount ?? 1,
  }]
  return { enabled: settings.enabled, reward_tiers: rewardTiers.map(tier => ({ ...tier })) }
}

async function load() {
  if (loading.value) return
  resetRefreshCountdown()
  loading.value = true
  error.value = ''
  try {
    const result = await api('/promotions/welfare-groups')
    groups.value = result.items
    sourceGroups.value = result.source_groups || result.items
    rateDraft.value = Object.fromEntries(result.items.map(group => [group.id, group.rate_multiplier ?? 1]))
    schedule.value = result.schedule
    scheduleDraft.value = { enabled: result.schedule.enabled, show_announcement: result.schedule.show_announcement, start_hour: result.schedule.start_hour, end_hour: result.schedule.end_hour, start_minute: result.schedule.start_minute, end_minute: result.schedule.end_minute, timezone: result.schedule.timezone, countdown_copy: { ...result.schedule.countdown_copy } }
    checkin.value = await api('/promotions/check-in')
    checkinDraft.value = normalizeCheckinSettings(checkin.value.settings)
  }
  catch (cause) { error.value = cause.message }
  finally { loading.value = false }
}

async function saveCheckin() {
  if (savingCheckin.value || !checkinDirty.value) return
  const rewardTiers = checkinDraft.value.reward_tiers.map(tier => ({
    min_streak_days: Number(tier.min_streak_days),
    max_streak_days: Number(tier.max_streak_days),
    reward_amount: Number(tier.reward_amount),
  })).sort((left, right) => left.min_streak_days - right.min_streak_days)
  if (!rewardTiers.length || rewardTiers.length > 20 || rewardTiers.some(tier =>
    !Number.isInteger(tier.min_streak_days) || !Number.isInteger(tier.max_streak_days) ||
    tier.min_streak_days < 1 || tier.max_streak_days < tier.min_streak_days || tier.max_streak_days > 3650 ||
    !Number.isFinite(tier.reward_amount) || tier.reward_amount <= 0 || tier.reward_amount > 10000 ||
    Math.abs(tier.reward_amount * 10000 - Math.round(tier.reward_amount * 10000)) > 1e-7) ||
    rewardTiers.some((tier, index) => index > 0 && tier.min_streak_days <= rewardTiers[index - 1].max_streak_days)) {
    error.value = '请输入不重叠的连续签到奖励区间和有效额度'
    return
  }
  savingCheckin.value = true
  error.value = ''
  try {
    await api('/promotions/check-in', 'PUT', { enabled: checkinDraft.value.enabled, reward_tiers: rewardTiers })
    await load()
    emit('notify', '签到活动配置已保存')
  } catch (cause) { await reloadAfterFailure(cause.message) }
  finally { savingCheckin.value = false }
}

async function toggleCheckin() {
  if (savingCheckin.value || !checkin.value) return
  const enabled = !checkin.value.settings.enabled
  savingCheckin.value = true
  error.value = ''
  checkinDraft.value.enabled = enabled
  try {
    await api('/promotions/check-in', 'PUT', { enabled, reward_tiers: normalizeCheckinSettings(checkin.value.settings).reward_tiers })
    await load()
    emit('notify', `签到活动已${enabled ? '开启' : '关闭'}`)
  } catch (cause) { await reloadAfterFailure(cause.message) }
  finally { savingCheckin.value = false }
}

function addCheckinTier() {
  if (busy.value || checkinDraft.value.reward_tiers.length >= 20) return
  const last = checkinDraft.value.reward_tiers.at(-1)
  const start = Math.min(3650, Number(last?.max_streak_days || 0) + 1)
  checkinDraft.value.reward_tiers.push({ min_streak_days: start, max_streak_days: Math.min(3650, start + 6), reward_amount: 1 })
}

function removeCheckinTier(index) {
  if (busy.value || checkinDraft.value.reward_tiers.length <= 1) return
  checkinDraft.value.reward_tiers.splice(index, 1)
}

function selectTab(tab) {
  activeTab.value = tab
  sourceMenuGroupId.value = null
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

function rateChanged(group) {
  return Number(rateDraft.value[group.id]) !== Number(group.rate_multiplier ?? 1)
}

async function saveRate(group) {
  if (rateUpdating.value !== null || !rateChanged(group)) return
  const rate = Number(rateDraft.value[group.id])
  if (!Number.isFinite(rate) || rate < 0 || rate > 1000 || Math.round(rate * 10000) !== rate * 10000) {
    error.value = '倍率必须是 0 到 1000 之间且最多 4 位小数'
    return
  }
  rateUpdating.value = group.id
  error.value = ''
  try {
    const result = await api(`/promotions/welfare-groups/${group.id}/rate`, 'PUT', { rate_multiplier: rate })
    if (!result.verified) throw new Error(result.error || '福利分组倍率未确认，请刷新后重试')
    group.rate_multiplier = result.rate_multiplier
    rateDraft.value[group.id] = result.rate_multiplier
    emit('notify', `${group.name}倍率已更新为 ${result.rate_multiplier}×`)
  } catch (cause) {
    const message = cause.message
    await load()
    error.value = message
  } finally { rateUpdating.value = null }
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
  } catch (cause) { await reloadAfterFailure(cause.message) }
  finally { savingSchedule.value = false }
}

async function toggleSchedule() {
  if (savingSchedule.value || !schedule.value) return
  const enabled = !schedule.value.enabled
  savingSchedule.value = true
  error.value = ''
  scheduleDraft.value.enabled = enabled
  try {
    const result = await api('/promotions/welfare-schedule', 'PUT', {
      enabled,
      show_announcement: schedule.value.show_announcement,
      start_hour: schedule.value.start_hour,
      end_hour: schedule.value.end_hour,
      start_minute: schedule.value.start_minute,
      end_minute: schedule.value.end_minute,
      timezone: schedule.value.timezone,
      countdown_copy: { ...schedule.value.countdown_copy },
    })
    await load()
    emit('notify', result.error ? '定时控制已切换，分组状态将在下轮自动重试' : `福利分组定时控制已${enabled ? '开启' : '关闭'}`, Boolean(result.error))
  } catch (cause) { await reloadAfterFailure(cause.message) }
  finally { savingSchedule.value = false }
}

async function toggleAnnouncement() {
  if (savingSchedule.value || !schedule.value) return
  const showAnnouncement = !schedule.value.show_announcement
  savingSchedule.value = true
  error.value = ''
  scheduleDraft.value.show_announcement = showAnnouncement
  try {
    const result = await api('/promotions/welfare-schedule', 'PUT', {
      enabled: schedule.value.enabled,
      show_announcement: showAnnouncement,
      start_hour: schedule.value.start_hour,
      end_hour: schedule.value.end_hour,
      start_minute: schedule.value.start_minute,
      end_minute: schedule.value.end_minute,
      timezone: schedule.value.timezone,
      countdown_copy: { ...schedule.value.countdown_copy },
    })
    await load()
    emit('notify', result.error ? '公告配置已切换，分组状态将在下轮自动重试' : `erxinai 公告已${showAnnouncement ? '开启' : '关闭'}`, Boolean(result.error))
  } catch (cause) { await reloadAfterFailure(cause.message) }
  finally { savingSchedule.value = false }
}

async function reloadAfterFailure(message) {
  try { await load(); error.value = message }
  catch (cause) { error.value = cause.message }
}

function toggleSourceMenu(groupId, event) {
  if (busy.value) return
  if (sourceMenuGroupId.value === groupId) {
    sourceMenuGroupId.value = null
    return
  }
  const rect = event.currentTarget.getBoundingClientRect()
  sourceMenuPlacement.value = rect.bottom + 280 > window.innerHeight ? 'up' : 'down'
  sourceMenuGroupId.value = groupId
}

function sourceLabel(groupId) {
  const selected = sourceGroups.value.find(source => source.id === Number(copySourceByTarget.value[groupId]))
  return selected ? `${selected.name}（#${selected.id}）` : '选择来源分组'
}

function selectCopySource(groupId, sourceId) {
  copySourceByTarget.value[groupId] = sourceId
  sourceMenuGroupId.value = null
}

function closeSourceMenu(event) {
  if (!event.target.closest('.source-picker')) sourceMenuGroupId.value = null
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
  document.addEventListener('click', closeSourceMenu)
  polling = setInterval(() => {
    if (!busy.value && !scheduleDirty.value) void load()
  }, 60000)
})
onUnmounted(() => {
  clearInterval(polling)
  document.removeEventListener('click', closeSourceMenu)
})
</script>

<template>
  <div class="promotion-campaigns">
    <header class="promotion-heading">
      <div><h1>促销活动</h1><p>管理促销活动关联的 GPT / OpenAI 分组状态。</p></div>
      <button class="button" :disabled="busy" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }"/>刷新数据 · <span class="refresh-countdown">{{ refreshCountdownText }}</span></button>
    </header>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>

    <nav class="promotion-tabs" role="tablist" aria-label="促销活动类型">
      <button type="button" class="promotion-tab" :class="{ active: activeTab === 'checkin' }" role="tab" aria-controls="checkin-panel" :aria-selected="activeTab === 'checkin'" @click="selectTab('checkin')"><CalendarCheck :size="16"/>每日签到</button>
      <button type="button" class="promotion-tab" :class="{ active: activeTab === 'welfare' }" role="tab" aria-controls="welfare-panel" :aria-selected="activeTab === 'welfare'" @click="selectTab('welfare')"><MoonStar :size="16"/>夜间福利分组<span v-if="groups.length" class="promotion-tab-count">{{ groups.length }}</span></button>
    </nav>

    <section v-if="activeTab === 'checkin'" id="checkin-panel" class="panel checkin-panel" aria-labelledby="checkin-title" role="tabpanel">
      <div class="welfare-heading">
        <div class="welfare-icon"><CalendarCheck :size="21"/></div>
        <div><h2 id="checkin-title">每日签到</h2><p>记录 erxinai 用户每日签到，并按 Asia/Shanghai 统计每天参与人数。</p></div>
        <span v-if="checkin" class="badge" :class="checkin.settings.enabled ? 'success' : 'neutral'">{{ checkin.settings.enabled ? '活动进行中' : '未开启' }}</span>
      </div>
      <form v-if="checkin" class="checkin-config" @submit.prevent="saveCheckin">
        <div class="schedule-toggle-copy"><CalendarCheck :size="18"/><div><strong>开启签到活动</strong><small>{{ checkin.settings.enabled ? '用户可以在 erxinai 顶部签到' : '开启后用户即可签到' }}</small></div></div>
        <button type="button" class="welfare-switch" role="switch" :aria-checked="checkinDraft.enabled" aria-label="开启签到活动" :disabled="busy" @click="toggleCheckin"><span/></button>
        <div class="checkin-tiers">
          <div class="checkin-tiers-heading"><div><strong>连续签到奖励区间</strong><small>按连续签到天数匹配奖励额度，区间不能重叠。</small></div><button type="button" class="button" :disabled="busy || checkinDraft.reward_tiers.length >= 20" @click="addCheckinTier"><Plus :size="15"/>新增区间</button></div>
          <div v-for="(tier, index) in checkinDraft.reward_tiers" :key="index" class="checkin-tier-row">
            <label>最少天数<input v-model.number="tier.min_streak_days" type="number" min="1" max="3650" required :disabled="busy" /></label>
            <label>最多天数<input v-model.number="tier.max_streak_days" type="number" min="1" max="3650" required :disabled="busy" /></label>
            <label>奖励额度<input v-model.number="tier.reward_amount" type="number" min="0.0001" max="10000" step="0.0001" required :disabled="busy" /></label>
            <button type="button" class="icon-button checkin-tier-remove" :disabled="busy || checkinDraft.reward_tiers.length <= 1" aria-label="删除此奖励区间" title="删除此奖励区间" @click="removeCheckinTier(index)"><Trash2 :size="15"/></button>
          </div>
        </div>
        <button class="button primary" type="submit" :disabled="busy || !checkinDirty"><Save :size="15"/> {{ savingCheckin ? '保存中…' : '保存签到配置' }}</button>
        <p>例如 1–3 天奖励 1，4–7 天奖励 2；签到记录会按用户和日期去重。</p>
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

    <section v-else id="welfare-panel" class="panel welfare-panel" aria-labelledby="welfare-title" role="tabpanel">
      <div class="welfare-heading">
        <div class="welfare-icon"><MoonStar :size="21"/></div>
        <div><h2 id="welfare-title">夜间福利分组</h2><p>名称中包含“福利”的 OpenAI 平台分组，开关直接对应分组的启用状态。</p></div>
        <span v-if="groups.length" class="badge neutral">{{ enabledCount }} / {{ groups.length }} 已启用</span>
      </div>

      <form v-if="schedule" class="welfare-schedule" @submit.prevent="saveSchedule">
        <div class="schedule-overview">
          <div class="schedule-toggle-copy"><span class="schedule-control-icon"><Clock3 :size="17"/></span><div><strong>定时控制</strong><small>{{ scheduleState }}<template v-if="transitionText"> · 下次切换 {{ transitionText }}</template></small></div></div>
          <button type="button" class="welfare-switch" role="switch" :aria-checked="scheduleDraft.enabled" aria-label="启用福利分组定时控制" :disabled="busy" @click="toggleSchedule"><span/></button>
          <label class="schedule-announcement-toggle"><span>erxinai 公告</span><button type="button" class="welfare-switch" role="switch" :aria-checked="scheduleDraft.show_announcement" aria-label="在 erxinai 展示福利分组公告" :disabled="busy" @click="toggleAnnouncement"><span/></button></label>
        </div>
        <div class="schedule-time-fields">
          <label>开始时间<input type="time" :value="minuteText(scheduleDraft.start_minute)" required :disabled="busy" @input="setMinute('start', $event.target.value)" /></label>
          <label>结束时间<input type="time" :value="minuteText(scheduleDraft.end_minute)" required :disabled="busy" @input="setMinute('end', $event.target.value)" /></label>
          <div class="schedule-timezone"><small>时区</small><strong>Asia/Shanghai</strong></div>
        </div>
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
          <div class="welfare-rate">
            <label>倍率<input v-model.number="rateDraft[group.id]" type="number" min="0" max="1000" step="0.0001" inputmode="decimal" :disabled="busy" :aria-label="`${group.name}倍率`" @keydown.enter.prevent="saveRate(group)" /></label>
            <button type="button" class="icon-button" :disabled="busy || !rateChanged(group)" :aria-label="`保存${group.name}倍率`" :title="`保存${group.name}倍率`" @click="saveRate(group)"><Save :size="15"/></button>
          </div>
          <div class="welfare-copy">
            <div class="source-picker">
              <button type="button" class="source-picker-trigger" :disabled="busy" :aria-expanded="sourceMenuGroupId === group.id" aria-haspopup="listbox" :aria-label="`选择复制到${group.name}的来源分组`" @click="toggleSourceMenu(group.id, $event)"><span>{{ sourceLabel(group.id) }}</span><ChevronDown :size="15" :class="{ rotated: sourceMenuGroupId === group.id }"/></button>
              <div v-if="sourceMenuGroupId === group.id" class="source-menu" :class="`source-menu-${sourceMenuPlacement}`" role="listbox" :aria-label="`选择复制到${group.name}的来源分组`">
                <button type="button" class="source-option" :class="{ selected: !copySourceByTarget[group.id] }" role="option" :aria-selected="!copySourceByTarget[group.id]" @click="selectCopySource(group.id, null)">选择来源分组</button>
                <template v-for="source in sourceGroups" :key="source.id"><button v-if="source.id !== group.id" type="button" class="source-option" :class="{ selected: copySourceByTarget[group.id] === source.id }" role="option" :aria-selected="copySourceByTarget[group.id] === source.id" @click="selectCopySource(group.id, source.id)">{{ source.name }}（#{{ source.id }}）</button></template>
              </div>
            </div>
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
.promotion-tabs { display: flex; gap: .35rem; margin-bottom: 1.25rem; overflow-x: auto; border-bottom: 1px solid var(--border); }
.promotion-tab { display: inline-flex; align-items: center; gap: .45rem; flex: 0 0 auto; padding: .75rem 1rem; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--muted); font: inherit; font-size: .88rem; white-space: nowrap; cursor: pointer; }
.promotion-tab:hover { color: var(--text); background: color-mix(in srgb, var(--accent) 5%, var(--surface)); }
.promotion-tab.active { border-bottom-color: var(--accent); color: var(--accent); font-weight: 600; }
.promotion-tab:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.promotion-tab-count { display: inline-grid; min-width: 1.25rem; height: 1.25rem; padding-inline: .3rem; place-items: center; border-radius: 999px; background: color-mix(in srgb, var(--accent) 10%, var(--surface)); color: var(--accent); font-size: .72rem; font-variant-numeric: tabular-nums; }
.welfare-panel { overflow: visible; }
.checkin-panel { overflow: hidden; margin-bottom: 1.25rem; }
.checkin-config { display: grid; grid-template-columns: minmax(15rem, 1fr) auto; align-items: end; gap: 1rem; padding: 1.25rem 1.5rem; border-top: 1px solid var(--border); background: color-mix(in srgb, var(--accent) 3%, var(--surface)); }
.checkin-config label { display: grid; gap: .4rem; color: var(--muted); font-size: .78rem; }
.checkin-config > p { grid-column: 1 / -1; margin: 0; color: var(--muted); font-size: .78rem; line-height: 1.6; }
.checkin-tiers { grid-column: 1 / -1; display: grid; gap: .7rem; width: 100%; }
.checkin-tiers-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.checkin-tiers-heading > div { display: grid; gap: .25rem; }
.checkin-tiers-heading small { color: var(--muted); font-size: .78rem; }
.checkin-tier-row { display: grid; grid-template-columns: repeat(3, minmax(8rem, 1fr)) auto; align-items: end; gap: .8rem; padding: .8rem; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.checkin-tier-remove { margin-bottom: .1rem; color: var(--muted); }
.checkin-tier-remove:hover:not(:disabled) { color: #b42318; }
.checkin-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; border-top: 1px solid var(--border); background: var(--border); }
.checkin-stat { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: .25rem .65rem; padding: 1rem 1.5rem; background: var(--surface); }
.checkin-stat svg { grid-row: span 2; color: var(--accent); }
.checkin-stat span { color: var(--muted); font-size: .78rem; }
.checkin-stat strong { font-size: 1.3rem; }
.checkin-counts { padding: 1.2rem 1.5rem 1.4rem; border-top: 1px solid var(--border); }
.checkin-counts-heading { display: flex; justify-content: space-between; gap: 1rem; margin-bottom: .9rem; }
.checkin-counts-heading small { color: var(--muted); font-size: .78rem; }
.checkin-count-row { display: grid; grid-template-columns: 7rem 1fr 3rem; align-items: center; gap: .8rem; min-height: 2rem; font-size: .8rem; }
.checkin-count-row > div { height: .45rem; overflow: hidden; border-radius: 1rem; background: color-mix(in srgb, var(--accent) 10%, var(--surface)); }
.checkin-count-row i { display: block; height: 100%; border-radius: inherit; background: var(--accent); }
.checkin-count-row strong { text-align: right; }
.welfare-heading { display: flex; align-items: center; gap: 1rem; padding: 1.5rem; }
.welfare-heading > div:nth-child(2) { flex: 1; min-width: 0; }
.welfare-heading h2 { font-size: 1rem; }
.welfare-icon { display: grid; place-items: center; width: 2.75rem; height: 2.75rem; flex-shrink: 0; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border-radius: .75rem; }
.welfare-schedule { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(22rem, .95fr); gap: 1.25rem 1.5rem; padding: 1.35rem 1.5rem 1.5rem; border-top: 1px solid var(--border); background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 4%, var(--surface)), var(--surface)); }
.schedule-overview, .schedule-time-fields { display: flex; align-items: center; min-width: 0; padding: .85rem 1rem; border: 1px solid color-mix(in srgb, var(--accent) 15%, var(--border)); border-radius: .8rem; background: color-mix(in srgb, var(--surface) 88%, var(--accent)); }
.schedule-overview { gap: .8rem; }
.schedule-toggle-copy { display: flex; align-items: center; gap: .7rem; min-width: 0; flex: 1; }
.schedule-control-icon { display: grid; place-items: center; width: 2.15rem; height: 2.15rem; flex: 0 0 auto; border-radius: .65rem; color: var(--accent); background: color-mix(in srgb, var(--accent) 11%, var(--surface)); }
.schedule-toggle-copy > svg { flex-shrink: 0; color: var(--accent); }
.schedule-toggle-copy strong, .schedule-toggle-copy small { display: block; }
.schedule-toggle-copy small { margin-top: .3rem; color: var(--muted); font-size: .78rem; line-height: 1.5; }
.welfare-schedule label { display: grid; gap: .4rem; color: var(--muted); font-size: .78rem; }
.welfare-schedule select { min-width: 0; }
.schedule-timezone { padding-left: .85rem; border-left: 1px solid var(--border); white-space: nowrap; }
.schedule-timezone small, .schedule-timezone strong { display: block; }
.schedule-timezone small { margin-bottom: .35rem; color: var(--muted); font-size: .78rem; }
.schedule-timezone strong { font-size: .82rem; }
.schedule-announcement-toggle { display: flex !important; align-items: center; gap: .6rem; padding-left: .9rem; border-left: 1px solid var(--border); white-space: nowrap; }
.schedule-time-fields { gap: .75rem; }
.schedule-time-fields > label { flex: 1; }
.schedule-copy-grid { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.schedule-copy-grid input { width: 100%; }
.welfare-schedule > .button { grid-column: 1 / -1; justify-self: start; }
.welfare-schedule > p { grid-column: 1 / -1; margin: 0; color: var(--muted); font-size: .78rem; line-height: 1.6; }
.schedule-error { margin: 0 1.5rem 1.25rem; }
.welfare-list { display: grid; gap: .65rem; padding: 1rem 1.5rem 1.5rem; border-top: 1px solid var(--border); background: #fbfcfe; }
.welfare-row { display: grid; grid-template-columns: minmax(12rem, 1.15fr) auto minmax(18rem, 1fr) auto auto; align-items: center; gap: 1rem; min-height: 4.8rem; padding: .95rem 1rem; border: 1px solid var(--border); border-radius: .8rem; background: var(--surface); box-shadow: 0 2px 8px #24324708; transition: border-color 160ms ease-out, box-shadow 160ms ease-out; }
.welfare-row:hover { border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); box-shadow: 0 5px 16px #24324712; }
.welfare-row > div { min-width: 0; }
.welfare-row strong, .welfare-row small { display: block; overflow-wrap: anywhere; }
.welfare-row strong { font-size: .9rem; }
.welfare-row small { margin-top: .35rem; color: var(--muted); font-size: .8rem; }
.welfare-rate { display: flex; align-items: end; gap: .45rem; flex-shrink: 0; }
.welfare-rate label { display: grid; gap: .35rem; color: var(--muted); font-size: .75rem; }
.welfare-rate input { width: 6.5rem; padding: 8px 9px; font-variant-numeric: tabular-nums; }
.welfare-rate .icon-button { margin-bottom: 1px; border: 1px solid var(--border); }
.welfare-switch { width: 2.8rem; height: 1.6rem; padding: .2rem; border: 0; border-radius: 2rem; background: var(--control-border); flex-shrink: 0; }
.welfare-switch span { display: block; width: 1.2rem; height: 1.2rem; border-radius: 50%; background: var(--surface); }
.welfare-switch[aria-checked='true'] { background: var(--accent); }
.welfare-switch[aria-checked='true'] span { transform: translateX(1.2rem); }
.welfare-switch:disabled { opacity: .55; cursor: not-allowed; }
.welfare-empty { margin: 0; padding: 2.5rem 1.5rem; border-top: 1px solid var(--border); color: var(--muted); text-align: center; font-size: .875rem; }
.welfare-remove { flex-shrink: 0; white-space: nowrap; }
.welfare-copy { display: flex; align-items: center; gap: .5rem; flex-shrink: 0; }
.source-picker { position: relative; min-width: 0; }
.source-picker-trigger { display: flex; align-items: center; justify-content: space-between; gap: .6rem; width: 100%; min-height: 38px; padding: 9px 10px 9px 13px; border: 1px solid #e0e6ef; border-radius: 10px; background: #fff; color: var(--text); text-align: left; font-size: 13px; }
.source-picker-trigger:hover:not(:disabled) { border-color: #b7c8da; background: #fbfcfe; }
.source-picker-trigger:disabled { background: #f7f9fc; color: var(--muted); cursor: not-allowed; }
.source-picker-trigger > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-picker-trigger > svg { flex-shrink: 0; color: var(--muted); transition: transform .15s; }
.source-picker-trigger > svg.rotated { transform: rotate(180deg); }
.source-menu { position: absolute; left: 0; z-index: 20; display: grid; width: min(19rem, calc(100vw - 2.2rem)); max-height: min(18rem, 50dvh); overflow-y: auto; padding: .35rem; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); box-shadow: 0 10px 28px #24324724; }
.source-menu-down { top: calc(100% + .35rem); }
.source-menu-up { bottom: calc(100% + .35rem); }
.source-option { width: 100%; min-width: 0; padding: .6rem .7rem; border: 0; border-radius: 7px; background: transparent; color: var(--text); text-align: left; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }
.source-option:hover, .source-option.selected { background: color-mix(in srgb, var(--accent) 8%, var(--surface)); color: var(--accent); }
.welfare-copy-button { white-space: nowrap; }
.welfare-remove-dialog { margin: auto; padding: 0; color: var(--text); width: min(560px, calc(100vw - 24px)); max-height: 90dvh; overflow-y: auto; }
.welfare-remove-dialog:not([open]) { display: none; }
.welfare-remove-dialog::backdrop { background: #21304a3b; }
.welfare-remove-dialog .modal-body { padding: 0 24px 20px; overflow-wrap: anywhere; }
.welfare-member-list { max-height: 240px; overflow: auto; margin-top: 12px; padding: 8px 10px; border-radius: 8px; background: color-mix(in srgb, var(--accent) 3%, var(--surface)); font-size: 13px; }
.welfare-member-list > div { padding: 5px 0; }
@media (max-width: 1050px) { .welfare-schedule { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .promotion-heading { align-items: flex-start; } .promotion-heading h1 { font-size: 1.35rem; } .welfare-heading, .welfare-row { padding-inline: 1.1rem; } .welfare-list { padding-inline: 1.1rem; } .welfare-row { grid-template-columns: 1fr auto; } .welfare-row > div:first-child { grid-column: 1 / -1; } .welfare-rate { justify-self: start; } .welfare-copy { grid-column: 1 / -1; min-width: 0; } .source-picker { flex: 1; } .welfare-icon { display: none; } .welfare-schedule, .checkin-config { grid-template-columns: 1fr auto; padding-inline: 1.1rem; } .welfare-schedule { display: grid; } .schedule-overview, .schedule-time-fields { grid-column: 1 / -1; } .schedule-overview { align-items: flex-start; flex-wrap: wrap; } .schedule-announcement-toggle { margin-left: 2.85rem; padding-left: 0; border-left: 0; } .schedule-time-fields { align-items: end; } .schedule-timezone { grid-column: 1 / -1; padding: .65rem 0 0; border-left: 0; border-top: 1px solid var(--border); } .welfare-schedule label, .checkin-config label { grid-column: span 1; } .checkin-tier-row { grid-template-columns: 1fr 1fr; } .checkin-tier-row label:nth-child(3) { grid-column: 1 / 2; } .checkin-tier-remove { grid-column: 2; grid-row: 2; justify-self: end; } .schedule-copy-grid { grid-template-columns: 1fr; } .welfare-schedule > .button, .checkin-config > .button { grid-column: 1 / -1; } .checkin-summary { grid-template-columns: 1fr; } .checkin-counts { padding-inline: 1.1rem; } .checkin-count-row { grid-template-columns: 6.5rem 1fr 2rem; gap: .5rem; } }
</style>
