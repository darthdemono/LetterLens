import os
import subprocess

def create_sample_image():
    ps = r'''
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap(650, 260)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::FromArgb(250, 250, 252))

$fontTitle = New-Object System.Drawing.Font('Arial', 14, [System.Drawing.FontStyle]::Bold)
$fontBody = New-Object System.Drawing.Font('Arial', 11)
$fontHeader = New-Object System.Drawing.Font('Arial', 9)

$brushHeader = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(100, 116, 139))
$brushTitle = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(15, 23, 42))
$brushBody = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(51, 65, 85))
$penBorder = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(203, 213, 225), 1)

$g.DrawRectangle($penBorder, 10, 10, 630, 240)
$g.DrawString("BUDAPEST METROPOLITAN HOUSING AUTHORITY", $fontHeader, $brushHeader, 25, 25)
$g.DrawString("FINAL PAYMENT AND RENEWAL NOTICE", $fontTitle, $brushTitle, 25, 50)
$g.DrawString("Dear Tenant, your annual lease renewal requires a mandatory utility indexation", $fontBody, $brushBody, 25, 90)
$g.DrawString("adjustment of 8,500 HUF starting from January 1, 2027.", $fontBody, $brushBody, 25, 115)
$g.DrawString("DEADLINE: Sign and return the confirmation form by November 25, 2026.", $fontBody, $brushBody, 25, 150)
$g.DrawString("PENALTY: Late submissions will incur a 2,500 HUF administrative surcharge.", $fontBody, $brushBody, 25, 175)
$g.DrawString("Contact: leasing@budapesthousing.hu | Reference: #BH-2026-9921", $fontHeader, $brushHeader, 25, 210)

$outPath = [System.IO.Path]::GetFullPath("samples/sample_letter_scan.png")
$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
Write-Output "SAVED: $outPath"
'''
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], capture_output=True, text=True)
    print("PS STDOUT:", res.stdout.strip())
    print("PS STDERR:", res.stderr.strip())

if __name__ == "__main__":
    create_sample_image()
