param (
    [string]$inputDirectory,
    [string]$inputXml,
    [string]$inputXsd,
    [string]$outputDirectory,
    [string]$xsdDirectory = $null  # Optional separate XSD directory
)

<#
.SYNOPSIS
    Validates XML manifest files against XSD schemas.

.DESCRIPTION
    This script validates XML manifest files using XSD schemas. It supports both legacy mode
    (XSD files in same directory as XML) and new mode (XSD files in separate directory).

.PARAMETER inputDirectory
    Directory containing the XML files to validate.

.PARAMETER inputXml
    Name of the XML file to validate.

.PARAMETER inputXsd
    Name of the primary XSD schema file.

.PARAMETER outputDirectory
    Directory where validation error files will be written.

.PARAMETER xsdDirectory
    Optional directory containing XSD schema files. If not provided, will look in inputDirectory.
#>

try
{
    if (-not($inputDirectory -and $inputXml -and $inputXsd -and $outputDirectory))
    {
        throw "Invalid input parameters"
    }

    # Determine XSD directory - use provided xsdDirectory or fall back to inputDirectory
    $actualXsdDirectory = if ($xsdDirectory) { $xsdDirectory } else { $inputDirectory }

    Write-Verbose "Input Directory: $inputDirectory"
    Write-Verbose "XSD Directory: $actualXsdDirectory"
    Write-Verbose "Input XML: $inputXml"
    Write-Verbose "Input XSD: $inputXsd"

    # Verify XSD files exist
    $primaryXsdPath = Join-Path $actualXsdDirectory $inputXsd
    # CommonTypesDefinitions.xsd is now in the root cache directory (parent of versioned directories)
    $commonXsdPath = Join-Path (Split-Path $actualXsdDirectory -Parent) "CommonTypesDefinitions.xsd"

    if (-not (Test-Path -Path $primaryXsdPath)) {
        throw "Primary XSD file not found: $primaryXsdPath"
    }

    if (-not (Test-Path -Path $commonXsdPath)) {
        throw "Common types XSD file not found: $commonXsdPath"
    }

    Write-Verbose "Loading XSD schemas..."
    Write-Verbose "Primary XSD: $primaryXsdPath"
    Write-Verbose "Common XSD: $commonXsdPath"

    # Verify files exist before loading
    if (-not (Test-Path $primaryXsdPath)) {
        throw "Primary XSD file not found: $primaryXsdPath"
    }
    if (-not (Test-Path $commonXsdPath)) {
        throw "Common XSD file not found: $commonXsdPath"
    }

    $schemaSet = [System.Xml.Schema.XmlSchemaSet]::new()
    
    # Load primary schema with proper disposal
    $primaryReader = [System.IO.StreamReader]::new($primaryXsdPath)
    try {
        $schema = [System.Xml.Schema.XmlSchema]::Read($primaryReader, $null)
        Write-Verbose "Loaded primary schema with target namespace: $($schema.TargetNamespace)"
    }
    finally {
        $primaryReader.Close()
        $primaryReader.Dispose()
    }

    # Load common schema with proper disposal
    $commonReader = [System.IO.StreamReader]::new($commonXsdPath)
    try {
        $schemaCommon = [System.Xml.Schema.XmlSchema]::Read($commonReader, $null)
        Write-Verbose "Loaded common schema with target namespace: $($schemaCommon.TargetNamespace)"
    }
    finally {
        $commonReader.Close()
        $commonReader.Dispose()
    }

    $schemaSet.Add($schema)
    $schemaSet.Add($schemaCommon)
    $schemaSet.Compile()
    Write-Verbose "Schema set compiled successfully"
    $settings = [System.Xml.XmlReaderSettings]::new()
    $settings.ValidationType = [System.Xml.ValidationType]::Schema
    $settings.ValidationFlags = [System.Xml.Schema.XmlSchemaValidationFlags]::ReportValidationWarnings
    $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $settings.Schemas.Add($schemaSet)

    # Enhanced validation event handler with debugging
    $handler = [System.Xml.Schema.ValidationEventHandler] {
        $args = $_ # entering new block so copy $_
        Write-Verbose "Validation event: Severity=$($args.Severity), Message=$($args.Message)"

        if ($args.Severity -eq [System.Xml.Schema.XmlSeverityType]::Warning -or $args.Severity -eq [System.Xml.Schema.XmlSeverityType]::Error)
        {
            Write-Host "VALIDATION ERROR DETECTED: $($args.Message)" -ForegroundColor Red
            $scriptPath = Join-Path $PSScriptRoot "WriteErrorsToFile.ps1"
            & $scriptPath -errors "$($args.Message)`r`n" -outputDirectory $outputDirectory
            Write-Verbose "Error written to file via WriteErrorsToFile.ps1"
        }
    }
    $settings.add_ValidationEventHandler($handler)

    Write-Verbose "Starting XML validation for: $(Join-Path $inputDirectory $inputXml)"
    $xmlPath = [string](Join-Path $inputDirectory $inputXml)
    Write-Verbose "Full XML path: $xmlPath"

    $reader = [System.Xml.XmlReader]::Create($xmlPath, [System.Xml.XmlReaderSettings]$settings)
    try {
        $nodeCount = 0
        while ($reader.Read()) {
            $nodeCount++
        }
        Write-Verbose "XML validation completed. Processed $nodeCount nodes."
    }
    finally {
        $reader.Close()
        $reader.Dispose()
    }

    # Additional validation logic (only for WorkloadManifest.xml)
    if ($inputXml -eq "WorkloadManifest.xml") {
        $workloadXml = [xml](Get-Content -Path (Join-Path $inputDirectory $inputXml))
        $workloadName = $workloadXml.WorkloadManifestConfiguration.Workload.WorkloadName
        $aadApp = $workloadXml.SelectSingleNode("//AADApp")
        if ($aadApp -and (-not ($aadApp.ResourceId -clike "*$($workloadName)")) -and (-not ($aadApp.ResourceId -clike "*$($workloadName)/*")))
        {
            $scriptPath = Join-Path $PSScriptRoot "WriteErrorsToFile.ps1"
            & $scriptPath -errors "AADApp.resourceId: $($aadApp.ResourceId), should contain the exact WorkloadName: $($workloadName)" -outputDirectory $outputDirectory
        }
    }
}
catch
{
    Write-Host "An error occurred:"
    Write-Host $_
}