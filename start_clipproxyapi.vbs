Option Explicit

Const SHOW_WINDOW = 1

Dim shell, fileSystem, root, launcher, pythonw
Set shell = CreateObject("Shell.Application")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

root = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = fileSystem.BuildPath(root, "launch.py")
pythonw = fileSystem.BuildPath(root, ".venv\Scripts\pythonw.exe")

If Not fileSystem.FileExists(launcher) Then
    ShowError "Startup file not found:" & vbCrLf & launcher & vbCrLf & vbCrLf & _
              "Keep this script in the project root folder."
End If

If Not fileSystem.FileExists(pythonw) Then
    ShowError "Project Python environment not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
              "Run these commands in the project root first:" & vbCrLf & _
              "py -3.11 -m venv .venv" & vbCrLf & _
              ".venv\Scripts\python.exe -m pip install -r requirements.txt"
End If

shell.ShellExecute pythonw, Quote(launcher), root, "open", SHOW_WINDOW
WScript.Quit 0

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Sub ShowError(message)
    MsgBox message, vbCritical, "CLIProxyAPI Config Assistant"
    WScript.Quit 1
End Sub
