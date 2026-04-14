param(
    [Parameter(Mandatory=$true)]
    [hashtable]$ComponentVersions,
    
    [string]$SchemaBaseUrl = "https://schemas.microsoft.com/fabric/extensibility/xsd",
    
    [Parameter(Mandatory=$true)]
    [string]$OutputDirectory,
    
    [switch]$Force
)

<#
.SYNOPSIS
    Downloads individual XSD schema files from Microsoft's schema repository based on component-specific versions.

.DESCRIPTION
    This script downloads XSD files for each component (WorkloadDefinition, ItemDefinition,
    CommonTypesDefinitions) from Microsoft's official schema repository. Each component can
    have a different version, and CommonTypesDefinitions is version-agnostic.

.PARAMETER ComponentVersions
    Hashtable containing the schema version for each component:
    @{
        WorkloadDefinition = "1.101.0"
        ItemDefinition = "1.102.0"
        CommonTypesDefinitions = "common"
    }

.PARAMETER SchemaBaseUrl
    The base URL of Microsoft's schema repository.
    Defaults to "https://schemas.microsoft.com/fabric/extensibility/xsd"

.PARAMETER OutputDirectory
    The local directory where XSD files will be saved.

.PARAMETER Force
    Force download even if files already exist in the output directory.

.EXAMPLE
    $versions = @{
        WorkloadDefinition = "1.101.0"
        ItemDefinition = "1.102.0"
        CommonTypesDefinitions = "common"
    }
    .\Download-XSDComponents.ps1 -ComponentVersions $versions -OutputDirectory "C:\temp\xsd-cache"

.OUTPUTS
    Returns hashtable with download results for each component.
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

try {
    Write-Verbose "Starting component-based XSD download..."
    Write-Verbose "Microsoft Schema Repository: $SchemaBaseUrl"
    Write-Verbose "Output Directory: $OutputDirectory"
    
    # Validate input parameters
    if (-not $ComponentVersions -or $ComponentVersions.Count -eq 0) {
        throw "ComponentVersions parameter is required and cannot be empty"
    }

    # Validate provided components (flexible - doesn't require all components)
    $validComponents = @("WorkloadDefinition", "ItemDefinition", "CommonTypesDefinitions")
    $componentsToProcess = @()
    
    foreach ($component in $ComponentVersions.Keys) {
        if ($component -notin $validComponents) {
            throw "Invalid component: $component. Valid components are: $($validComponents -join ', ')"
        }
        
        $version = $ComponentVersions[$component]
        
        # Validate version format for each component
        if ($component -eq "CommonTypesDefinitions") {
         if ($version -ne "common") {
            throw "CommonTypesDefinitions must use 'common' as version, got: $version"
         }
        } else {
         if ($version -notmatch '^\d+\.\d+\.\d+$') {
            throw "Invalid version format for $component`: $version (expected format: x.y.z)"
         }
        }

        $componentsToProcess += $component
    }

    if ($componentsToProcess.Count -eq 0) {
        throw "No valid components provided for download"
    }
    
    # Display component versions
    Write-Verbose "Component versions to download:"
    foreach ($component in $componentsToProcess) {
        Write-Verbose "  $component = $($ComponentVersions[$component])"
    }
    
    # Create output directory if it doesn't exist
    if (-not (Test-Path -Path $OutputDirectory)) {
        Write-Verbose "Creating output directory: $OutputDirectory"
        New-Item -Path $OutputDirectory -ItemType Directory -Force | Out-Null
    }
    
    # Track download results
    $downloadResults = @{}
    $downloadedFiles = @()
    $failedFiles = @()
    $skippedFiles = @()
    
    # Download each component's XSD file
    foreach ($component in $componentsToProcess) {
        $version = $ComponentVersions[$component]
        $xsdFileName = $ComponentXsdMapping[$component]
        $filePath = Join-Path $OutputDirectory $xsdFileName
        
        Write-Verbose "Processing $component (v$version) -> $xsdFileName"
        
        # Check if file already exists and not forcing
        if ((Test-Path -Path $filePath) -and (-not $Force)) {
            Write-Verbose "Skipping existing file: $xsdFileName"
            $skippedFiles += $xsdFileName
            $downloadResults[$component] = @{
                Status = "Skipped"
                Version = $version
                FilePath = $filePath
                Message = "File already exists"
            }
            continue
        }
        
        try {
            # Construct download URL based on component type
            if ($component -eq "CommonTypesDefinitions") {
                # CommonTypesDefinitions is version-agnostic
                $url = "$($SchemaBaseUrl.TrimEnd('/'))/$xsdFileName"
            } else {
                # Versioned components
                $url = "$($SchemaBaseUrl.TrimEnd('/'))/$version/$xsdFileName"
            }
            
            Write-Verbose "Downloading $url to $filePath"
            
            # Download with retry logic
            $maxRetries = 3
            $retryCount = 0
            $downloadSuccess = $false
            $lastError = $null
            
            while (-not $downloadSuccess -and $retryCount -lt $maxRetries) {
                try {
                    Invoke-WebRequest -Uri $url -OutFile $filePath -ErrorAction Stop
                    $downloadSuccess = $true
                }
                catch {
                    $lastError = $_
                    $retryCount++
                    if ($retryCount -lt $maxRetries) {
                        Write-Warning "Download failed for $xsdFileName (attempt $retryCount/$maxRetries): $($_.Exception.Message). Retrying..."
                        Start-Sleep -Seconds 2
                    }
                }
            }
            
            if (-not $downloadSuccess) {
                throw $lastError
            }
            
            # Verify the downloaded file is valid XML
            Write-Verbose "Validating downloaded XML file: $xsdFileName"
            $testXml = [xml](Get-Content -Path $filePath -ErrorAction Stop)
            
            # Verify it's actually an XSD schema
            if ($testXml.DocumentElement.LocalName -ne "schema" -or 
                $testXml.DocumentElement.NamespaceURI -ne "http://www.w3.org/2001/XMLSchema") {
                throw "Downloaded file is not a valid XSD schema"
            }
            
            Write-Verbose "Successfully downloaded and verified: $xsdFileName (v$version)"
            $downloadedFiles += $xsdFileName
            
            $downloadResults[$component] = @{
                Status = "Downloaded"
                Version = $version
                FilePath = $filePath
                Url = $url
                Message = "Successfully downloaded and verified"
            }
            
        }
        catch {
            $errorMessage = "Failed to download $xsdFileName (v$version) from $url`: $($_.Exception.Message)"
            Write-Error $errorMessage
            $failedFiles += $xsdFileName
            
            $downloadResults[$component] = @{
                Status = "Failed"
                Version = $version
                FilePath = $filePath
                Url = $url
                Message = $_.Exception.Message
            }
        }
    }
    
    # Summary
    Write-Verbose "Download completed:"
    Write-Verbose "  Downloaded: $($downloadedFiles.Count) files"
    Write-Verbose "  Skipped: $($skippedFiles.Count) files"  
    Write-Verbose "  Failed: $($failedFiles.Count) files"
    
    if ($downloadedFiles.Count -gt 0) {
        Write-Verbose "Downloaded files: $($downloadedFiles -join ', ')"
    }
    
    if ($skippedFiles.Count -gt 0) {
        Write-Verbose "Skipped files: $($skippedFiles -join ', ')"
    }
    
    if ($failedFiles.Count -gt 0) {
        throw "Failed to download the following XSD files: $($failedFiles -join ', ')"
    }
    
    # Create a summary file in the output directory
    $summaryPath = Join-Path $OutputDirectory "download-summary.json"
    $summary = @{
        DownloadDate = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssZ")
        SchemaRepository = $SchemaBaseUrl
        ComponentVersions = $ComponentVersions
        DownloadResults = $downloadResults
        Statistics = @{
            Downloaded = $downloadedFiles.Count
            Skipped = $skippedFiles.Count
            Failed = $failedFiles.Count
        }
    }
    
    $summary | ConvertTo-Json -Depth 4 | Set-Content -Path $summaryPath -Force
    Write-Verbose "Download summary saved to: $summaryPath"
    
    $successCount = $downloadedFiles.Count + $skippedFiles.Count
    Write-Output "Successfully processed $successCount/$($componentsToProcess.Count) XSD components"
    
    # Return the download results for use by calling scripts
    Write-Output $downloadResults
    exit 0
    
}
catch {
    Write-Error "XSD component download failed: $_"
    exit 1
}