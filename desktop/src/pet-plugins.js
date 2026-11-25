'use strict'

// 插件里的桌宠: pets, skins and accessories that come in plugins installed in nest.
//
// nest lists them (GET /api/pet-plugins: each plugin's version, who may use it, and its pet folders with
// their files); they are fetched into <pets>\.plugins\<plugin id>@<version>\<folder>\ and loaded beside the
// user's own packs (pets.js). A plugin's folder is fetched once per version; one nest no longer lists goes.
// The list is kept in .plugins\index.json, so the pets are there when nest is away at start.
//
// A folder is a pet or a skin (pet.json: "name" is whose pet, "skin_of" makes it a skin of hers) or an
// accessory (acc.json + item.png: "for" names whose, else anyone's).

const fs = require('node:fs')
const path = require('node:path')
const log = require('./log')

const FILE_TIMEOUT = 60_000
const SAFE = /^[A-Za-z0-9._-]+$/

const folderOf = (root, plugin) => path.join(root, `${plugin.id}@${plugin.version}`)

function readIndex(root) {
  try {
    const found = JSON.parse(fs.readFileSync(path.join(root, 'index.json'), 'utf8'))
    return Array.isArray(found) ? found : []
  } catch {
    return []
  }
}

/** Fetch what is new from nest; the plugins whose pets are on this computer now. */
async function syncPetPlugins(nest, petsDir) {
  const root = path.join(petsDir, '.plugins')
  fs.mkdirSync(root, { recursive: true })
  const res = await nest.api('GET', '/api/pet-plugins', undefined, { timeout: 20_000 })
  if (res.status !== 200 || !Array.isArray(res.json?.plugins)) return readIndex(root)
  const plugins = res.json.plugins.filter(
    (p) => SAFE.test(String(p.id)) && SAFE.test(String(p.version)) && Array.isArray(p.items),
  )
  for (const plugin of plugins) {
    const target = folderOf(root, plugin)
    if (fs.existsSync(target)) continue
    const temp = `${target}.part`
    fs.rmSync(temp, { recursive: true, force: true })
    try {
      for (const item of plugin.items) {
        const folder = String(item.path || '')
        if (!folder || folder.split('/').some((part) => !part || part === '..' || !SAFE.test(part))) continue
        for (const name of item.files || []) {
          if (!SAFE.test(String(name))) continue
          const file = await nest.api(
            'GET',
            `/api/pet-plugins/${encodeURIComponent(plugin.id)}/file?path=${encodeURIComponent(`${folder}/${name}`)}`,
            undefined,
            { timeout: FILE_TIMEOUT },
          )
          if (file.status !== 200 || typeof file.json?.data !== 'string') throw new Error(`${folder}/${name}: ${file.status}`)
          const place = path.join(temp, ...folder.split('/'), name)
          fs.mkdirSync(path.dirname(place), { recursive: true })
          fs.writeFileSync(place, Buffer.from(file.json.data, 'base64'))
        }
      }
      fs.renameSync(temp, target)
      log.info(`pet plugin ${plugin.id} ${plugin.version}: fetched`)
    } catch (err) {
      fs.rmSync(temp, { recursive: true, force: true })
      log.warn(`pet plugin ${plugin.id}: could not fetch (${err.message})`)
    }
  }
  const keep = new Set(plugins.map((p) => path.basename(folderOf(root, p))))
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (entry.isDirectory() && !keep.has(entry.name)) fs.rmSync(path.join(root, entry.name), { recursive: true, force: true })
  }
  const ready = plugins.filter((p) => fs.existsSync(folderOf(root, p)))
  fs.writeFileSync(path.join(root, 'index.json'), JSON.stringify(ready, null, 2), 'utf8')
  return ready
}

/** The plugin pets on this computer, as loadable items: { kind, for, label, folder, id, bots }. */
function pluginItems(petsDir, plugins = readIndex(path.join(petsDir, '.plugins'))) {
  const root = path.join(petsDir, '.plugins')
  const items = []
  for (const plugin of plugins) {
    for (const item of plugin.items || []) {
      const folder = path.join(folderOf(root, plugin), ...String(item.path || '').split('/'))
      if (!fs.existsSync(folder)) continue
      items.push({
        kind: item.kind,
        for: String(item.for || ''),
        label: String(item.label || ''),
        folder,
        id: `${plugin.id}/${item.path}`,
        bots: Array.isArray(plugin.bots) ? plugin.bots.map(String) : null,
      })
    }
  }
  return items
}

module.exports = { syncPetPlugins, pluginItems }
