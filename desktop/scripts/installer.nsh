; Replacing or removing the app's files (the installer over an older version, the uninstaller): first stop the nest
; that runs from them, then whatever else does.
;
; electron-builder's own check ends every process below the install folder at once. For nest that would be in the
; middle of a reply or a write. So: ask (as it does), let the old version's own Python run `nest stop --mine` (it
; finds every data folder by the running processes), then end the rest. The check here also looks for "$INSTDIR\",
; with the backslash: without it a neighbouring folder whose name merely starts the same would be ended too.
; The uninstaller itself may be run from there.

!include "getProcessInfo.nsh"
!include "nsDialogs.nsh"
Var pid

!macro nestProcesses _DO _RETURN
  nsExec::Exec /TIMEOUT=20000 `"$PowerShellPath" -NoProfile -NonInteractive -C "$$p = @(Get-CimInstance -ClassName Win32_Process | ? {$$_.Path -and $$_.Name -ne '${UNINSTALL_FILENAME}' -and $$_.Path.StartsWith('$INSTDIR\', 'CurrentCultureIgnoreCase')}); ${_DO}"`
  Pop ${_RETURN}
!macroend

!define NEST_ANY "if ($$p.Count -gt 0) { exit 0 } else { exit 1 }"
!define NEST_END "$$p | % { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"

!macro nestStop
  ${if} ${FileExists} "$INSTDIR\resources\backend\python\python.exe"
    nsExec::Exec /TIMEOUT=45000 `"$INSTDIR\resources\backend\python\python.exe" -I -B -m nest stop --mine`
    Pop $R1
  ${endif}
!macroend

!macro customCheckAppRunning
  ; Started from PowerShell 7 (or by an app that was), its modules come first and Windows' own PowerShell cannot load
  ; them: it would be taken for missing, and nest would run on while its files are removed.
  System::Call 'kernel32::SetEnvironmentVariable(t "PSModulePath", p 0)'
  !insertmacro IS_POWERSHELL_AVAILABLE
  ${if} $IsPowerShellAvailable != 0
    !insertmacro nestStop
    !insertmacro _CHECK_APP_RUNNING
  ${else}
    !insertmacro nestProcesses "${NEST_ANY}" $R0
    ${if} $R0 == 0
      ${ifNot} ${isUpdated}
        MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(appRunning)" /SD IDOK IDOK nestEnd
        Quit
      ${endif}
      nestEnd:
      DetailPrint "$(appClosing)"
      !insertmacro nestStop
      StrCpy $R1 0
      nestAgain:
        IntOp $R1 $R1 + 1
        !insertmacro nestProcesses "${NEST_END}" $R0
        Sleep 1000
        !insertmacro nestProcesses "${NEST_ANY}" $R0
        ${if} $R0 == 0
          ${if} $R1 < 5
            Goto nestAgain
          ${endif}
          MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(appCannotBeClosed)" /SD IDCANCEL IDRETRY nestAgain
          Quit
        ${endif}
    ${endif}
  ${endif}
!macroend

; Choosing the folder. electron-builder's own page (allowToChangeInstallationDirectory) puts a nest folder below the
; chosen one unless "nest" is somewhere in its path, and then uses it whatever is in it: the uninstaller removes the
; whole folder, with the user's own files. Here: a new or empty folder, or the one nest is in already, is used as it
; was chosen; one with other things in it gets a nest folder of its own inside, said beforehand; and a folder that
; cannot be written to is refused on the spot.

; Windows paths end at 259 characters; build-backend.js keeps the longest below the install folder under 135.
!define NEST_LONGEST 120

!macro customPageAfterChangeDir
  Var nestDir ; where it goes; "" when there is no good place
  Var nestDesktop ; "1": a shortcut on the desktop
  Var nestMenu ; "1": one in the Start menu
  Var nestDesktopBox
  Var nestMenuBox

  ; Where the shortcuts go. Not asked again on an update: the last answer stands.
  Function nestLinksShow
    ${if} ${isUpdated}
      Abort
    ${endIf}
    !insertmacro MUI_HEADER_TEXT "快捷方式" "选择要在哪里放 nest 的图标"
    nsDialogs::Create 1018
    Pop $0
    ${if} $0 == error
      Abort
    ${endIf}
    ${NSD_CreateCheckbox} 0 0 100% 12u "在桌面创建快捷方式"
    Pop $nestDesktopBox
    ${if} $nestDesktop == "1"
      ${NSD_Check} $nestDesktopBox
    ${endIf}
    ${NSD_CreateCheckbox} 0 20u 100% 12u "添加到开始菜单"
    Pop $nestMenuBox
    ${if} $nestMenu == "1"
      ${NSD_Check} $nestMenuBox
    ${endIf}
    ${NSD_CreateLabel} 12u 34u -12u 20u "开始菜单里有图标，Windows 才会弹出 nest 的提醒通知。"
    Pop $0
    nsDialogs::Show
  FunctionEnd

  Function nestLinksLeave
    ${NSD_GetState} $nestDesktopBox $0
    StrCpy $nestDesktop "0"
    ${if} $0 == ${BST_CHECKED}
      StrCpy $nestDesktop "1"
    ${endIf}
    ${NSD_GetState} $nestMenuBox $0
    StrCpy $nestMenu "0"
    ${if} $0 == ${BST_CHECKED}
      StrCpy $nestMenu "1"
    ${endIf}
  FunctionEnd

  ; $0: a folder. $1: "1" when there are other things in it than nest.
  Function nestTaken
    StrCpy $1 "0"
    ${if} ${FileExists} "$0\${APP_EXECUTABLE_FILENAME}"
    ${andIf} ${FileExists} "$0\resources\app.asar"
      Return
    ${endIf}
    StrLen $2 $0
    ${if} $2 <= 3 ; a whole drive
      StrCpy $1 "1"
      Return
    ${endIf}
    StrCpy $3 ""
    ClearErrors
    FindFirst $2 $3 "$0\*.*"
    ${doWhile} $3 != ""
      ${if} $3 != "."
      ${andIf} $3 != ".."
        StrCpy $1 "1"
        ${exitDo}
      ${endIf}
      StrCpy $3 ""
      FindNext $2 $3
    ${loop}
    FindClose $2
    ClearErrors
  FunctionEnd

  Function nestPlace
    StrCpy $0 $INSTDIR
    Call nestTaken
    ${if} $1 == "1"
      StrCpy $2 $0 1 -1
      ${if} $2 == "\"
        StrCpy $0 $0 -1
      ${endIf}
      StrCpy $0 "$0\${APP_FILENAME}"
      Call nestTaken
    ${endIf}
    StrCpy $nestDir $0
    ${if} $1 == "1"
      StrCpy $nestDir ""
    ${endIf}
  FunctionEnd

  Function nestDirLeave
    Call nestPlace
    ${if} $nestDir == ""
      MessageBox MB_OK|MB_ICONEXCLAMATION "这个文件夹和它里面的 ${APP_FILENAME} 文件夹都有别的文件，请换一个。"
      Abort
    ${endIf}
    StrLen $2 $nestDir
    ${if} $2 > ${NEST_LONGEST}
      MessageBox MB_OK|MB_ICONEXCLAMATION "这个路径太长了（Windows 的限制），请换一个短一些的，不超过 ${NEST_LONGEST} 个字。"
      Abort
    ${endIf}
    ${if} $nestDir != $INSTDIR
      MessageBox MB_OKCANCEL|MB_ICONINFORMATION "这个文件夹里已经有别的文件，会装到它里面单独的文件夹：$\r$\n$\r$\n$nestDir" IDOK nestDirGood
      Abort
      nestDirGood:
    ${endIf}
    ClearErrors
    CreateDirectory $nestDir
    FileOpen $2 "$nestDir\.nest-try" w
    ${if} ${errors}
      RMDir $nestDir
      MessageBox MB_OK|MB_ICONEXCLAMATION "不能写到这个文件夹：$\r$\n$nestDir$\r$\n$\r$\n换一个，或者退回上一步，选为所有用户安装。"
      Abort
    ${endIf}
    FileClose $2
    Delete "$nestDir\.nest-try"
    RMDir $nestDir ; only when empty
    ClearErrors
  FunctionEnd

  ; $INSTDIR set in the directory page's leave function would be overwritten by the page.
  Function nestFilesPre
    ${if} $nestDir != ""
      StrCpy $INSTDIR $nestDir
    ${endIf}
  FunctionEnd

  AllowRootDirInstall true ; or the button is just grey: nestDirLeave says what happens instead
  !insertmacro skipPageIfUpdated
  !define MUI_PAGE_CUSTOMFUNCTION_LEAVE nestDirLeave
  !insertmacro MUI_PAGE_DIRECTORY
  Page custom nestLinksShow nestLinksLeave
  !define MUI_PAGE_CUSTOMFUNCTION_PRE nestFilesPre ; of the page that follows, the installing
!macroend

; Silent (/S /D=folder): the same places, without the questions.
!macro customInit
  ; The last answer (kept with the install's own registry entries, which go with it), else both: but an update from
  ; a version that never asked keeps what it made, the Start menu only.
  ReadRegStr $nestDesktop SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" NestDesktopShortcut
  ${if} $nestDesktop == ""
    StrCpy $nestDesktop "1"
    ${if} ${isUpdated}
      StrCpy $nestDesktop "0"
    ${endIf}
  ${endIf}
  ReadRegStr $nestMenu SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" NestStartMenuShortcut
  ${if} $nestMenu == ""
    StrCpy $nestMenu "1"
  ${endIf}

  ${if} ${Silent}
    Call nestPlace
    StrLen $2 $nestDir
    ${if} $nestDir == ""
    ${orIf} $2 > ${NEST_LONGEST}
      SetErrorLevel 2
      Quit
    ${endIf}
    StrCpy $INSTDIR $nestDir
  ${endIf}
!macroend

; electron-builder keeps a copy of the whole installer for its auto-update, which nest does not use (src/update.js has
; its own): 140 MB that not even the uninstaller removes.
!macro customInstall
  ${if} $installMode == "all"
    SetShellVarContext current
  ${endif}
  Delete "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}"
  ${GetParent} "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}" $0
  RMDir $0 ; only when empty
  ${if} $installMode == "all"
    SetShellVarContext all
  ${endif}

  ; electron-builder has just made both shortcuts.
  ${if} $nestDesktop != "1"
    WinShell::UninstShortcut "$newDesktopLink"
    Delete "$newDesktopLink"
  ${endIf}
  ${if} $nestMenu != "1"
    WinShell::UninstShortcut "$newStartMenuLink"
    Delete "$newStartMenuLink"
    StrCpy $launchLink "$appExe"
  ${endIf}
  System::Call 'Shell32::SHChangeNotify(i 0x8000000, i 0, i 0, i 0)'
  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" NestDesktopShortcut $nestDesktop
  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" NestStartMenuShortcut $nestMenu
!macroend

; The last page: start nest now, ticked. From the Start menu shortcut when there is one (it carries the app's ID),
; else the program itself.
!macro customFinishPage
  Function nestRun
    ${if} ${isUpdated}
      StrCpy $1 "--updated"
    ${else}
      StrCpy $1 ""
    ${endif}
    ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
  FunctionEnd

  !define MUI_FINISHPAGE_RUN
  !define MUI_FINISHPAGE_RUN_TEXT "立即运行 nest"
  !define MUI_FINISHPAGE_RUN_FUNCTION "nestRun"
  !insertmacro MUI_PAGE_FINISH
!macroend
