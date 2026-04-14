param(
    [Parameter(Mandatory=$true)]
    [string]$PackageDirectory
)

<#
.SYNOPSIS
    Detects schema versions for all manifest components in a package directory.

.DESCRIPTION
    This script analyzes all manifest files in a package directory and extracts the schema 
    versions for each component type (WorkloadManifest and ItemManifests).

.PARAMETER PackageDirectory
    Path to the package directory containing manifest files.

.EXAMPLE
    .\Get-AllSchemaVersions.ps1 -PackageDirectory "..\..\Backend\src\Packages\manifest"
    Returns a hashtable with component versions.

.OUTPUTS
    Hashtable containing schema versions for each component:
    @{
        WorkloadDefinition = "1.101.0"
        Item1 = "1.102.0"
        Item2 = "1.103.0"
        CommonTypesDefinitions = "common"
    }
    
    Note: Individual item manifests are returned with their file names as keys.
#>

# Set verbose preference
if ($Verbose) {
    $VerbosePreference = "Continue"
}

try {
    Write-Verbose "Analyzing schema versions in package directory: $PackageDirectory"
    
    # Validate package directory exists
    if (-not (Test-Path -Path $PackageDirectory -PathType Container)) {
        throw "Package directory not found: $PackageDirectory"
    }

    $versions = @{}
    
    # 1. Detect WorkloadManifest schema version
    Write-Verbose "Detecting WorkloadManifest schema version..."
    $workloadManifestPath = Join-Path $PackageDirectory "WorkloadManifest.xml"
    
    if (-not (Test-Path -Path $workloadManifestPath -PathType Leaf)) {
        throw "WorkloadManifest.xml not found in package directory"
    }
    
    $workloadXml = [xml](Get-Content -Path $workloadManifestPath -ErrorAction Stop)
    $workloadVersion = $workloadXml.WorkloadManifestConfiguration.SchemaVersion
    
    if (-not $workloadVersion) {
        throw "SchemaVersion attribute not found in WorkloadManifestConfiguration"
    }
    
    if ($workloadVersion -notmatch '^\d+\.\d+\.\d+$') {
        Write-Warning "WorkloadManifest schema version format may be invalid: $workloadVersion"
    }
    
    $versions["WorkloadDefinition"] = $workloadVersion
    Write-Verbose "WorkloadDefinition schema version: $workloadVersion"
    
    # 2. Detect ItemManifest schema versions for each individual item
    Write-Verbose "Detecting ItemManifest schema versions..."
    $allXmls = Get-ChildItem -Path $PackageDirectory -Filter "*.xml"
    
    # Filter to only include files with ItemManifestConfiguration root element
    $itemXmls = @()
    foreach ($xmlFile in $allXmls) {
        Write-Verbose "Checking XML file: $($xmlFile.Name)"
        
        try {
            $xmlContent = [xml](Get-Content -Path $xmlFile.FullName -ErrorAction Stop)
            if ($xmlContent.DocumentElement.LocalName -eq "ItemManifestConfiguration") {
                $itemXmls += $xmlFile
                Write-Verbose "Detected item manifest: $($xmlFile.Name)"
            }
            else {
                Write-Verbose "Skipping non-item manifest: $($xmlFile.Name) (root element: $($xmlContent.DocumentElement.LocalName))"
            }
        }
        catch {
            Write-Warning "Failed to parse XML file $($xmlFile.Name): $($_.Exception.Message)"
        }
    }
    
    if ($itemXmls.Count -eq 0) {
        Write-Warning "No item manifests with ItemManifestConfiguration found in package directory"
        # Still add ItemDefinition with WorkloadDefinition version as fallback
        $versions["ItemDefinition"] = $workloadVersion
        Write-Verbose "Using WorkloadDefinition version as ItemDefinition fallback: $workloadVersion"
    }
    else {
        foreach ($itemXml in $itemXmls) {
            Write-Verbose "Processing item manifest: $($itemXml.Name)"
            
            try {
                $itemXmlContent = [xml](Get-Content -Path $itemXml.FullName -ErrorAction Stop)
                $itemVersion = $itemXmlContent.ItemManifestConfiguration.SchemaVersion
                
                if ($itemVersion) {
                    # Validate version format
                    if ($itemVersion -notmatch '^\d+\.\d+\.\d+$') {
                        Write-Warning "Item manifest $($itemXml.Name) schema version format may be invalid: $itemVersion"
                        # Try to fix simple cases like "1" -> "1.0.0"
                        if ($itemVersion -match '^\d+$') {
                            $itemVersion = "$itemVersion.0.0"
                            Write-Verbose "Auto-corrected version to: $itemVersion"
                        }
                    }
                    
                    # Use the item file name (without .xml) as the component key
                    $itemName = $itemXml.BaseName
                    $versions[$itemName] = $itemVersion
                    Write-Verbose "Item manifest $($itemXml.Name) schema version: $itemVersion"
                }
                else {
                    Write-Warning "SchemaVersion attribute not found in $($itemXml.Name)"
                    # Use WorkloadDefinition version as fallback for this item
                    $itemName = $itemXml.BaseName
                    $versions[$itemName] = $workloadVersion
                    Write-Verbose "Using WorkloadDefinition version as fallback for $($itemXml.Name): $workloadVersion"
                }
            }
            catch {
                Write-Warning "Failed to parse item manifest $($itemXml.Name): $($_.Exception.Message)"
                # Use WorkloadDefinition version as fallback for this item
                $itemName = $itemXml.BaseName
                $versions[$itemName] = $workloadVersion
                Write-Verbose "Using WorkloadDefinition version as fallback for $($itemXml.Name) due to parse error: $workloadVersion"
            }
        }
    }
    
    # 3. Add CommonTypesDefinitions as version-agnostic
    $versions["CommonTypesDefinitions"] = "common"
    Write-Verbose "CommonTypesDefinitions schema version: common (version-agnostic)"
    
    # 4. Validate all versions are reasonable
    Write-Verbose "Validating detected schema versions..."
    foreach ($component in $versions.Keys) {
        $version = $versions[$component]
        
        # Skip validation for CommonTypesDefinitions (always "common")
        if ($component -eq "CommonTypesDefinitions") {
            if ($version -ne "common") {
                Write-Warning "CommonTypesDefinitions should be 'common', got: $version"
            }
            continue
        }
        
        # Validate semantic versioning for other components
        try {
            $versionObj = [Version]$version
            if ($versionObj.Major -lt 1) {
                Write-Warning "$component version seems too low: $version"
            }
        }
        catch {
            Write-Warning "$component version format invalid: $version"
        }
    }
    
    # 4. Add CommonTypesDefinitions as version-agnostic
    $versions["CommonTypesDefinitions"] = "common"
    Write-Verbose "CommonTypesDefinitions schema version: common (version-agnostic)"
    
    # Output summary
    Write-Verbose "Schema version detection completed successfully"
    Write-Verbose "Detected versions:"
    foreach ($component in $versions.Keys | Sort-Object) {
        Write-Verbose "  $component = $($versions[$component])"
    }
    
    # Return the versions hashtable
    Write-Output $versions
    
}
catch {
    Write-Error "Failed to detect schema versions: $_"
    exit 1
}