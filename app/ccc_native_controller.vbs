' CCC2 private adapter: ordered edge batches, explicit per-channel ACK, fresh sessions.
Class CCCNativeController
    Public GameName, SplashInfoLine, ShowTitle, ShowFrame, ShowDMDOnly
    Public HandleMechanics, HandleKeyboard, DIP, Sys11, Hidden
    Private fs, root, seq, sw, coils, lamps, gi, paused
    Private session, beat, beatAt, pingAt, parts, delivered, runtimeStatus, requestAck
    Private Sub Class_Initialize()
        Set fs=CreateObject("Scripting.FileSystemObject")
        root="" ' Caller must bind the current launch IPC directory before Run.
        seq=0: requestAck=0: session="": beat=-1: beatAt=Timer: pingAt=Timer
        ReDim sw(127),coils(63),lamps(89),gi(4),delivered(2)
        Dim i
        For i=0 To 127: sw(i)=0: Next
        For i=0 To 63: coils(i)=0: Next
        For i=0 To 89: lamps(i)=0: Next
        For i=0 To 4: gi(i)=0: Next
        For i=0 To 2: delivered(i)=0: Next
    End Sub
    Public Property Get Version: Version="22222222": End Property
    Public Property Let RootPath(p): root=p: End Property
    Public Property Get SessionID: SessionID=session: End Property
    Public Property Get RuntimeState: Poll: RuntimeState=runtimeStatus: End Property
    Private Function Elapsed(t)
        Dim dt: dt=Timer-t
        If dt<0 Then dt=dt+86400
        Elapsed=dt
    End Function
    Private Sub Send(command)
        Dim f,stem
        If session="" Then Err.Raise 513,"CCC bridge","Run required before requests"
        If seq-requestAck>=256 Then Err.Raise 513,"CCC bridge","Request backlog limit reached"
        seq=seq+1: stem=root & "/" & CStr(seq)
        Set f=fs.CreateTextFile(stem & ".req",True)
        f.WriteLine session & " " & CStr(seq) & " " & command: f.Close
        Set f=fs.CreateTextFile(stem & ".ready",True)
        f.WriteLine "1": f.Close
        pingAt=Timer
    End Sub
    Public Sub Run()
        If session<>"" Then Err.Raise 513,"CCC bridge","Session already bound"
        Poll
        If runtimeStatus<>"READY" Then Err.Raise 513,"CCC bridge","Runtime is not ready"
        Dim initialBeat,started
        initialBeat=beat: started=Timer
        Do
            Poll
            If beat<>initialBeat Then Exit Do
            If Elapsed(started)>1 Then Err.Raise 513,"CCC bridge","Runtime failed liveness challenge"
        Loop
        Send "RUN " & GameName
    End Sub
    Public Sub Shutdown(): Poll: Send "STOP": End Sub
    Public Property Let Pause(value)
        Poll: paused=CBool(value): Send "PAUSE " & CStr(Abs(CInt(paused)))
    End Property
    Public Property Get Pause: Pause=paused: End Property
    Public Property Let Switch(number,value)
        Poll
        If number<0 Or number>119 Then Err.Raise 513,"CCC bridge","Switch out of range"
        sw(number)=Abs(CInt(CBool(value)))
        Send "SW " & CStr(number) & " " & CStr(sw(number))
    End Property
    Public Property Get Switch(number): Switch=sw(number): End Property
    Public Property Get Switches: Switches=sw: End Property
    Public Property Get Solenoid(number): Poll: Solenoid=CBool(coils(number)): End Property
    Public Property Get Solenoids: Poll: Solenoids=coils: End Property
    Private Sub Poll()
        Dim f,line,header,values,i,age
        If Not fs.FileExists(root & "/state.txt") Then Err.Raise 513,"CCC bridge","Runtime state missing"
        Set f=fs.OpenTextFile(root & "/state.txt",1)
        line=f.ReadAll: f.Close
        parts=Split(Replace(line,vbCr,""),vbLf)
        If UBound(parts)<7 Then Err.Raise 513,"CCC bridge","Incomplete CCC2 state"
        header=Split(parts(0)," ")
        If UBound(header)<>4 Then Err.Raise 513,"CCC bridge","Invalid state header"
        If header(0)<>"CCC2" Then Err.Raise 513,"CCC bridge","Wrong protocol"
        If session="" Then session=header(1)
        If session<>header(1) Then Err.Raise 513,"CCC bridge","Runtime session changed"
        If CLng(header(4))<requestAck Or CLng(header(4))>seq Then Err.Raise 513,"CCC bridge","Invalid request acknowledgement"
        requestAck=CLng(header(4))
        If CDbl(header(2))<>beat Then
            beat=CDbl(header(2)): beatAt=Timer
        ElseIf Elapsed(beatAt)>3 Then
            Err.Raise 513,"CCC bridge","Runtime heartbeat expired"
        End If
        runtimeStatus=header(3)
        If runtimeStatus="ERROR" Or runtimeStatus="STOPPED" Then Err.Raise 513,"CCC bridge","Runtime stopped or failed"
        values=Split(parts(1)," ")
        If UBound(values)<>63 Then Err.Raise 513,"CCC bridge","Wrong coil count"
        For i=0 To 63: coils(i)=CInt(values(i)): Next
        values=Split(parts(2)," ")
        If UBound(values)<>89 Then Err.Raise 513,"CCC bridge","Wrong lamp count"
        For i=0 To 89: lamps(i)=CInt(values(i)): Next
        values=Split(parts(3)," ")
        If UBound(values)<>4 Then Err.Raise 513,"CCC bridge","Wrong GI count"
        For i=0 To 4: gi(i)=CInt(values(i)): Next
        If Elapsed(pingAt)>0.25 Then Send "PING"
    End Sub
    Private Function Edges(channel)
        Poll
        Dim rows,fields,i,n,result,nextSeq
        If parts(4+channel)="" Then Edges=Empty: Exit Function
        rows=Split(parts(4+channel),";"): n=0: nextSeq=delivered(channel)
        For i=0 To UBound(rows)
            fields=Split(rows(i),",")
            If UBound(fields)<>2 Then Err.Raise 513,"CCC bridge","Malformed edge"
            If CLng(fields(0))>delivered(channel) Then
                If CLng(fields(0))<>nextSeq+1 Then Err.Raise 513,"CCC bridge","Output sequence gap"
                nextSeq=CLng(fields(0)): n=n+1
            End If
        Next
        If n=0 Then Edges=Empty: Exit Function
        ReDim result(n-1,1): n=0
        For i=0 To UBound(rows)
            fields=Split(rows(i),",")
            If CLng(fields(0))>delivered(channel) Then
                result(n,0)=CInt(fields(1)): result(n,1)=CInt(fields(2)): n=n+1
            End If
        Next
        delivered(channel)=nextSeq
        Send "ACK " & Mid("CLG",channel+1,1) & " " & CStr(nextSeq)
        Edges=result
    End Function
    Public Property Get ChangedSolenoids: ChangedSolenoids=Edges(0): End Property
    Public Property Get ChangedLamps: ChangedLamps=Edges(1): End Property
    Public Property Get ChangedGIStrings: ChangedGIStrings=Edges(2): End Property
    Public Property Get Mech(number): Mech=0: End Property
    Public Property Get GetMech(number): GetMech=0: End Property
    Public Sub PrintGlobal(): End Sub
End Class
