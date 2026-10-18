// 串行轮询：空闲时每 5 分钟检查，升级时每 2 秒跟进，避免慢请求堆积。
export function createUpgradePolling(refresh, isRunning, timers = globalThis) {
  let timer, stopped = false
  function cancel() {
    timers.clearTimeout(timer)
    timer = undefined
  }
  function schedule() {
    cancel()
    if (stopped) return
    timer = timers.setTimeout(async () => {
      try { await refresh() } finally { schedule() }
    }, isRunning() ? 2000 : 300000)
  }
  return { schedule, cancel, stop() { stopped = true; cancel() } }
}
