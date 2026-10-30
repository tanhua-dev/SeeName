param(
    [string]$Python = 'python',
    [string]$Version = 'v0.0.0-dev',
    [string]$OutputDirectory = '',
    [string]$WorkDirectory = '',
    [string]$VerifyModelDirectory = '',
    [string]$VulkanCatalog = ''
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script must run on Windows.' }
$buildArgs = @('-B', (Join-Path $PSScriptRoot 'scripts\build_windows.py'), '--version', $Version)
if ($OutputDirectory) { $buildArgs += @('--output', $OutputDirectory) }
if ($WorkDirectory) { $buildArgs += @('--work-root', $WorkDirectory) }
if ($VerifyModelDirectory) { $buildArgs += @('--verify-model-directory', $VerifyModelDirectory) }
if ($VulkanCatalog) { $buildArgs += @('--vulkan-catalog', $VulkanCatalog) }

# Use the selected Python as-is. Dependency installation is a separate, explicit
# step so local builds never silently alter a Conda environment.
& $Python @buildArgs
exit $LASTEXITCODE
