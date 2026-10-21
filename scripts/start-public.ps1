# Chạy Link2Audio cho người khác dùng qua web:
#   1. bật backend trên máy này (127.0.0.1:8000)
#   2. mở Cloudflare Tunnel miễn phí -> có địa chỉ https công khai
#   3. ghi địa chỉ đó vào frontend/config.js và push lên GitHub để trang Pages tự trỏ đúng máy chủ
#   4. in + copy "link mời" cố định để gửi cho người khác
# Đóng cửa sổ hoặc Ctrl+C để dừng.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Port = 8000
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ToolsDir = Join-Path $Root "tools"
$SecretFile = Join-Path $Root "secrets\api-key.txt"
$LogDir = Join-Path $Root "data\logs"
$CloudflaredUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
$TunnelWaitSeconds = 60
$HealthWaitSeconds = 30

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Initialize-Python {
    if (Test-Path $Python) { return }
    Write-Step "Tạo môi trường Python (lần đầu, mất vài phút)..."
    & python -m venv .venv
    & $Python -m pip install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Cài thư viện Python thất bại" }
}

function Get-Cloudflared {
    $installed = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($installed) { return $installed.Source }
    $exe = Join-Path $ToolsDir "cloudflared.exe"
    if (-not (Test-Path $exe)) {
        Write-Step "Tải cloudflared từ GitHub của Cloudflare..."
        New-Item -ItemType Directory -Force $ToolsDir | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -UseBasicParsing -Uri $CloudflaredUrl -OutFile $exe
        $signature = Get-AuthenticodeSignature $exe
        if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Cloudflare") {
            Remove-Item $exe -Force
            throw "cloudflared tải về không có chữ ký hợp lệ của Cloudflare — đã xoá"
        }
    }
    return $exe
}

function Get-ApiKey {
    if (Test-Path $SecretFile) { return (Get-Content $SecretFile -Raw).Trim() }
    New-Item -ItemType Directory -Force (Split-Path $SecretFile) | Out-Null
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    Set-Content -Path $SecretFile -Value $key -NoNewline -Encoding ascii
    return $key
}

function Get-PagesInfo {
    $remote = (& git remote get-url origin).Trim()
    if ($remote -notmatch "github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(\.git)?$") {
        throw "Không đọc được repo GitHub từ 'git remote origin' ($remote)"
    }
    $owner = $Matches["owner"].ToLower()
    return @{ Origin = "https://$owner.github.io"; Url = "https://$owner.github.io/$($Matches["repo"])/" }
}

function Assert-PortFree {
    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) { throw "Cổng $Port đang được dùng (run.bat đang chạy?). Tắt nó rồi chạy lại." }
}

function Wait-Health([string]$BaseUrl, [string]$Key, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Headers @{ "X-API-Key" = $Key } -TimeoutSec 5
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Wait-TunnelUrl([string]$LogFile) {
    $deadline = (Get-Date).AddSeconds($TunnelWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $LogFile) {
            $match = Select-String -Path $LogFile -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($match) { return $match.Matches[0].Value }
        }
        Start-Sleep -Seconds 1
    }
    throw "Không lấy được địa chỉ tunnel sau $TunnelWaitSeconds giây (xem $LogFile)"
}

function Publish-ServerUrl([string]$Url) {
    $configPath = Join-Path $Root "frontend\config.js"
    $content = "// Tự động ghi bởi scripts/start-public.ps1 — địa chỉ máy chủ hiện tại.`n" +
               "window.L2A_CONFIG = { apiBase: `"$Url`" };`n"
    [IO.File]::WriteAllText($configPath, $content, (New-Object Text.UTF8Encoding $false))

    $ErrorActionPreference = "Continue"  # git ghi tiến trình ra stderr, không coi là lỗi
    & git diff --quiet -- frontend/config.js
    if ($LASTEXITCODE -eq 0) { return $true }
    & git commit -q -m "chore: update server URL" -- frontend/config.js
    if ($LASTEXITCODE -ne 0) { return $false }
    & git push -q 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

$backend = $null
$tunnel = $null
try {
    Initialize-Python
    Assert-PortFree
    $cloudflared = Get-Cloudflared
    $apiKey = Get-ApiKey
    $pages = Get-PagesInfo
    New-Item -ItemType Directory -Force $LogDir | Out-Null

    Write-Step "Bật máy chủ xử lý..."
    $env:L2A_CORS_ORIGINS = $pages.Origin
    $env:L2A_API_KEY = $apiKey
    $backend = Start-Process -FilePath $Python -NoNewWindow -PassThru `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--no-access-log" `
        -RedirectStandardOutput (Join-Path $LogDir "backend.out.log") `
        -RedirectStandardError (Join-Path $LogDir "backend.log")
    if (-not (Wait-Health "http://127.0.0.1:$Port" $apiKey $HealthWaitSeconds)) {
        throw "Máy chủ không khởi động được (xem data\logs\backend.log)"
    }

    Write-Step "Mở Cloudflare Tunnel..."
    $tunnelLog = Join-Path $LogDir "tunnel.log"
    Remove-Item $tunnelLog -ErrorAction SilentlyContinue
    $tunnel = Start-Process -FilePath $cloudflared -NoNewWindow -PassThru `
        -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port" `
        -RedirectStandardOutput (Join-Path $LogDir "tunnel.out.log") `
        -RedirectStandardError $tunnelLog
    $publicUrl = Wait-TunnelUrl $tunnelLog
    if (-not (Wait-Health $publicUrl $apiKey $HealthWaitSeconds)) {
        Write-Warning "Tunnel đã có địa chỉ nhưng chưa truy cập được, có thể cần thêm ít phút."
    }

    Write-Step "Cập nhật địa chỉ máy chủ lên GitHub Pages..."
    $published = Publish-ServerUrl $publicUrl
    $invite = "$($pages.Url)#key=$apiKey"
    if (-not $published) {
        Write-Warning "Không push được lên GitHub — link mời dưới đây kèm luôn địa chỉ máy chủ (sẽ hết hiệu lực khi tắt máy chủ)."
        $invite = "$($pages.Url)#server=$([Uri]::EscapeDataString($publicUrl))&key=$apiKey"
    }
    Set-Clipboard -Value $invite

    Write-Host ""
    Write-Host "  ĐANG CHẠY — gửi link này cho người khác (đã copy sẵn):" -ForegroundColor Green
    Write-Host "  $invite" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Máy chủ:   $publicUrl"
    if ($published) { Write-Host "  Trang web cập nhật địa chỉ mới sau khoảng 1 phút." }
    Write-Host "  Giữ cửa sổ này mở. Đóng cửa sổ hoặc Ctrl+C để dừng."
    Write-Host ""

    while (-not $backend.HasExited -and -not $tunnel.HasExited) { Start-Sleep -Seconds 2 }
    if ($backend.HasExited) { Write-Warning "Máy chủ đã dừng (xem data\logs\backend.log)" }
    if ($tunnel.HasExited) { Write-Warning "Tunnel đã dừng (xem data\logs\tunnel.log)" }
}
catch {
    Write-Host "LỖI: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    foreach ($proc in @($tunnel, $backend)) {
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    }
}
