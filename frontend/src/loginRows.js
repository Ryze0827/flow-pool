export function parseLoginRows(source) {
  const rows = []
  const emails = new Set()
  for (const [index, raw] of source.split(/\r?\n/).entries()) {
    const line = raw.trim().replace(/^\[([^\]]+)\]\(mailto:[^)]+\)/i, '$1').replace(/^mailto:/i, '')
    if (!line) continue
    const first = line.indexOf('----')
    const last = line.lastIndexOf('----')
    if (first <= 0 || last <= first + 4) throw new Error(`第 ${index + 1} 行格式不正确，请使用：邮箱----密码----2FA密钥`)
    const email = line.slice(0, first).trim()
    const password = line.slice(first + 4, last)
    const totp_secret = line.slice(last + 4).trim()
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254 || !password || password.length > 512 || totp_secret.length > 1024) {
      throw new Error(`第 ${index + 1} 行账号信息无效，请检查邮箱、密码及密钥长度`)
    }
    if (emails.has(email.toLowerCase())) throw new Error(`第 ${index + 1} 行邮箱重复，请去除重复账号`)
    emails.add(email.toLowerCase())
    rows.push({ line: index + 1, email, password, totp_secret, workspace_id: '', workspaces: [], status: 'waiting', message: '' })
  }
  if (!rows.length) throw new Error('请先粘贴账号，每行一个')
  if (rows.length > 500) throw new Error('单次最多导入 500 个账号')
  return rows
}
