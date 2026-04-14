param (
    [string]$inputDirectory,
    [string]$inputXsd,
    [string]$outputDirectory,
    [string]$xsdDirectory = $null  # Optional separate XSD directory
)

<#
.SYNOPSIS
    Validates item manifest XML files against XSD schemas.

.DESCRIPTION
    This script validates all item manifest XML files in a directory, excluding the
    WorkloadManifest.xml file. It also performs naming convention validations.

.PARAMETER inputDirectory
    Directory containing the XML files to validate.

.PARAMETER inputXsd
    Name of the item XSD schema file.

.PARAMETER outputDirectory
    Directory where validation error files will be written.

.PARAMETER xsdDirectory
    Optional directory containing XSD schema files. If not provided, will look in inputDirectory.
#>

try
{
    if (-not($inputDirectory -and $inputXsd -and $outputDirectory))
    {
        throw "Invalid input parameters"
    }

    Write-Verbose "Starting item manifest validation..."
    Write-Verbose "Input Directory: $inputDirectory"
    Write-Verbose "XSD Directory: $(if ($xsdDirectory) { $xsdDirectory } else { $inputDirectory })"
    Write-Verbose "Input XSD: $inputXsd"

    $workloadManifest = "WorkloadManifest.xml"
    $workloadXmlPath = Join-Path $inputDirectory $workloadManifest

    if (-not (Test-Path -Path $workloadXmlPath)) {
        throw "WorkloadManifest.xml not found in input directory: $inputDirectory"
    }

    $workloadXml = [xml](Get-Content -Path $workloadXmlPath)
    $workloadName = $workloadXml.WorkloadManifestConfiguration.Workload.WorkloadName
    Write-Verbose "Workload Name: $workloadName"

    $allXmls = Get-ChildItem -Path $inputDirectory -Filter "*.xml"
    Write-Verbose "Found $($allXmls.Count) XML files to examine"

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

    Write-Verbose "Found $($itemXmls.Count) item manifest files to validate"
    
    foreach ($itemXml in $itemXmls)
    {
        Write-Verbose "Validating item manifest: $($itemXml.Name)"
        $manifestValidatorPath = Join-Path $PSScriptRoot "ManifestValidator.ps1"

        # Pass xsdDirectory parameter if provided
        if ($xsdDirectory) {
            & $manifestValidatorPath -inputDirectory $inputDirectory -inputXml $itemXml.Name -inputXsd $inputXsd -outputDirectory $outputDirectory -xsdDirectory $xsdDirectory
        }
        else {
            & $manifestValidatorPath -inputDirectory $inputDirectory -inputXml $itemXml.Name -inputXsd $inputXsd -outputDirectory $outputDirectory
        }

        # Naming Validations
        $itemXmlPath = $itemXml.FullName
        $xdoc = [xml](Get-Content -Path $itemXmlPath)
        $itemWorkloadName = $xdoc.ItemManifestConfiguration.Item.Workload.WorkloadName
        if ($itemWorkloadName -ne $workloadName)
        {
            $scriptPath = Join-Path $PSScriptRoot "WriteErrorsToFile.ps1"
            & $scriptPath -errors "Non matching WorkloadName between WorkloadManifest.xml and $($itemXml.Name)" -outputDirectory $outputDirectory
        }
        $itemName = $xdoc.ItemManifestConfiguration.Item.TypeName
        if (-not ($itemName -clike "$($itemWorkloadName).*"))
        {
            $scriptPath = Join-Path $PSScriptRoot "WriteErrorsToFile.ps1"
            & $scriptPath -errors "Item name's prefix should be WorkloadName for item $($itemName)" -outputDirectory $outputDirectory
        }
        $jobNames = $xdoc.SelectNodes("//ItemJobType")
        foreach ($jobName in $jobNames)
        {
            if (-not ($jobName.Name -clike "$($itemName).*"))
            {
                $scriptPath = Join-Path $PSScriptRoot "WriteErrorsToFile.ps1"
                & $scriptPath -errors "Job type name's prefix should be ItemName for jobType $($jobName.Name)" -outputDirectory $outputDirectory
            }
        }
    }
}
catch
{
    Write-Host "An error occurred:"
    Write-Host $_
}