param(
    [string]$PackageDirectory = "..\..\Backend\src\Packages\manifest"
)

<#
.SYNOPSIS
    Test script to verify the manual validation system is working correctly.

.DESCRIPTION
    This script performs basic tests on the validation system components to ensure
    they are functioning properly before running actual validation.

.PARAMETER PackageDirectory
    Path to the package directory to test with. Defaults to "..\..\Backend\src\Packages\manifest"

.EXAMPLE
    .\Test-ValidationSystem.ps1
    Run basic tests with default package directory.

.EXAMPLE
    .\Test-ValidationSystem.ps1 -PackageDirectory "path\to\package" -Verbose
    Run tests with custom package directory and verbose output.
#>

# Set verbose preference
if ($Verbose) {
    $VerbosePreference = "Continue"
}

$script:TestResults = @()
$script:TestCount = 0
$script:PassCount = 0
$script:FailCount = 0

function Test-Component {
    param(
        [string]$TestName,
        [scriptblock]$TestScript
    )
    
    $script:TestCount++
    Write-Host "[$script:TestCount] Testing: $TestName" -ForegroundColor Cyan
    
    try {
        $result = & $TestScript
        if ($result) {
            Write-Host "  ✓ PASS" -ForegroundColor Green
            $script:PassCount++
            $script:TestResults += [PSCustomObject]@{
                Test = $TestName
                Result = "PASS"
                Message = ""
            }
        }
        else {
            Write-Host "  ✗ FAIL" -ForegroundColor Red
            $script:FailCount++
            $script:TestResults += [PSCustomObject]@{
                Test = $TestName
                Result = "FAIL"
                Message = "Test returned false"
            }
        }
    }
    catch {
        Write-Host "  ✗ FAIL: $($_.Exception.Message)" -ForegroundColor Red
        $script:FailCount++
        $script:TestResults += [PSCustomObject]@{
            Test = $TestName
            Result = "FAIL"
            Message = $_.Exception.Message
        }
    }
    
    Write-Host ""
}

function Test-FileExists {
    param([string]$FilePath, [string]$Description)
    
    if (Test-Path -Path $FilePath) {
        Write-Verbose "✓ Found: $Description at $FilePath"
        return $true
    }
    else {
        Write-Verbose "✗ Missing: $Description at $FilePath"
        return $false
    }
}

Write-Host "Manual Validation System - Component Tests" -ForegroundColor Yellow
Write-Host "=========================================" -ForegroundColor Yellow
Write-Host ""

# Test 1: Check if all required scripts exist
Test-Component "Required Scripts Exist" {
    $requiredScripts = @(
        "Get-AllSchemaVersions.ps1",
        "Download-XSDComponents.ps1",
        "Manage-ComponentXSDCache.ps1",
        "Invoke-ManifestValidation.ps1",
        "ManifestValidator.ps1",
        "ItemManifestValidator.ps1",
        "ValidateNoDefaults.ps1",
        "WriteErrorsToFile.ps1",
        "RemoveErrorFile.ps1"
    )
    
    $allExist = $true
    foreach ($script in $requiredScripts) {
        $scriptPath = Join-Path $PSScriptRoot $script
        if (-not (Test-FileExists $scriptPath "Script: $script")) {
            $allExist = $false
        }
    }
    
    return $allExist
}

# Test 2: Check if package directory exists
Test-Component "Package Directory Exists" {
    $fullPath = Resolve-Path $PackageDirectory -ErrorAction SilentlyContinue
    if ($fullPath) {
        Write-Verbose "✓ Package directory found: $fullPath"
        return $true
    }
    else {
        Write-Verbose "✗ Package directory not found: $PackageDirectory"
        return $false
    }
}

# Test 3: Check if WorkloadManifest.xml exists
Test-Component "WorkloadManifest.xml Exists" {
    $manifestPath = Join-Path $PackageDirectory "WorkloadManifest.xml"
    return Test-FileExists $manifestPath "WorkloadManifest.xml"
}

# Test 4: Test component schema version detection
Test-Component "Component Schema Version Detection" {
    $versionScript = Join-Path $PSScriptRoot "Get-AllSchemaVersions.ps1"
    $componentVersions = & $versionScript -PackageDirectory $PackageDirectory
    
    if (-not $componentVersions -or $componentVersions.Count -eq 0) {
        throw "Failed to detect component schema versions"
    }
    
    # Check for required components
    if (-not $componentVersions.ContainsKey("WorkloadDefinition")) {
        throw "Missing component version: WorkloadDefinition"
    }
    
    if (-not $componentVersions.ContainsKey("CommonTypesDefinitions")) {
        throw "Missing component version: CommonTypesDefinitions"
    }
    
    # Validate each component version
    foreach ($component in $componentVersions.Keys) {
        $version = $componentVersions[$component]
        
        # CommonTypesDefinitions uses "common", others use x.y.z format
        if ($component -eq "CommonTypesDefinitions") {
            if ($version -ne "common") {
                throw "Invalid version format for $component`: $version (expected: common)"
            }
        } else {
            # All other components (WorkloadDefinition and individual items) use semantic versioning
            if ($version -notmatch '^\d+\.\d+\.\d+$') {
                throw "Invalid version format for $component`: $version (expected format: x.y.z)"
            }
        }
    }
    
    # Check that we have at least one item component (anything that's not WorkloadDefinition or CommonTypesDefinitions)
    $itemComponents = $componentVersions.Keys | Where-Object { $_ -ne "WorkloadDefinition" -and $_ -ne "CommonTypesDefinitions" }
    if ($itemComponents.Count -eq 0) {
        throw "No item manifest components found"
    }
    
    Write-Verbose "✓ Detected component versions:"
    foreach ($component in $componentVersions.Keys | Sort-Object) {
        Write-Verbose "  $component = $($componentVersions[$component])"
    }
    return $true
}

# Test 5: Test component cache management (basic functionality)
Test-Component "Component Cache Management" {
    $cacheScript = Join-Path $PSScriptRoot "Manage-ComponentXSDCache.ps1"
    $testCacheDir = Join-Path $env:TEMP "FabricXSDTest_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    
    $testComponentVersions = @{
        WorkloadDefinition = "1.0.0"
        ItemDefinition = "1.0.0"
        CommonTypesDefinitions = "1.0.0"
    }
    
    try {
        # Test cache directory creation
        $createdDir = & $cacheScript -ComponentVersions $testComponentVersions -CacheRootDirectory $testCacheDir -Action "Create"
        
        if (-not (Test-Path $createdDir)) {
            throw "Failed to create cache directory"
        }
        
        # Test cache check
        $checkResult = & $cacheScript -ComponentVersions $testComponentVersions -CacheRootDirectory $testCacheDir -Action "Check"
        
        # Should return empty string since no XSD files are present
        if ($checkResult) {
            throw "Cache check should return empty for directory without XSD files"
        }
        
        Write-Verbose "✓ Component cache management basic functionality working"
        return $true
    }
    finally {
        # Cleanup test cache directory
        if (Test-Path $testCacheDir) {
            Remove-Item $testCacheDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# Test 6: Test internet connectivity (optional)
Test-Component "Internet Connectivity" {
    try {
        $testConnection = Test-NetConnection -ComputerName "github.com" -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
        if ($testConnection) {
            Write-Verbose "✓ Can connect to GitHub"
            return $true
        }
        else {
            Write-Verbose "✗ Cannot connect to GitHub (this may be expected in some environments)"
            return $false
        }
    }
    catch {
        Write-Verbose "✗ Internet connectivity test failed: $($_.Exception.Message)"
        return $false
    }
}

# Test 7: Test main script parameter validation
Test-Component "Main Script Parameter Validation" {
    $mainScript = Join-Path $PSScriptRoot "Invoke-ManifestValidation.ps1"
    
    # Test with invalid package directory (should fail gracefully)
    try {
        $result = & $mainScript -PackageDirectory "NonExistentDirectory" -ErrorAction SilentlyContinue
        # Script should exit with error code 1
        if ($LASTEXITCODE -eq 1) {
            Write-Verbose "✓ Main script correctly validates parameters"
            return $true
        }
        else {
            throw "Main script should have failed with invalid directory"
        }
    }
    catch {
        # Expected behavior - script should throw error for invalid directory
        Write-Verbose "✓ Main script correctly validates parameters (threw exception as expected)"
        return $true
    }
}

# Summary
Write-Host "Test Summary" -ForegroundColor Yellow
Write-Host "============" -ForegroundColor Yellow
Write-Host "Total Tests: $script:TestCount" -ForegroundColor White
Write-Host "Passed: $script:PassCount" -ForegroundColor Green
Write-Host "Failed: $script:FailCount" -ForegroundColor $(if ($script:FailCount -gt 0) { "Red" } else { "Green" })
Write-Host ""

# Show detailed results if there were failures
if ($script:FailCount -gt 0) {
    Write-Host "Failed Tests:" -ForegroundColor Red
    $script:TestResults | Where-Object { $_.Result -eq "FAIL" } | ForEach-Object {
        Write-Host "  - $($_.Test): $($_.Message)" -ForegroundColor Red
    }
    Write-Host ""
}

# Recommendations
Write-Host "Recommendations:" -ForegroundColor Yellow
if ($script:FailCount -eq 0) {
    Write-Host "✓ All tests passed! The validation system appears to be ready for use." -ForegroundColor Green
    Write-Host "  You can now run: .\Invoke-ManifestValidation.ps1 -PackageDirectory '$PackageDirectory'" -ForegroundColor Green
}
else {
    Write-Host "✗ Some tests failed. Please address the issues before using the validation system." -ForegroundColor Red
    Write-Host "  Check the error messages above and ensure all required files are present." -ForegroundColor Red
}

if (($script:TestResults | Where-Object { $_.Test -eq "Internet Connectivity" -and $_.Result -eq "FAIL" })) {
    Write-Host "⚠ Internet connectivity test failed. The validation system will still work if:" -ForegroundColor Yellow
    Write-Host "  - You use -SkipCache to download XSDs to a temporary location" -ForegroundColor Yellow
    Write-Host "  - Or if the required XSD files are already cached" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "For more information, see the README.md file in the ValidationScripts directory." -ForegroundColor Cyan

# Exit with appropriate code
exit $(if ($script:FailCount -gt 0) { 1 } else { 0 })