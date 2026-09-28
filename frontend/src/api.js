export async function api(path, method = 'GET', body, options = {}) {
  const response = await fetch(`/api${path}`, {
    method,
    signal: options.signal,
    headers: { 'Content-Type': 'application/json', 'X-Scheduler-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const error = new Error(data?.detail || '请求失败，请稍后重试')
    error.status = response.status
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('flowpool-auth-expired'))
    error.workspaces = data?.workspaces
    throw error
  }
  if (data === null) throw new Error('服务返回了无效响应，请稍后重试')
  return data
}
