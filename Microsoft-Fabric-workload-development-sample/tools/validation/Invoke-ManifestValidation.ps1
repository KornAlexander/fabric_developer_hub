param(
    [Parameter(Mandatory=$true)]
    [string]$PackageDirectory,
    
    [Parameter(Mandatory=$true)]
    [string]$AppSettingsPath,
    
    [string]$SchemaBaseUrl = "https://schemas.microsoft.com/fabric/extensibility/xsd",
    [string]$CacheDirectory = "$env:TEMP\FabricXSDCache",
    [switch]$Force,
    [switch]$SkipCache,
    [switch]$CleanCache
)

<#
.SYNOPSIS
    Main validation script that downloads XSD schemas from Microsoft's official repository and validates manifest files.

.DESCRIPTION
    This script orchestrates the entire validation process:
    1. Detects schema versions from manifest files (component-specific)
    2. Downloads or retrieves cached XSD files from Microsoft's schema repository
    3. Runs validation scripts against the manifest files separately for each component
    4. Reports validation results

.PARAMETER PackageDirectory
    Path to the package directory containing manifest files to validate.
    Must contain WorkloadManifest.xml and any item manifest XML files.

.PARAMETER AppSettingsPath
    Path to the appsettings.json file for validation configuration.
    Example: "..\..\Backend\src\appsettings.json"

.PARAMETER SchemaBaseUrl
    Base URL of Microsoft's schema repository.
    Defaults to "https://schemas.microsoft.com/fabric/extensibility/xsd"

.PARAMETER CacheDirectory
    Root directory for XSD file cache. Defaults to %TEMP%\FabricXSDCache

.PARAMETER Force
    Force re-download of XSD files even if they exist in cache.

.PARAMETER SkipCache
    Skip cache entirely and always download fresh XSD files to a temporary location.

.PARAMETER CleanCache
    Clean old cache entries before validation (removes entries older than 30 days).

.EXAMPLE
    .\Invoke-ManifestValidation.ps1 -PackageDirectory "..\..\Backend\src\Packages\manifest" -AppSettingsPath "..\..\Backend\src\appsettings.json"
    Basic validation using default Microsoft schema repository.

.EXAMPLE
    .\Invoke-ManifestValidation.ps1 -PackageDirectory "..\..\Backend\src\Packages\manifest" -AppSettingsPath "..\..\Backend\src\appsettings.json" -Force -Verbose
    Force re-download XSDs and show verbose output.

.EXAMPLE
    .\Invoke-ManifestValidation.ps1 -PackageDirectory "..\..\Backend\src\Packages\manifest" -AppSettingsPath "..\..\Backend\src\appsettings.json" -SchemaBaseUrl "https://custom.schema.com/fabric/xsd"
    Use a custom schema repository URL.

.OUTPUTS
    Exit code 0 on success, non-zero on failure.
    Validation errors are written to ValidationErrors.txt in the script directory.
#>

# Set verbose preference based on parameter
if ($Verbose) {
    $VerbosePreference = "Continue"
}

# Initialize validation context
$script:ValidationStartTime = Get-Date
$script:ErrorCount = 0
$script:WarningCount = 0

function Write-ValidationLog {
    param(
        [string]$Message,
        [ValidateSet("Info", "Warning", "Error", "Success")]
        [string]$Level = "Info"
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $prefix = switch ($Level) {
        "Info"    { "[INFO]" }
        "Warning" { "[WARN]" }
        "Error"   { "[ERROR]" }
        "Success" { "[SUCCESS]" }
    }
    
    $logMessage = "$timestamp $prefix $Message"
    
    switch ($Level) {
        "Info"    { Write-Host $logMessage }
        "Warning" { Write-Warning $Message; $script:WarningCount++ }
        "Error"   { Write-Error $Message; $script:ErrorCount++ }
        "Success" { Write-Host $logMessage -ForegroundColor Green }
    }
    
    Write-Verbose $logMessage
}

function Test-Prerequisites {
    Write-ValidationLog "Checking prerequisites..."
    
    # Check if PackageDirectory exists
    if (-not (Test-Path -Path $PackageDirectory -PathType Container)) {
        Write-ValidationLog "Package directory not found: $PackageDirectory" -Level Error
        return $false
    }
    
    # Check if AppSettingsPath exists
    if (-not (Test-Path -Path $AppSettingsPath -PathType Leaf)) {
        Write-ValidationLog "AppSettings file not found: $AppSettingsPath" -Level Error
        return $false
    }
    
    # Check if WorkloadManifest.xml exists
    $workloadManifestPath = Join-Path $PackageDirectory "WorkloadManifest.xml"
    if (-not (Test-Path -Path $workloadManifestPath -PathType Leaf)) {
        Write-ValidationLog "WorkloadManifest.xml not found in package directory: $PackageDirectory" -Level Error
        return $false
    }
    
    # Check internet connectivity (basic test)
    try {
        $testConnection = Test-NetConnection -ComputerName "schemas.microsoft.com" -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
        if (-not $testConnection) {
            Write-ValidationLog "Cannot connect to Microsoft schema repository. Internet connection may be required for XSD download." -Level Warning
        }
    }
    catch {
        Write-ValidationLog "Could not test internet connectivity: $($_.Exception.Message)" -Level Warning
    }
    
    Write-ValidationLog "Prerequisites check completed" -Level Success
    return $true
}

function Get-ValidationScriptPath {
    param([string]$ScriptName)
    
    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    if (-not (Test-Path -Path $scriptPath)) {
        throw "Required validation script not found: $scriptPath"
    }
    return $scriptPath
}

try {
    Write-ValidationLog "Starting manifest validation process..."
    
    # Resolve PackageDirectory to absolute path to avoid path resolution issues
    $PackageDirectory = Resolve-Path $PackageDirectory -ErrorAction Stop
    
    Write-ValidationLog "Package Directory: $PackageDirectory"
    Write-ValidationLog "Microsoft Schema Repository: $SchemaBaseUrl"
    Write-ValidationLog "Cache Directory: $CacheDirectory"
    
    # Check prerequisites
    if (-not (Test-Prerequisites)) {
        throw "Prerequisites check failed"
    }
    
    # Clean cache if requested
    if ($CleanCache) {
        Write-ValidationLog "Cleaning old cache entries..."
        $cacheManagerPath = Get-ValidationScriptPath "Manage-ComponentXSDCache.ps1"
        $cleanedCount = & $cacheManagerPath -ComponentVersions @{WorkloadDefinition="1.0.0";ItemDefinition="1.0.0";CommonTypesDefinitions="1.0.0"} -CacheRootDirectory $CacheDirectory -Action "Clean" -Verbose:$Verbose
        Write-ValidationLog "Cleaned $cleanedCount old cache entries" -Level Success
    }
    
    # Step 1: Detect component schema versions
    Write-ValidationLog "Detecting component schema versions..."
    $versionDetectorPath = Get-ValidationScriptPath "Get-AllSchemaVersions.ps1"
    
    $componentVersions = & $versionDetectorPath -PackageDirectory $PackageDirectory -Verbose:$Verbose
    if (-not $componentVersions -or $componentVersions.Count -eq 0) {
        throw "Failed to detect component schema versions from manifest files"
    }
    
    # Update CommonTypesDefinitions to use "common" for the new architecture
    $componentVersions["CommonTypesDefinitions"] = "common"
    
    Write-ValidationLog "Detected component schema versions:" -Level Success
    foreach ($component in $componentVersions.Keys | Sort-Object) {
        Write-ValidationLog "  $component = $($componentVersions[$component])" -Level Info
    }
    
    # Step 2: Manage component XSD cache
    $componentCachePaths = @{}
    
    if ($SkipCache) {
        Write-ValidationLog "Skipping cache, using temporary directory..."
        $tempDirectory = Join-Path $env:TEMP "FabricXSD_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        New-Item -Path $tempDirectory -ItemType Directory -Force | Out-Null
        
        # Use same temp directory for all components
        foreach ($component in $componentVersions.Keys) {
            $componentCachePaths[$component] = $tempDirectory
        }
    }
    else {
        Write-ValidationLog "Checking component XSD cache..."
        $cacheManagerPath = Get-ValidationScriptPath "Manage-ComponentXSDCache.ps1"
        
        # Check if component versions are already cached (unless Force is specified)
        if (-not $Force) {
            $cachedPaths = & $cacheManagerPath -ComponentVersions $componentVersions -CacheRootDirectory $CacheDirectory -Action "Check" -Verbose:$Verbose
            if ($cachedPaths -and $cachedPaths.Count -gt 0) {
                Write-ValidationLog "Using cached XSD files" -Level Success
                $componentCachePaths = $cachedPaths
            }
        }
        
        # Create cache directories if not found or forcing
        if ($componentCachePaths.Count -eq 0) {
            Write-ValidationLog "Creating cache directories for component versions..."
            $componentCachePaths = & $cacheManagerPath -ComponentVersions $componentVersions -CacheRootDirectory $CacheDirectory -Action "Create" -Verbose:$Verbose
        }
    }
    
    # Step 3: Download component XSD files if needed
    $componentXsdMapping = @{
        "WorkloadDefinition" = "WorkloadDefinition.xsd"
        "ItemDefinition" = "ItemDefinition.xsd"
        "CommonTypesDefinitions" = "CommonTypesDefinitions.xsd"
    }
    
    $needsDownload = $Force -or $SkipCache
    $missingComponents = @()
    
    if (-not $needsDownload) {
        # Check if all required XSD files exist in their respective cache directories
        foreach ($component in $componentVersions.Keys) {
            $cacheDir = $componentCachePaths[$component]

            # Map component to appropriate XSD file
            if ($component -eq "WorkloadDefinition") {
                $xsdFile = "WorkloadDefinition.xsd"
            }
            elseif ($component -eq "CommonTypesDefinitions") {
                $xsdFile = "CommonTypesDefinitions.xsd"
            }
            else {
                # All item manifests use ItemDefinition.xsd
                $xsdFile = "ItemDefinition.xsd"
            }

            $xsdPath = Join-Path $cacheDir $xsdFile
            
            if (-not (Test-Path -Path $xsdPath)) {
                $needsDownload = $true
                $missingComponents += $component
            }
        }
    }
    
    if ($needsDownload) {
        Write-ValidationLog "Downloading component XSD files from Microsoft schema repository..."
        $downloaderPath = Get-ValidationScriptPath "Download-XSDComponents.ps1"
        
        # Map component versions to standard XSD component names
        $standardComponentVersions = @{}

        foreach ($component in $componentVersions.Keys) {
            if ($component -eq "WorkloadDefinition") {
                $standardComponentVersions["WorkloadDefinition"] = $componentVersions[$component]
            }
            elseif ($component -eq "CommonTypesDefinitions") {
                $standardComponentVersions["CommonTypesDefinitions"] = $componentVersions[$component]
            }
            else {
                # All item manifests use ItemDefinition XSD
                if (-not $standardComponentVersions.ContainsKey("ItemDefinition")) {
                    $standardComponentVersions["ItemDefinition"] = $componentVersions[$component]
                }
            }
        }

        # Download to each component's specific cache directory
        foreach ($component in $standardComponentVersions.Keys) {
            # Find the cache directory for this standard component
            if ($component -eq "WorkloadDefinition" -or $component -eq "CommonTypesDefinitions") {
                $cacheDir = $componentCachePaths[$component]
            } else {
                # For ItemDefinition, find any item component's cache directory
                $itemComponentName = $componentVersions.Keys | Where-Object { $_ -ne "WorkloadDefinition" -and $_ -ne "CommonTypesDefinitions" } | Select-Object -First 1
                $cacheDir = $componentCachePaths[$itemComponentName]
            }

            $version = $standardComponentVersions[$component]
            $singleComponentVersions = @{ $component = $version }
            
            Write-ValidationLog "Downloading $component v$version to $cacheDir"
            $downloadResult = & $downloaderPath -ComponentVersions $singleComponentVersions -SchemaBaseUrl $SchemaBaseUrl -OutputDirectory $cacheDir -Force:$Force -Verbose:$Verbose
            
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to download XSD files for $component from Microsoft schema repository"
            }
        }
        
        Write-ValidationLog "XSD files downloaded successfully" -Level Success
    }
    else {
        Write-ValidationLog "All required XSD files found in cache" -Level Success
    }
    
    # Verify all required XSD files are present
    Write-ValidationLog "Verifying XSD files..."
    foreach ($component in $componentVersions.Keys) {
        $cacheDir = $componentCachePaths[$component]
        $xsdFile = $componentXsdMapping[$component]
        $xsdPath = Join-Path $cacheDir $xsdFile
        
        if (-not (Test-Path -Path $xsdPath)) {
            throw "Required XSD file not found: $xsdPath"
        }
    }
    Write-ValidationLog "All XSD files verified" -Level Success
    
    # Step 4: Run validation scripts
    Write-ValidationLog "Starting manifest validation..."
    
    # Clean up any existing error files
    $removeErrorPath = Get-ValidationScriptPath "RemoveErrorFile.ps1"
    & $removeErrorPath -outputDirectory $PSScriptRoot -Verbose:$Verbose
    
    # Validate WorkloadManifest.xml
    Write-ValidationLog "Validating WorkloadManifest.xml..."
    $manifestValidatorPath = Get-ValidationScriptPath "ManifestValidator.ps1"
    $workloadXsdDir = $componentCachePaths["WorkloadDefinition"]
    & $manifestValidatorPath -inputDirectory $PackageDirectory -inputXml "WorkloadManifest.xml" -inputXsd "WorkloadDefinition.xsd" -outputDirectory $PSScriptRoot -xsdDirectory $workloadXsdDir -Verbose:$Verbose
    
    # Validate Item manifests
    Write-ValidationLog "Validating item manifests..."
    $itemValidatorPath = Get-ValidationScriptPath "ItemManifestValidator.ps1"
    
    # Find any item cache path (exclude WorkloadDefinition and CommonTypesDefinitions)
    $itemCacheKey = $componentCachePaths.Keys | Where-Object { $_ -ne "WorkloadDefinition" -and $_ -ne "CommonTypesDefinitions" } | Select-Object -First 1
    
    if (-not $itemCacheKey) {
        throw "No item cache paths found in componentCachePaths. Available paths: $($componentCachePaths.Keys -join ', ')"
    }
    
    $itemXsdDir = $componentCachePaths[$itemCacheKey]
    Write-ValidationLog "Using item XSD directory from $itemCacheKey`: $itemXsdDir"
    
    & $itemValidatorPath -inputDirectory $PackageDirectory -inputXsd "ItemDefinition.xsd" -outputDirectory $PSScriptRoot -xsdDirectory $itemXsdDir
    
    # Run additional validations
    Write-ValidationLog "Running additional validations..."
    $noDefaultsValidatorPath = Get-ValidationScriptPath "ValidateNoDefaults.ps1"
    & $noDefaultsValidatorPath -outputDirectory $PSScriptRoot -appsettingsLocation $AppSettingsPath -packageDirectory $PackageDirectory
    
    # Step 5: Check for validation errors
    $errorFilePath = Join-Path $PSScriptRoot "ValidationErrors.txt"
    if (Test-Path -Path $errorFilePath) {
        $errorContent = Get-Content -Path $errorFilePath -Raw
        if ($errorContent.Trim()) {
            Write-ValidationLog "Validation errors found:" -Level Error
            Write-Host $errorContent -ForegroundColor Red
            throw "Validation failed with errors"
        }
    }
    
    # Success!
    $duration = (Get-Date) - $script:ValidationStartTime
    Write-ValidationLog "Validation completed successfully in $($duration.TotalSeconds.ToString('F2')) seconds" -Level Success
    Write-ValidationLog "Component Schema Versions:" -Level Success
    foreach ($component in $componentVersions.Keys | Sort-Object) {
        Write-ValidationLog "  $component = $($componentVersions[$component])" -Level Success
    }
    Write-ValidationLog "XSD Source: $SchemaBaseUrl (Microsoft official repository)" -Level Success
    
    if ($script:WarningCount -gt 0) {
        Write-ValidationLog "Validation completed with $($script:WarningCount) warning(s)" -Level Warning
    }
    
    exit 0
}
catch {
    $duration = (Get-Date) - $script:ValidationStartTime
    Write-ValidationLog "Validation failed after $($duration.TotalSeconds.ToString('F2')) seconds: $($_.Exception.Message)" -Level Error
    
    exit 1
}
finally {
    # Cleanup temporary directory if used (guaranteed to run regardless of success/failure)
    if ($SkipCache -and $componentCachePaths.Count -gt 0) {
        Write-ValidationLog "Cleaning up temporary XSD directory..."
        $tempDirectory = $componentCachePaths.Values | Select-Object -First 1
        if ($tempDirectory -and (Test-Path -Path $tempDirectory)) {
            Remove-Item -Path $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}