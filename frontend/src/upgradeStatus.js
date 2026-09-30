// Read status first so older backends and failed remote checks retain version/progress.
export async function loadUpgradeStatus(api, checkRemote, signal, onStatus) {
  const status = await api('/upgrade', 'GET', undefined, { signal })
  onStatus(status)
  if (!checkRemote || status.running) return
  try {
    onStatus(await api('/upgrade/check', 'POST', undefined, { signal }))
  } catch (error) {
    if ([404, 405].includes(error.status)) {
      throw new Error('当前连接的后端版本暂不支持检查更新，请更新并重启 FlowPool 后端后再试。当前尚未确认是否有新版本。')
    }
    throw error
  }
}
