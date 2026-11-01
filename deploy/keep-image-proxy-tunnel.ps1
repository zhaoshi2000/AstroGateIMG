param(
    [Parameter(Mandatory = $true)][string]$RemoteHost,
    [int]$LocalProxyPort = 7897,
    [int]$RemotePort = 17897
)

$ssh = (Get-Command ssh.exe).Source
$forward = "127.0.0.1:${RemotePort}:127.0.0.1:${LocalProxyPort}"
& $ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R $forward $RemoteHost
exit $LASTEXITCODE
