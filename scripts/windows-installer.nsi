Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
Name "爆款内容工厂 ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\ContentFactory"
InstallDirRegKey HKCU "Software\ContentFactory" "InstallRoot"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "本软件需要 64 位 Windows。"
    Abort
  ${EndIf}
FunctionEnd
Section "软件和运行组件"
  ; Bootstrap is deliberately on the selected drive, not the Windows temp drive.
  SetOutPath "$INSTDIR\installer-bootstrap"
  File /r "${PYTHON}\*.*"
  File "${WORKER}"
  File "${MANIFEST}"
  DetailPrint "正在验证并安装程序、语音模型和运行组件，请稍候…"
  ExecWait '"$INSTDIR\installer-bootstrap\python.exe" -B "$INSTDIR\installer-bootstrap\install_payload.py" --source "$EXEDIR" --target "$INSTDIR" --manifest "$INSTDIR\installer-bootstrap\delivery-manifest.json"' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "安装未完成。请确保所有配套文件与安装程序放在同一文件夹。详细原因见安装目录中的 installation-error.txt。已有版本和用户数据保持不变。"
    SetErrorLevel 1
    Abort
  ${EndIf}
  WriteRegStr HKCU "Software\ContentFactory" "InstallRoot" "$INSTDIR"
  WriteRegStr HKCU "Software\ContentFactory" "Version" "${VERSION}"
  CreateShortCut "$DESKTOP\爆款内容工厂.lnk" "$INSTDIR\versions\${VERSION}\runtime\python\pythonw.exe" '-B "$INSTDIR\versions\${VERSION}\scripts\installed_launcher.py"' "$INSTDIR\versions\${VERSION}\content-factory-desktop.exe"
SectionEnd
