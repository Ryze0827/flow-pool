export async function api(path, method = 'GET', body) {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Scheduler-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await response.json()
  if (!response.ok) {
    const error = new Error(data.detail || '请求失败，请稍后重试')
    error.status = response.status
    error.workspaces = data.workspaces
    throw error
  }
  return data
}
