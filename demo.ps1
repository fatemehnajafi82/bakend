[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$base = "http://127.0.0.1:8000/api"
$email = "fatima2003najafi@gmail.com"

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

Step "1) Request OTP"
Invoke-RestMethod -Method Post -Uri "$base/auth/email/request-otp/" `
  -ContentType "application/json" -Body (@{email=$email} | ConvertTo-Json)

$code = Read-Host "Enter the 6-digit code from your email"

Step "2) Verify OTP and get token"
$r = Invoke-RestMethod -Method Post -Uri "$base/auth/email/verify-otp/" `
  -ContentType "application/json" -Body (@{email=$email; code=$code} | ConvertTo-Json)
$token = $r.access
$h = @{ Authorization = "Bearer $token" }
"Token received (length: $($token.Length))"

Step "3) Upload a recording"
curl.exe -s -X POST "$base/recordings/" -H "Authorization: Bearer $token" `
  -F "audio=@test.mp3" -F "duration=240" -F "bpm=118"

Step "4) Recent recordings (limit=3)"
Invoke-RestMethod -Uri "$base/recordings/?limit=3" -Headers $h | ConvertTo-Json -Depth 5

Step "5) Download the audio file"
curl.exe -s -o downloaded.mp3 -w "HTTP %{http_code}`n" "$base/recordings/1/audio/" -H "Authorization: Bearer $token"

Step "6) Without token (should be 401)"
curl.exe -s -w "`nHTTP %{http_code}`n" "$base/recordings/"
