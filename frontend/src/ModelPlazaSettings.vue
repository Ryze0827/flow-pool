<script setup>
import { computed, onMounted, ref } from 'vue'
import { Check, EyeOff, FileText, Layers3, LoaderCircle, RefreshCw, Save, ShieldCheck } from 'lucide-vue-next'
import { api } from './api'
const emit = defineEmits(['notify'])
const settings = ref(null)
const hiddenGroups = ref('')
const busy = ref(false)
const error = ref('')
const saved = ref('')
const snapshot = () => JSON.stringify({ ...settings.value, hidden_group_ids: hiddenGroups.value.split(/[,，\s]+/).filter(Boolean).map(Number) })
const dirty = computed(() => settings.value && snapshot() !== saved.value)
async function load() {
  busy.value = true
  error.value = ''
  try {
    settings.value = await api('/model-plaza/settings')
    hiddenGroups.value = settings.value.hidden_group_ids.join(', ')
    saved.value = snapshot()
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function save() {
  error.value = ''
  const values = hiddenGroups.value.split(/[,，\s]+/).filter(Boolean)
  if (values.some(value => !/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)))) {
    error.value = '分组 ID 必须是正整数，以逗号分隔'; return
  }
  busy.value = true
  try {
    settings.value = await api('/model-plaza/settings', 'PUT', { ...settings.value, hidden_group_ids: values.map(Number) })
    hiddenGroups.value = settings.value.hidden_group_ids.join(', ')
    saved.value = snapshot()
    emit('notify', '模型广场配置已保存')
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <div class="plaza-settings">
    <header class="plaza-heading">
      <div><h1>模型广场</h1><p>管理模型广场的开放状态、价格说明与展示范围。</p></div>
      <button class="button" :disabled="busy" @click="load"><RefreshCw :size="15" :class="{ spinning: busy }"/>刷新配置</button>
    </header>
    <p v-if="error" role="alert" class="notice error">{{ error }}</p>
    <section v-if="!settings" class="panel plaza-loading" role="status">
      <LoaderCircle v-if="busy" :size="22" class="spinning"/>
      <span>{{ busy ? '正在读取模型广场配置…' : '配置加载失败，请点击刷新重试。' }}</span>
    </section>
    <form v-else @submit.prevent="save" class="plaza-layout">
      <div class="plaza-editor">
        <section class="panel plaza-availability">
          <div class="plaza-section-icon"><Layers3 :size="21"/></div>
          <div class="plaza-availability-copy"><h2>开放模型广场 <span :class="['badge', settings.enabled ? 'success' : 'neutral']">{{ settings.enabled ? '已开启' : '未开启' }}</span></h2><p>让已登录用户浏览可用模型与价格。</p></div>
          <button type="button" class="plaza-switch" role="switch" :aria-checked="settings.enabled" aria-label="启用模型广场" :disabled="busy" @click="settings.enabled = !settings.enabled"><span/></button>
        </section>
        <section class="panel plaza-card">
          <div class="plaza-section-heading"><FileText :size="18"/><div><h2>价格说明</h2><p>展示在模型广场顶部，帮助用户了解计费规则。</p></div><span class="badge neutral">Markdown</span></div>
          <label for="plaza-description" class="plaza-label">说明内容 <span>选填</span></label>
          <textarea id="plaza-description" v-model="settings.description" class="plaza-textarea" :disabled="busy" rows="9" maxlength="20000" placeholder="例如：价格以美元计费，Token 单价按每百万 Token 展示。&#10;&#10;可在此补充计费规则、优惠活动或使用说明。" aria-describedby="plaza-description-help"/>
          <div class="plaza-input-meta"><span id="plaza-description-help">留空时，沿用上游的价格说明。</span><span>{{ settings.description.length.toLocaleString() }} / 20,000</span></div>
        </section>
        <section class="panel plaza-card">
          <div class="plaza-section-heading"><EyeOff :size="18"/><div><h2>展示范围</h2><p>将指定分组从模型广场中隐藏。</p></div></div>
          <label for="plaza-hidden-groups" class="plaza-label">隐藏分组 ID <span>选填</span></label>
          <input id="plaza-hidden-groups" v-model="hiddenGroups" class="plaza-group-input" :disabled="busy" placeholder="例如：2, 5, 12" aria-describedby="plaza-groups-help"/>
          <p id="plaza-groups-help" class="plaza-help"><span class="mobile-full-copy">多个 ID 用逗号分隔。留空展示用户有权查看的全部分组。</span><span class="mobile-brief-copy">ID 用逗号分隔；留空不隐藏分组。</span></p>
        </section>
        <footer class="plaza-save-bar">
          <span class="plaza-save-status" role="status"><span v-if="dirty" class="dot"/><Check v-else :size="16"/>{{ dirty ? '有未保存的更改' : '配置已同步' }}</span>
          <button class="button primary" type="submit" :disabled="busy || !dirty"><LoaderCircle v-if="busy" :size="16" class="spinning"/><Save v-else :size="16"/>{{ busy ? '保存中…' : '保存配置' }}</button>
        </footer>
      </div>
      <aside class="panel plaza-guide">
        <div class="plaza-guide-heading"><ShieldCheck :size="19"/><h2>展示规则</h2></div>
        <div><h3>价格保持一致</h3><p>模型与价格实时读取 Sub2API，包含用户专属倍率。</p></div>
        <div><h3>权限不受影响</h3><p>隐藏分组只改变广场展示，不影响用户调用权限和实际计费。</p></div>
        <div><h3>登录后可见</h3><p>用户登录 erxinai 后，可从「分组状态」下方进入模型广场。</p></div>
        <p class="plaza-prerequisite">使用前，请确认 Sub2API 已支持并启用模型广场接口。</p>
      </aside>
    </form>
  </div>
</template>

<style scoped>
.plaza-settings { max-width: 76rem; margin-inline: auto; }
.plaza-heading { display: flex; align-items: center; justify-content: space-between; gap: 1.5rem; margin-bottom: 1.75rem; }
.plaza-heading h1 { font-size: 1.6rem; }
.plaza-heading p, .plaza-availability p, .plaza-section-heading p, .plaza-guide p { color: var(--muted); font-size: .875rem; }
.plaza-layout { display: grid; grid-template-columns: minmax(0, 1fr) 19rem; gap: 1.5rem; align-items: start; }
.plaza-editor { display: grid; gap: 1.25rem; min-width: 0; }
.plaza-availability { display: flex; align-items: center; gap: 1rem; padding: 1.5rem; }
.plaza-section-icon { display: grid; place-items: center; width: 2.75rem; height: 2.75rem; flex-shrink: 0; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border-radius: .75rem; }
.plaza-availability-copy { flex: 1; }
.plaza-availability h2 { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; }
.plaza-settings h2 { font-size: 1rem; }
.plaza-switch { width: 2.8rem; height: 1.6rem; padding: .2rem; border: 0; border-radius: 2rem; background: var(--control-border); flex-shrink: 0; }
.plaza-switch span { display: block; width: 1.2rem; height: 1.2rem; border-radius: 50%; background: var(--surface); }
.plaza-switch[aria-checked='true'] { background: var(--accent); }
.plaza-switch[aria-checked='true'] span { transform: translateX(1.2rem); }
.plaza-card { padding: 1.5rem; }
.plaza-section-heading { display: flex; align-items: flex-start; gap: .75rem; margin-bottom: 1.5rem; }
.plaza-section-heading > svg { color: var(--accent); margin-top: .2rem; flex-shrink: 0; }
.plaza-section-heading > .badge { margin-left: auto; }
.plaza-label { display: flex; align-items: center; gap: .5rem; font-size: .875rem; font-weight: 500; margin-bottom: .65rem; }
.plaza-label span { color: var(--muted); font-size: .8rem; font-weight: 400; }
.plaza-textarea { display: block; width: 100%; min-height: 14rem; resize: vertical; padding: 1rem; color: var(--text); background: var(--surface); border: 1px solid var(--control-border); border-radius: .6rem; line-height: 1.8; font-size: .875rem; }
.plaza-textarea::placeholder { color: var(--muted); opacity: .75; }
.plaza-textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.plaza-input-meta { display: flex; justify-content: space-between; flex-wrap: wrap; gap: .5rem; margin-top: .65rem; font-size: .8rem; color: var(--muted); }
.plaza-group-input { width: 100%; }
.plaza-help { font-size: .8rem; color: var(--muted); margin-top: .65rem; }
.plaza-guide { padding: 1.5rem; }
.plaza-guide-heading { display: flex; align-items: center; gap: .6rem; color: var(--accent); margin-bottom: 1.5rem; }
.plaza-guide > div + div { margin-top: 1.4rem; }
.plaza-guide h3 { font-size: .875rem; font-weight: 600; }
.plaza-guide .plaza-prerequisite { border-top: 1px solid var(--border); margin-top: 1.5rem; padding-top: 1.25rem; font-size: .8rem; }
.plaza-save-bar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .25rem 0 .75rem; }
.plaza-save-status { display: flex; align-items: center; gap: .5rem; color: var(--muted); font-size: .8rem; }
.plaza-settings button:disabled { opacity: .55; }
.plaza-loading { display: flex; align-items: center; justify-content: center; gap: .75rem; padding: 4rem 1.5rem; color: var(--muted); }
@media (max-width: 1000px) { .plaza-layout { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 600px) { .plaza-heading { align-items: flex-start; } .plaza-heading h1 { font-size: 1.35rem; } .plaza-card, .plaza-availability, .plaza-guide { padding: 1.1rem; } .plaza-section-icon { display: none; } .plaza-textarea, .plaza-group-input { font-size: 1rem; } }
</style>
