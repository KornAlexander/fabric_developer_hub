param(
    [Parameter(Mandatory=$false)]
    [hashtable]$ComponentVersions,
    
    [Parameter(Mandatory=$false)]
    [string]$Version,
    
    [string]$CacheRootDirectory = "$env:TEMP\FabricXSDCache",
    
    [ValidateSet("Check", "Create", "Clean", "List")]
    [string]$Action = "Check",
    
    [int]$MaxCacheAgeDays = 30
)

<#
.SYNOPSIS
    Manages local XSD cache with simplified version-based structure.

.DESCRIPTION
    This script provides cache management functionality for XSD files with simplified
    version-based storage. Each version gets its own directory, and CommonTypesDefinitions
    is stored in the root cache directory for XSD reference compatibility.

.PARAMETER ComponentVersions
    Hashtable containing the schema version for each component:
    @{
        WorkloadDefinition = "1.101.0"
        ItemDefinition = "1.102.0"
        CommonTypesDefinitions = "common"
    }

.PARAMETER Version
    Single version to operate on (alternative to ComponentVersions for single-version operations)

.PARAMETER CacheRootDirectory
    The root directory for the XSD cache. Defaults to %TEMP%\FabricXSDCache

.PARAMETER Action
    The action to perform:
    - Check: Check if the versions are cached and return cache paths
    - Create: Create cache directories for the versions
    - Clean: Remove cache entries older than MaxCacheAgeDays
    - List: List all cached versions

.PARAMETER MaxCacheAgeDays
    Maximum age in days for cache entries (used with Clean action). Defaults to 30 days.

.EXAMPLE
    $versions = @{
        WorkloadDefinition = "1.101.0"
        ItemDefinition = "1.102.0" 
        CommonTypesDefinitions = "common"
    }
    .\Manage-ComponentXSDCache.ps1 -ComponentVersions $versions -Action Check

.OUTPUTS
    For Check action: Returns hashtable of cache directory paths by component.
    For Create action: Returns hashtable of created cache directory paths.
    For Clean action: Returns count of cleaned entries.
    For List action: Returns array of cached version information.
#>

# Set verbose preference
if ($Verbose) {
    $VerbosePreference = "Continue"
}

# Component to XSD file mapping
$ComponentXsdMapping = @{
    "WorkloadDefinition" = "WorkloadDefinition.xsd"
    "ItemDefinition" = "ItemDefinition.xsd"
    "CommonTypesDefinitions" = "CommonTypesDefinitions.xsd"
}

function Get-VersionCacheDirectory {
    param(
        [string]$Version,
        [string]$CacheRoot
    )
    
    if ($Version -eq "common") {
        # CommonTypesDefinitions goes in root cache directory for XSD reference compatibility
        return $CacheRoot
    } else {
        return Join-Path $CacheRoot "v$Version"
    }
}

function Test-CacheIntegrity {
    param(
        [string]$CachePath,
        [string]$Component
    )
    
    Write-Verbose "Checking cache integrity for $Component at: $CachePath"
    
    if (-not (Test-Path -Path $CachePath -PathType Container)) {
        Write-Verbose "Cache directory does not exist: $CachePath"
        return $false
    }
    
    $xsdFile = $ComponentXsdMapping[$Component]
    $filePath = Join-Path $CachePath $xsdFile
    
    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        Write-Verbose "Missing XSD file: $filePath"
        return $false
    }
    
    try {
        # Verify it's valid XML
        $xml = [xml](Get-Content -Path $filePath -ErrorAction Stop)
        
        # Verify it's an XSD schema
        if ($xml.DocumentElement.LocalName -ne "schema" -or 
            $xml.DocumentElement.NamespaceURI -ne "http://www.w3.org/2001/XMLSchema") {
            Write-Verbose "Invalid XSD schema file: $filePath"
            return $false
        }
    }
    catch {
        Write-Verbose "Failed to parse XSD file $filePath`: $_"
        return $false
    }
    
    Write-Verbose "Cache integrity check passed for $Component at: $CachePath"
    return $true
}

function Get-CacheMetadata {
    param([string]$CachePath)
    
    $metadataFile = Join-Path $CachePath ".metadata"
    
    if (Test-Path -Path $metadataFile) {
        try {
            $metadata = Get-Content -Path $metadataFile -Raw | ConvertFrom-Json
            return $metadata
        }
        catch {
            Write-Verbose "Failed to read cache metadata: $_"
        }
    }
    
    # Return default metadata if file doesn't exist or is invalid
    return @{
        CreatedDate = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssZ")
        LastAccessDate = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssZ")
        CacheFormat = "VersionBased"
    }
}

function Set-CacheMetadata {
    param(
        [string]$CachePath,
        $Metadata  # Accept any type (hashtable or PSCustomObject)
    )
    
    $metadataFile = Join-Path $CachePath ".metadata"
    
    try {
        # Convert PSCustomObject to hashtable if needed
        if ($Metadata -is [PSCustomObject]) {
            $hashMetadata = @{}
            $Metadata.PSObject.Properties | ForEach-Object {
                $hashMetadata[$_.Name] = $_.Value
            }
            $hashMetadata | ConvertTo-Json -Depth 3 | Set-Content -Path $metadataFile -Force
        } else {
            $Metadata | ConvertTo-Json -Depth 3 | Set-Content -Path $metadataFile -Force
        }
        Write-Verbose "Updated cache metadata: $metadataFile"
    }
    catch {
        Write-Warning "Failed to update cache metadata: $_"
    }
}

try {
    Write-Verbose "Cache management action: $Action"
    Write-Verbose "Cache root directory: $CacheRootDirectory"

    # Determine which versions to work with
    $versionsToProcess = @{}
    
    if ($ComponentVersions) {
        $versionsToProcess = $ComponentVersions.Clone()
        Write-Verbose "Component versions: $(($ComponentVersions.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ')"
    } elseif ($Version) {
        # Single version operation - assume all components use this version except CommonTypes
        $versionsToProcess = @{
            WorkloadDefinition = $Version
            ItemDefinition = $Version
            CommonTypesDefinitions = "common"
        }
        Write-Verbose "Single version operation: $Version"
    }

    switch ($Action) {
        "Check" {
            Write-Verbose "Checking cache for component versions..."
            
            $cachePaths = @{}
            $allCached = $true
            
            foreach ($component in $versionsToProcess.Keys) {
                $version = $versionsToProcess[$component]
                $cacheDir = Get-VersionCacheDirectory -Version $version -CacheRoot $CacheRootDirectory
                
                if (Test-CacheIntegrity -CachePath $cacheDir -Component $component) {
                    # Update last access time
                    $metadata = Get-CacheMetadata -CachePath $cacheDir
                    $metadata.LastAccessDate = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssZ")
                    Set-CacheMetadata -CachePath $cacheDir -Metadata $metadata
                    
                    $cachePaths[$component] = $cacheDir
                    Write-Verbose "Cache found for $component v$version at: $cacheDir"
                } else {
                    $allCached = $false
                    Write-Verbose "Cache not found or invalid for $component v$version"
                }
            }
            
            if ($allCached) {
                Write-Output $cachePaths
            } else {
                Write-Output @{}
            }
        }
        
        "Create" {
            Write-Verbose "Creating cache directories for component versions..."
            
            $createdPaths = @{}
            
            foreach ($component in $versionsToProcess.Keys) {
                $version = $versionsToProcess[$component]
                $cacheDir = Get-VersionCacheDirectory -Version $version -CacheRoot $CacheRootDirectory
                
                if (-not (Test-Path -Path $cacheDir)) {
                    New-Item -Path $cacheDir -ItemType Directory -Force | Out-Null
                    Write-Verbose "Created cache directory for $component v$version`: $cacheDir"
                } else {
                    Write-Verbose "Cache directory already exists for $component v$version`: $cacheDir"
                }
                
                # Create/update metadata
                $metadata = @{
                    Version = $version
                    Component = $component
                    CreatedDate = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssZ")
                    LastAccessDate = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssZ")
                    CacheFormat = "VersionBased"
                }
                Set-CacheMetadata -CachePath $cacheDir -Metadata $metadata
                
                $createdPaths[$component] = $cacheDir
            }
            
            Write-Output $createdPaths
        }
        
        "Clean" {
            Write-Verbose "Cleaning cache entries older than $MaxCacheAgeDays days..."
            
            $cutoffDate = (Get-Date).AddDays(-$MaxCacheAgeDays)
            $cleanedCount = 0
            
            if (Test-Path -Path $CacheRootDirectory) {
                # Check CommonTypesDefinitions.xsd in root directory
                $commonTypesFile = Join-Path $CacheRootDirectory "CommonTypesDefinitions.xsd"
                if (Test-Path -Path $commonTypesFile) {
                    $metadata = Get-CacheMetadata -CachePath $CacheRootDirectory
                    
                    try {
                        $lastAccessDate = [DateTime]::Parse($metadata.LastAccessDate)
                        
                        if ($lastAccessDate -lt $cutoffDate) {
                            Write-Verbose "Removing old CommonTypesDefinitions cache (last accessed: $lastAccessDate)"
                            Remove-Item -Path $commonTypesFile -Force
                            # Also remove metadata and download summary if they exist
                            $metadataFile = Join-Path $CacheRootDirectory ".metadata"
                            $summaryFile = Join-Path $CacheRootDirectory "download-summary.json"
                            if (Test-Path -Path $metadataFile) { Remove-Item -Path $metadataFile -Force }
                            if (Test-Path -Path $summaryFile) { Remove-Item -Path $summaryFile -Force }
                            $cleanedCount++
                        }
                    }
                    catch {
                        Write-Warning "Failed to parse last access date for CommonTypesDefinitions, skipping cleanup"
                    }
                }
                
                # Clean versioned directories
                $cacheDirectories = Get-ChildItem -Path $CacheRootDirectory -Directory
                
                foreach ($cacheDir in $cacheDirectories) {
                    $fullCachePath = $cacheDir.FullName
                    $metadata = Get-CacheMetadata -CachePath $fullCachePath
                    
                    try {
                        $lastAccessDate = [DateTime]::Parse($metadata.LastAccessDate)
                        
                        if ($lastAccessDate -lt $cutoffDate) {
                            Write-Verbose "Removing old cache entry: $($cacheDir.Name) (last accessed: $lastAccessDate)"
                            Remove-Item -Path $fullCachePath -Recurse -Force
                            $cleanedCount++
                        }
                    }
                    catch {
                        Write-Warning "Failed to parse last access date for $($cacheDir.Name), skipping cleanup"
                    }
                }
            }
            
            Write-Verbose "Cleaned $cleanedCount cache entries"
            Write-Output $cleanedCount
        }
        
        "List" {
            Write-Verbose "Listing cached versions..."
            
            $cachedVersions = @()
            
            if (Test-Path -Path $CacheRootDirectory) {
                # First, check for CommonTypesDefinitions.xsd in the root cache directory
                $commonTypesFile = Join-Path $CacheRootDirectory "CommonTypesDefinitions.xsd"
                if (Test-Path -Path $commonTypesFile) {
                    $metadata = Get-CacheMetadata -CachePath $CacheRootDirectory
                    
                    $versionInfo = [PSCustomObject]@{
                        CacheDirectory = $CacheRootDirectory
                        DirectoryName = "(root)"
                        Version = "common"
                        AvailableComponents = @("CommonTypesDefinitions")
                        CreatedDate = $metadata.CreatedDate
                        LastAccessDate = $metadata.LastAccessDate
                        CacheFormat = $metadata.CacheFormat
                    }
                    
                    $cachedVersions += $versionInfo
                    Write-Verbose "Found CommonTypesDefinitions in root cache directory"
                }
                
                # Then check versioned directories
                $cacheDirectories = Get-ChildItem -Path $CacheRootDirectory -Directory
                
                foreach ($cacheDir in $cacheDirectories) {
                    $fullCachePath = $cacheDir.FullName
                    $metadata = Get-CacheMetadata -CachePath $fullCachePath
                    
                    # Determine version from directory name
                    $dirName = $cacheDir.Name
                    if ($dirName -match "^v(.+)$") {
                        $version = $matches[1]
                    } else {
                        $version = $dirName
                    }
                    
                    # Check what components are available in this cache
                    $availableComponents = @()
                    foreach ($component in $ComponentXsdMapping.Keys) {
                        # Skip CommonTypesDefinitions for versioned directories since it's in root
                        if ($component -eq "CommonTypesDefinitions") {
                            continue
                        }
                        
                        $xsdFile = $ComponentXsdMapping[$component]
                        $filePath = Join-Path $fullCachePath $xsdFile
                        if (Test-Path -Path $filePath) {
                            $availableComponents += $component
                        }
                    }
                    
                    $versionInfo = [PSCustomObject]@{
                        CacheDirectory = $fullCachePath
                        DirectoryName = $dirName
                        Version = $version
                        AvailableComponents = $availableComponents
                        CreatedDate = $metadata.CreatedDate
                        LastAccessDate = $metadata.LastAccessDate
                        CacheFormat = $metadata.CacheFormat
                    }
                    
                    $cachedVersions += $versionInfo
                    Write-Verbose "Found cached version: $version with components: $($availableComponents -join ', ')"
                }
            }
            
            Write-Verbose "Found $($cachedVersions.Count) cached entries total"
            Write-Output $cachedVersions
        }
    }
}
catch {
    Write-Error "Component cache management failed: $_"
    exit 1
}