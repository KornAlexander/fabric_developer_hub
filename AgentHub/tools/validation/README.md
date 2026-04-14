# Manual Manifest Validation System

This documentation describes how to use the manual validation system that downloads XSD schemas from Microsoft's official schema repository based on component-specific schema versions found in your manifest files.

## Overview

The validation system has been updated to work independently of the build process. Instead of using local XSD files, it automatically downloads the correct XSD schemas from Microsoft's official schema repository based on the schema versions specified in your manifest files. Each component (WorkloadDefinition, ItemDefinition) can have different schema versions, while CommonTypesDefinitions uses a single version-agnostic file.

## Quick Start

Basic validation of a package directory:

```powershell
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json"
```

## Components

### Main Script
- **`Invoke-ManifestValidation.ps1`** - Main orchestration script that coordinates the entire validation process

### Core Components
- **`Get-AllSchemaVersions.ps1`** - Detects schema versions for all components from manifest files
- **`Download-XSDComponents.ps1`** - Downloads XSD files based on component-specific versions
- **`Manage-ComponentXSDCache.ps1`** - Manages local cache for component-specific versions

### Updated Validation Scripts
- **`ManifestValidator.ps1`** - Core XSD validation (updated to support external XSD directory)
- **`ItemManifestValidator.ps1`** - Item manifest validation (updated to support external XSD directory)
- **`ValidateNoDefaults.ps1`** - Default value validation (unchanged)
- **`WriteErrorsToFile.ps1`** - Error reporting (unchanged)
- **`RemoveErrorFile.ps1`** - Error file cleanup (unchanged)

## Usage Examples

### Basic Validation
```powershell
# Validate package using default Microsoft repository
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json"
```

### Force Re-download
```powershell
# Force re-download of XSD files even if cached
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -Force
```

### Verbose Output
```powershell
# Show detailed execution information
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -Verbose
```

### Custom Schema Repository
```powershell
# Use a different schema repository for XSD files
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -SchemaBaseUrl "https://custom.schema.com/fabric/xsd"
```

### Skip Cache
```powershell
# Always download fresh XSD files to temporary location
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -SkipCache
```

### Clean Cache
```powershell
# Clean old cache entries before validation
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -CleanCache
```

## Parameters

### Invoke-ManifestValidation.ps1

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `PackageDirectory` | Yes | - | Path to package directory containing manifest files |
| `AppSettingsPath` | Yes | - | Path to appsettings.json file for validation configuration |
| `SchemaBaseUrl` | No | `https://schemas.microsoft.com/fabric/extensibility/xsd` | Microsoft schema repository base URL |
| `CacheDirectory` | No | `%TEMP%\FabricXSDCache` | Root directory for XSD cache |
| `Force` | No | `false` | Force re-download even if files are cached |
| `SkipCache` | No | `false` | Skip cache entirely, use temporary directory |
| `Verbose` | No | `false` | Enable verbose logging |
| `CleanCache` | No | `false` | Clean old cache entries before validation |

## Microsoft Schema Repository Structure

The validation system downloads XSD files from Microsoft's official schema repository:

```
https://schemas.microsoft.com/fabric/extensibility/xsd/
├── 1.100.0/
│   ├── WorkloadDefinition.xsd
│   └── ItemDefinition.xsd
├── 1.101.0/
│   ├── WorkloadDefinition.xsd
│   └── ItemDefinition.xsd
├── 1.102.0/
│   ├── WorkloadDefinition.xsd
│   └── ItemDefinition.xsd
└── CommonTypesDefinitions.xsd (version-agnostic)
```

### XSD File Organization
- **Versioned XSDs**: Each version folder contains component-specific XSD files
  - `WorkloadDefinition.xsd` - Schema for WorkloadManifest.xml
  - `ItemDefinition.xsd` - Schema for item manifest XML files
- **Common Types**: Single version-agnostic file at repository root
  - `CommonTypesDefinitions.xsd` - Common type definitions referenced by all schemas

## Schema Version Detection

The system automatically detects schema versions from multiple sources:

### Component Schema Versions
Each component can have different schema versions:

**WorkloadManifest.xml:**
```xml
<?xml version="1.0" encoding="utf-8" ?>
<WorkloadManifestConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" SchemaVersion="1.101.0">
  <!-- ... -->
</WorkloadManifestConfiguration>
```

**Item1.xml (Item Manifest):**
```xml
<?xml version="1.0" encoding="utf-8" ?>
<ItemManifestConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" SchemaVersion="1.102.0">
  <!-- ... -->
</ItemManifestConfiguration>
```

### Version Resolution Strategy
- **WorkloadDefinition.xsd**: Uses version from WorkloadManifest.xml
- **ItemDefinition.xsd**: Uses highest version from all item manifest files
- **CommonTypesDefinitions.xsd**: Always uses "common" (version-agnostic)

### Example Output
```
Component Schema Versions:
  WorkloadDefinition = 1.101.0
  ItemDefinition = 1.102.0
  CommonTypesDefinitions = common
```

## Caching System

### Cache Location
By default, XSD files are cached in `%TEMP%\FabricXSDCache\`

### Cache Structure
The cache uses simplified version-based storage:

```
%TEMP%\FabricXSDCache\
├── CommonTypesDefinitions.xsd (in root for XSD reference compatibility)
├── .metadata
├── download-summary.json
├── v1.100.0\
│   ├── WorkloadDefinition.xsd
│   ├── ItemDefinition.xsd
│   ├── .metadata
│   └── download-summary.json
├── v1.101.0\
│   ├── WorkloadDefinition.xsd
│   ├── ItemDefinition.xsd
│   ├── .metadata
│   └── download-summary.json
└── v1.102.0\
    ├── WorkloadDefinition.xsd
    ├── ItemDefinition.xsd
    ├── .metadata
    └── download-summary.json
```

**Cache Directory Naming:**
- **Versioned Components**: `v{version}` (e.g., `v1.101.0`, `v1.102.0`)
- **Common Types**: Root cache directory (for XSD reference compatibility via `..\..\common.xsd`)
- Each version gets its own cache directory for better organization

### Cache Management
- Cache entries are automatically validated for integrity
- Old cache entries (>30 days) can be cleaned with `-CleanCache`
- Cache can be bypassed with `-SkipCache`
- Cache can be forced to refresh with `-Force`

## Validation Process Flow

1. **Prerequisites Check**: Verify package directory and internet connectivity to Microsoft schema repository
2. **Component Schema Version Detection**: Extract versions from WorkloadManifest.xml and all item manifest files
3. **Version-Based Cache Management**: Check for cached XSD files in version-specific directories
4. **Microsoft Schema Download**: Download missing XSD files from official Microsoft repository by version
5. **File Verification**: Ensure all required XSD files are present and valid in their respective cache directories
6. **Separate Component Validation**: Run validation scripts for each component using its specific XSD version
7. **Error Reporting**: Check for validation errors and report results with component version details

## Error Handling

### Common Errors and Solutions

**Error: Package directory not found**
- Ensure the package directory path is correct and exists
- Use absolute paths if relative paths don't work

**Error: WorkloadManifest.xml not found**
- Verify that WorkloadManifest.xml exists in the package directory
- Check file name spelling and case sensitivity

**Error: Failed to detect schema version**
- Ensure WorkloadManifest.xml has a valid SchemaVersion attribute
- Verify XML is well-formed and not corrupted

**Error: Failed to download XSD files**
- Check internet connectivity to schemas.microsoft.com
- Verify Microsoft schema repository URL is correct and accessible
- Check if specific version exists in the Microsoft schema repository

**Error: Invalid XSD schema file**
- Verify downloaded files are valid XSD schemas
- Check Microsoft schema repository for file integrity

### Exit Codes
- `0` - Validation successful
- `1` - Validation failed or error occurred

### Error Files
Validation errors are written to `ValidationErrors.txt` in the script directory.

## Migration from Build-Time Validation

### Before (Build-Time)
```xml
<!-- In .csproj file -->
<Target Name="PreBuild" BeforeTargets="PreBuildEvent">
  <Exec Command="powershell -File ValidationScripts\ManifestValidator.ps1 ..." />
</Target>
```

### After (Manual)
```powershell
# Run manually before/after development
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json"
```

### CI/CD Integration
For continuous integration, you can still call the validation script:

```yaml
# Azure DevOps Pipeline example
- task: PowerShell@2
  displayName: 'Validate Manifests'
  inputs:
    filePath: 'tools/validation/Invoke-ManifestValidation.ps1'
    arguments: '-PackageDirectory "Backend/src/Packages/manifest" -AppSettingsPath "Backend/src/appsettings.json" -Verbose'
    pwsh: true
```

## Troubleshooting

### Enable Verbose Logging
Add `-Verbose` to see detailed execution information:

```powershell
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -Verbose
```

### Clear Cache
If you encounter cache-related issues:

```powershell
# Clean old entries
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -CleanCache

# Or skip cache entirely
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -SkipCache
```

### Force Fresh Download
If XSD files seem outdated or corrupted:

```powershell
.\Invoke-ManifestValidation.ps1 -PackageDirectory "Backend\src\Packages\manifest" -AppSettingsPath "Backend\src\appsettings.json" -Force
```

### Manual Cache Management
You can also manage cache manually using the cache management script:

```powershell
# List cached versions
.\Manage-XSDCache.ps1 -SchemaVersion "1.101.0" -Action "List"

# Check specific version
.\Manage-XSDCache.ps1 -SchemaVersion "1.101.0" -Action "Check"

# Clean old entries
.\Manage-XSDCache.ps1 -SchemaVersion "1.101.0" -Action "Clean"
```

## Best Practices

1. **Run validation regularly** during development to catch issues early
2. **Use caching** for better performance in repeated validations
3. **Check error files** thoroughly when validation fails
4. **Stay updated** with new schema versions from Microsoft's repository
5. **Use verbose mode** when troubleshooting issues
6. **Clean cache periodically** to avoid disk space issues

## Support

For issues or questions:
1. Check this documentation first
2. Enable verbose logging to get detailed error information
3. Verify Microsoft schema repository accessibility
4. Check internet connectivity to schemas.microsoft.com for XSD downloads