const { readFileSync, writeFileSync } = require('node:fs')
const { resolve } = require('node:path')

function nextVersion(version) {
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) throw new Error('版本号必须为三个非负整数')
  let [major, minor, patch] = version.split('.').map(Number)
  if (![major, minor, patch].every(Number.isSafeInteger) || minor > 15 || patch > 10) throw new Error('第二段最大为 15，第三段最大为 10')
  patch++
  if (patch > 10) { patch = 0; minor++ }
  if (minor > 15) { minor = 0; major++ }
  return `${major}.${minor}.${patch}`
}

function bumpVersion() {
  const packagePath = resolve(__dirname, '../package.json')
  const lockPath = resolve(__dirname, '../package-lock.json')
  const packageText = readFileSync(packagePath, 'utf8')
  const lockText = readFileSync(lockPath, 'utf8')
  const version = JSON.parse(packageText).version
  const lock = JSON.parse(lockText)
  if (lock.version !== version || lock.packages[''].version !== version) throw new Error('package.json 与 package-lock.json 版本不一致，请先核对')
  const next = nextVersion(version)
  const updatedPackage = packageText.replace(`  "version": "${version}"`, `  "version": "${next}"`)
  const updatedLock = lockText.replace(`  "version": "${version}"`, `  "version": "${next}"`).replace(`      "version": "${version}"`, `      "version": "${next}"`)
  const parsedLock = JSON.parse(updatedLock)
  if (JSON.parse(updatedPackage).version !== next || parsedLock.version !== next || parsedLock.packages[''].version !== next) throw new Error('无法定位版本字段，未写入文件')
  writeFileSync(packagePath, updatedPackage)
  writeFileSync(lockPath, updatedLock)
  console.log(`${version} -> ${next}`)
}

if (require.main === module) {
  try { bumpVersion() }
  catch (error) { console.error(error.message); process.exitCode = 1 }
}

module.exports = { nextVersion }
