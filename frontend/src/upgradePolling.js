// 串行轮询：请求结束后再计时，避免重启期间慢请求堆积。
export function createUpgradePolling(refresh, isRunning, timers = globalThis) {
  let timer, stopped = false
  function cancel() {
    timers.clearTimeout(timer)
    timer = undefined
  }
  function schedule() {
    cancel()
    if (stopped || !isRunning()) return
    timer = timers.setTimeout(async () => {
      try { await refresh() } finally { schedule() }
    }, 2000)
  }
  return { schedule, cancel, stop() { stopped = true; cancel() } }
}
