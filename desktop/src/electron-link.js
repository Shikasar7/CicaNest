'use strict'

// Run from the sources: for its first notification Electron makes a Start Menu shortcut of its own, Electron.lnk, with
// our AppUserModelID and no icon. Windows then takes the taskbar button's icon from it (Electron's) instead of from
// nest.lnk. The shortcut gets our icon, and what it needs to open nest; Electron makes it again when its
// toastActivatorClsid is gone, so that stays.

/** What to write into the shortcut `link` (shell.readShortcutLink), or null when it is not that one, or fixed. */
function linkFix(link, { appId, execPath, icon, appPath }) {
  const same = (a, b) => String(a || '').toLowerCase() === String(b || '').toLowerCase()
  if (!link || link.appUserModelId !== appId || !same(link.target, execPath) || link.icon) return null
  return {
    target: link.target,
    appUserModelId: appId,
    ...(link.toastActivatorClsid ? { toastActivatorClsid: link.toastActivatorClsid } : {}),
    icon,
    iconIndex: 0,
    args: `"${appPath}"`,
    cwd: appPath,
  }
}

/** Gives `file` its icon when it is such a shortcut; whether it did. `shell`: Electron's. */
function fixElectronLink(file, shell, options) {
  let link
  try {
    link = shell.readShortcutLink(file)
  } catch {
    return false // there is none
  }
  const fix = linkFix(link, options)
  return Boolean(fix) && shell.writeShortcutLink(file, 'update', fix)
}

module.exports = { fixElectronLink, linkFix }
