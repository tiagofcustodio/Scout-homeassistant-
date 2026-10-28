$ips = @("10.42.0.1") + (1..254 | ForEach-Object { "192.168.1.$_" })
Write-Host "Scanning 192.168.1.1-254 for Moorebot Scout (ROS Port 11311 and SSH Port 22)..."

$runspacePool = [runspacefactory]::CreateRunspacePool(1, 64)
$runspacePool.Open()
$jobs = @()

$scriptBlock = {
    param($ip)
    $rosOpen = $false
    $sshOpen = $false

    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect($ip, 11311, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(450, $false)) {
            try {
                $tcp.EndConnect($iar)
                $rosOpen = $true
            } catch {}
        }
        $tcp.Close()
    } catch {}

    try {
        $tcp2 = New-Object System.Net.Sockets.TcpClient
        $iar2 = $tcp2.BeginConnect($ip, 22, $null, $null)
        if ($iar2.AsyncWaitHandle.WaitOne(450, $false)) {
            try {
                $tcp2.EndConnect($iar2)
                $sshOpen = $true
            } catch {}
        }
        $tcp2.Close()
    } catch {}

    if ($rosOpen -or $sshOpen) {
        return [PSCustomObject]@{
            IP = $ip
            ROSPort11311 = $rosOpen
            SSHPort22 = $sshOpen
        }
    }
    return $null
}

foreach ($ip in $ips) {
    $ps = [powershell]::Create().AddScript($scriptBlock).AddArgument($ip)
    $ps.RunspacePool = $runspacePool
    $jobs += [PSCustomObject]@{ Pipe = $ps; Result = $ps.BeginInvoke() }
}

$found = @()
foreach ($j in $jobs) {
    $res = $j.Pipe.EndInvoke($j.Result)
    if ($res) { $found += $res }
    $j.Pipe.Dispose()
}
$runspacePool.Close()

if ($found.Count -eq 0) {
    Write-Host "No hosts found with port 11311 or 22 open."
} else {
    $found | Format-Table -AutoSize
}
