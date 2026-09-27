Option Explicit

' Portable launcher: starts the bundled Python entry point without showing a terminal.
Dim shell, fso, root, python, entry, command, result
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = fso.BuildPath(root, "runtime\python\pythonw.exe")
entry = fso.BuildPath(root, "scripts\installed_launcher.py")
If Not fso.FileExists(python) Or Not fso.FileExists(entry) Then
  MsgBox "便携版运行组件不完整，请重新下载完整压缩包。", 16, "爆款内容工厂"
  WScript.Quit 1
End If
command = Chr(34) & python & Chr(34) & " -B " & Chr(34) & entry & Chr(34)
result = shell.Run(command, 0, True)
If result <> 0 Then
  MsgBox "软件未能启动。请查看用户目录 %LOCALAPPDATA%\ContentFactory\runtime 中的诊断文件。", 48, "爆款内容工厂"
End If
