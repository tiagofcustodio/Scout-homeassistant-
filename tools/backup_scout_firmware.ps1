# Script to backup critical Moorebot Scout partitions and system files to local PC
$backupDir = "$PSScriptRoot\..\firmware_backup"
if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir | Out-Null
}

Write-Host "=== Moorebot Scout Firmware & Config Backup ===" -ForegroundColor Cyan
Write-Host "Target directory: $backupDir"

# 1. Backup bootloader, kernel, resource and OEM partitions
$partitions = @(
    @{ Name = "p1_uboot"; Dev = "/dev/mmcblk0p1" },
    @{ Name = "p2_trust"; Dev = "/dev/mmcblk0p2" },
    @{ Name = "p3_misc"; Dev = "/dev/mmcblk0p3" },
    @{ Name = "p4_boot_resource"; Dev = "/dev/mmcblk0p4" },
    @{ Name = "p5_kernel_recovery"; Dev = "/dev/mmcblk0p5" },
    @{ Name = "p7_oem"; Dev = "/dev/mmcblk0p7" }
)

foreach ($p in $partitions) {
    $outFile = "$backupDir\$($p.Name).img.gz"
    if (-not (Test-Path $outFile)) {
        Write-Host "Dumping $($p.Name) ($($p.Dev))..." -ForegroundColor Yellow
        ssh -o StrictHostKeyChecking=no root@192.168.1.115 "dd if=$($p.Dev) bs=1M status=none | gzip -c" > $outFile
        Write-Host "Saved: $outFile" -ForegroundColor Green
    } else {
        Write-Host "Partition $($p.Name) already backed up." -ForegroundColor Gray
    }
}

# 2. Backup system configurations and ROS roller_eye packages
$configArchive = "$backupDir\scout_configs_and_ros.tar.gz"
Write-Host "Archiving /var/roller_eye, /etc, /opt/ros/melodic/share/roller_eye..." -ForegroundColor Yellow
ssh -o StrictHostKeyChecking=no root@192.168.1.115 "tar -czf - /var/roller_eye /etc /opt/ros/melodic/share/roller_eye 2>/dev/null" > $configArchive
Write-Host "Saved configs archive: $configArchive" -ForegroundColor Green

Write-Host "=== Backup completed successfully! ===" -ForegroundColor Cyan
