import { computed, onMounted, onUnmounted, ref } from 'vue'

export function useRefreshCountdown(intervalSeconds) {
  const seconds = ref(intervalSeconds)
  let timer
  const reset = () => { seconds.value = intervalSeconds }
  const text = computed(() => seconds.value > 0 ? `${seconds.value}秒后` : '即将刷新')

  onMounted(() => {
    reset()
    timer = setInterval(() => { seconds.value = Math.max(0, seconds.value - 1) }, 1000)
  })
  onUnmounted(() => clearInterval(timer))

  return { text, reset }
}
