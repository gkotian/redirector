Option Explicit

Dim shell, wmiService, pythonExe, scriptPath, command, process
Dim killedExisting

Set shell = CreateObject("WScript.Shell")
Set wmiService = GetObject("winmgmts:\\.\\root\\cimv2")

pythonExe = "C:\Python313\pythonw.exe"
scriptPath = "\\wsl$\Arch\home\gautam\play\redirector\redirector.py"
command = """" & pythonExe & """ """ & scriptPath & """"
killedExisting = False

' Stop only prior redirector instances, identified by the script path in the command line.
For Each process In wmiService.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'pythonw.exe'")
    If Not IsNull(process.CommandLine) Then
        If InStr(1, process.CommandLine, scriptPath, vbTextCompare) > 0 Then
            process.Terminate
            killedExisting = True
        End If
    End If
Next

If killedExisting Then
    WScript.Sleep 1000
End If

' Run hidden and do not wait so login can continue normally.
shell.Run command, 0, False
