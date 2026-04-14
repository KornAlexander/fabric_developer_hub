param (
	[string]$outputDirectory,
	[string]$appsettingsLocation = "appsettings.json",
	[string]$packageDirectory = ""
)

<#
.SYNOPSIS
    Validates that WorkloadManifest.xml values match appsettings.json values.

.DESCRIPTION
    This script checks that the AADApp configuration in WorkloadManifest.xml
    matches the corresponding values in appsettings.json file.

.PARAMETER outputDirectory
    Directory where validation errors will be written.

.PARAMETER appsettingsLocation
    Path to the appsettings.json file. Can be relative or absolute.

.PARAMETER packageDirectory
    Directory containing the WorkloadManifest.xml file. If not provided,
    will attempt to find it relative to the script location.

.EXAMPLE
    .\ValidateNoDefaults.ps1 -outputDirectory "C:\temp" -appsettingsLocation "..\..\Backend\src\appsettings.json"
#>

try
{
    if (-not($outputDirectory))
    {
        throw "Invalid input: outputDirectory parameter is required"
    }
    
    # Resolve appsettings path
    $appSettingsPath = $appsettingsLocation
    if (-not (Test-Path $appSettingsPath)) {
        throw "AppSettings file not found at: $appSettingsPath"
    }
    
    # Load appsettings.json
    $appSettingsContent = (Get-Content $appSettingsPath) -replace '// .*', '' -join [Environment]::NewLine | ConvertFrom-Json
    
    # Determine WorkloadManifest.xml path
    if ($packageDirectory -and (Test-Path $packageDirectory)) {
        $workloadXmlPath = Join-Path -Path $packageDirectory -ChildPath "WorkloadManifest.xml"
    } else {
        # Fallback to relative path for backward compatibility
        $workloadXmlPath = Join-Path -Path $PSScriptRoot -ChildPath "..\..\Backend\src\Packages\manifest\WorkloadManifest.xml"
    }
    
    if (-not (Test-Path $workloadXmlPath)) {
        throw "WorkloadManifest.xml not found at: $workloadXmlPath"
    }
    
    # Load and validate WorkloadManifest.xml
    $workloadXml = [xml](Get-Content -Path $workloadXmlPath)
    $aadApp = $workloadXml.SelectSingleNode("//AADApp")
    
    if (-not $aadApp) {
        throw "AADApp configuration not found in WorkloadManifest.xml"
    }
    
    # Check if values match
    if (($appSettingsContent.Audience -ne $aadApp.ResourceId) -or ($appSettingsContent.ClientId -ne $aadApp.AppId))
    {
        $scriptPath = Join-Path -Path $PSScriptRoot -ChildPath "WriteErrorsToFile.ps1"
        & $scriptPath -errors "Non matching default values in WorkloadManifest.xml file" -outputDirectory $outputDirectory
    }
}
catch
{
    Write-Host "An error occurred in ValidateNoDefaults.ps1:"
    Write-Host $_.Exception.Message
    
    # Write error to file for consistent error reporting
    $scriptPath = Join-Path -Path $PSScriptRoot -ChildPath "WriteErrorsToFile.ps1"
    if (Test-Path $scriptPath) {
        & $scriptPath -errors "ValidateNoDefaults.ps1 error: $($_.Exception.Message)" -outputDirectory $outputDirectory
    }
}