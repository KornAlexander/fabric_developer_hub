#!/usr/bin/env python3
"""
Manifest Package Generator for Microsoft Fabric Workload

Production-ready tool that generates ManifestPackage.nupkg files, mirroring the C# build process
exactly using the existing .nuspec template files.

Usage:
    python manifest_package_generator.py [--version VERSION] [--configuration CONFIG] [--output-dir OUTPUT_DIR]
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any


class ManifestPackageGenerator:
    def __init__(self, project_root: str, version: str = "1.0.0", build_config: str = "Debug"):
        """
        Initialize the manifest package generator.
        
        Args:
            project_root: Root directory of the Python project
            version: Version for the package (default: 1.0.0)
            build_config: Build configuration - "Debug" or "Release"
        """
        self.project_root = Path(project_root).resolve()
        self.version = version
        self.build_config = build_config
        self.manifest_dir = self.project_root / "src" / "Packages" / "manifest"
        self.frontend_package_dir = self.project_root.parent.parent / "Frontend" / "Package"
        
        # Validate project structure
        if not self.manifest_dir.exists():
            raise ValueError(
                f"Manifest directory not found at: {self.manifest_dir}\n"
                f"Current directory: {Path.cwd()}\n"
                f"Project root: {self.project_root}\n"
                f"Please ensure you're running from the correct location or specify --project-root"
            )
        
        # Select nuspec file based on build configuration
        if build_config.lower() == "release":
            self.nuspec_file = self.manifest_dir / "ManifestPackageRelease.nuspec"
            self.package_id = "ManifestPackageRelease"
        else:
            self.nuspec_file = self.manifest_dir / "ManifestPackageDebug.nuspec"
            self.package_id = "ManifestPackage"
    
    def validate_source_files(self) -> bool:
        """Validate that all required source files exist."""
        required_files = [
            self.manifest_dir / "WorkloadManifest.xml",
            self.nuspec_file
        ]
        
        # Item1.xml is optional - will create template if missing
        optional_files = [
            self.manifest_dir / "Item1.xml"
        ]
        
        missing_files = []
        for file_path in required_files:
            if not file_path.exists():
                missing_files.append(str(file_path))
        
        if missing_files:
            print(f"❌ Missing required files:")
            for file_path in missing_files:
                print(f"   - {file_path}")
            return False
        
        # Check optional files
        for file_path in optional_files:
            if not file_path.exists():
                print(f"⚠️  Optional file missing: {file_path} (will create template)")
        
        print("✅ All required source files found")
        return True
    
    def run_validation_steps(self) -> bool:
        """Run validation steps that mirror C# PreBuild process."""
        print("🔍 Running validation steps...")
        
        # Step 1: Validate XML files
        if not self.validate_xml_files():
            return False
        
        # Step 2: Validate workload configuration
        if not self.validate_workload_configuration():
            return False
        
        print("✅ All validation steps passed")
        return True
    
    def validate_xml_files(self) -> bool:
        """Validate XML files for well-formedness and content."""
        xml_files = ["WorkloadManifest.xml", "Item1.xml"]
        
        for xml_file in xml_files:
            xml_path = self.manifest_dir / xml_file
            
            if not xml_path.exists():
                if xml_file == "Item1.xml":
                    print(f"⚠️  {xml_file} not found, will create template")
                    continue
                else:
                    print(f"❌ Required file not found: {xml_path}")
                    return False
            
            # Validate XML structure
            try:
                tree = ET.parse(xml_path)
                print(f"✅ {xml_file} is valid XML")
                
                # Additional validation for WorkloadManifest
                if xml_file == "WorkloadManifest.xml":
                    if not self.validate_workload_manifest_content(tree):
                        return False
                        
            except ET.ParseError as e:
                print(f"❌ {xml_file} has XML errors: {e}")
                return False
        
        return True
    
    def validate_workload_manifest_content(self, tree: ET.ElementTree) -> bool:
        """Validate WorkloadManifest.xml content."""
        root = tree.getroot()
        
        # Find Workload element (handle namespaces)
        workload_elem = None
        for elem in root.iter():
            if elem.tag.endswith('Workload'):
                workload_elem = elem
                break
        
        if workload_elem is not None:
            workload_name = workload_elem.get('WorkloadName')
            if workload_name:
                if not workload_name.startswith('Org.'):
                    print(f"❌ WorkloadName '{workload_name}' must have 'Org.' prefix")
                    return False
                print(f"✅ WorkloadName format valid: {workload_name}")
            else:
                print("❌ WorkloadName attribute not found")
                return False
        else:
            print("❌ Workload element not found in WorkloadManifest.xml")
            return False
        
        return True
    
    def validate_workload_configuration(self) -> bool:
        """Validate overall workload configuration."""
        # Check for XSD files (optional but recommended)
        xsd_files = ["WorkloadDefinition.xsd", "ItemDefinition.xsd"]
        for xsd_file in xsd_files:
            xsd_path = self.manifest_dir / xsd_file
            if not xsd_path.exists():
                print(f"⚠️  XSD schema not found: {xsd_path} (optional)")
        
        return True
    
    def get_workload_name(self) -> str:
        """Extract WorkloadName from WorkloadManifest.xml."""
        try:
            manifest_path = self.manifest_dir / "WorkloadManifest.xml"
            tree = ET.parse(manifest_path)
            root = tree.getroot()
            
            # Find Workload element (handle namespaces)
            for elem in root.iter():
                if elem.tag.endswith('Workload'):
                    workload_name = elem.get('WorkloadName')
                    if workload_name:
                        return workload_name
        except Exception as e:
            print(f"⚠️  Could not extract WorkloadName: {e}")
        
        return 'Org.WorkloadSample'
    
    def load_and_update_nuspec(self) -> str:
        """Load the nuspec template and update version."""
        if not self.nuspec_file.exists():
            raise FileNotFoundError(f"Nuspec template not found: {self.nuspec_file}")
        
        print(f"📄 Using nuspec template: {self.nuspec_file.name}")
        
        # Read the template
        nuspec_content = self.nuspec_file.read_text(encoding='utf-8')
        
        # Update version in the template
        try:
            # Register namespace
            ET.register_namespace('', 'http://schemas.microsoft.com/packaging/2010/07/nuspec.xsd')
            root = ET.fromstring(nuspec_content)
            
            # Find version element
            for elem in root.iter():
                if elem.tag.endswith('version'):
                    elem.text = self.version
                    print(f"✅ Updated package version to: {self.version}")
                    break
            
            # Return updated XML
            return ET.tostring(root, encoding='unicode', xml_declaration=True)
            
        except ET.ParseError:
            # Fallback: string replacement
            print("⚠️  Using string replacement for version update")
            return nuspec_content.replace('<version>1.0.0</version>', f'<version>{self.version}</version>')
    
    def create_item1_template(self, output_path: Path) -> None:
        """Create Item1.xml with correct workload name."""
        workload_name = self.get_workload_name()
        item_type = f"{workload_name}.SampleWorkloadItem"
        
        template_content = f'''<?xml version="1.0" encoding="utf-8" ?>
<ItemManifestConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" SchemaVersion="1.101.0">
  <Item TypeName="{item_type}" Category="Data" CreateOneLakeFoldersOnArtifactCreation="true">
    <Workload WorkloadName="{workload_name}" />
    <JobScheduler>
      <OnDemandJobDeduplicateOptions>PerItem</OnDemandJobDeduplicateOptions>
      <ScheduledJobDeduplicateOptions>PerItem</ScheduledJobDeduplicateOptions>
      <ItemJobTypes>
        <ItemJobType Name="{item_type}.ScheduledJob" />
        <ItemJobType Name="{item_type}.CalculateAsText" />
        <ItemJobType Name="{item_type}.CalculateAsParquet" />
        <ItemJobType Name="{item_type}.LongRunningCalculateAsText" />
        <ItemJobType Name="{item_type}.InstantJob" />
      </ItemJobTypes>
    </JobScheduler>
  </Item>
</ItemManifestConfiguration>'''
        
        output_path.write_text(template_content, encoding='utf-8')
        print(f"✅ Created Item1.xml template with WorkloadName: {workload_name}")
    
    def process_frontend_pattern(self, zipf: zipfile.ZipFile, src_pattern: str, target: str) -> None:
        """Process frontend file patterns from nuspec."""
        print(f"📁 Processing frontend pattern: {src_pattern} -> {target}")
        
        # Convert Windows-style paths to cross-platform
        src_pattern = src_pattern.replace('\\', '/')
        
        # Handle relative paths from nuspec
        if src_pattern.startswith('../../../../Frontend/Package'):
            # Remove the relative part and get actual path
            pattern = src_pattern.replace('../../../../Frontend/Package/', '')
            pattern = pattern.rstrip('/')  # Remove trailing slash if any
            
            if not self.frontend_package_dir.exists():
                print(f"⚠️  Frontend package directory not found: {self.frontend_package_dir}")
                return
            
            if pattern == '*':
                # Add all files in Package directory (non-recursive)
                for file_path in self.frontend_package_dir.glob('*'):
                    if file_path.is_file():
                        zipf.write(file_path, f"{target}/{file_path.name}")
                        print(f"   Added: {file_path.name}")
                        
            elif pattern.startswith('assets/'):
                # Handle assets directory patterns
                if pattern.endswith('**') or pattern.endswith('**/*'):
                    # Add all files in assets directory recursively
                    assets_dir = self.frontend_package_dir / "assets"
                    if assets_dir.exists():
                        for root, dirs, files in os.walk(assets_dir):
                            for file in files:
                                file_path = Path(root) / file
                                rel_path = file_path.relative_to(assets_dir)
                                zipf.write(file_path, f"{target}/{rel_path}")
                                print(f"   Added: {rel_path}")
                else:
                    # Specific assets file
                    asset_file = self.frontend_package_dir / pattern
                    if asset_file.exists():
                        zipf.write(asset_file, f"{target}/{pattern}")
                        print(f"   Added: {pattern}")
            else:
                # Specific file
                file_path = self.frontend_package_dir / pattern
                if file_path.exists():
                    zipf.write(file_path, f"{target}/{pattern}")
                    print(f"   Added: {pattern}")
    
    def add_frontend_files_from_nuspec(self, zipf: zipfile.ZipFile, nuspec_content: str) -> None:
        """Add frontend files based on nuspec file patterns."""
        print("🎨 Adding frontend files based on nuspec patterns...")
        
        try:
            root = ET.fromstring(nuspec_content)
            
            # Find all file elements
            for elem in root.iter():
                if elem.tag.endswith('file'):
                    src = elem.get('src')
                    target = elem.get('target')
                    
                    if src and target and 'Frontend' in src:
                        self.process_frontend_pattern(zipf, src, target)
                        
        except ET.ParseError as e:
            print(f"⚠️  Could not parse nuspec for frontend patterns: {e}")
            # Fallback: add basic frontend files
            self.add_basic_frontend_files(zipf)
    
    def add_basic_frontend_files(self, zipf: zipfile.ZipFile) -> None:
        """Fallback method to add basic frontend files."""
        print("📄 Adding basic frontend files (fallback)...")
        
        if not self.frontend_package_dir.exists():
            print(f"⚠️  Frontend package directory not found: {self.frontend_package_dir}")
            return
        
        basic_files = ["Product.json", "Item1.json"]
        for filename in basic_files:
            file_path = self.frontend_package_dir / filename
            if file_path.exists():
                zipf.write(file_path, f"FE/{filename}")
                print(f"   Added: {filename}")
        
        # Add assets directory if it exists
        assets_dir = self.frontend_package_dir / "assets"
        if assets_dir.exists():
            for root, dirs, files in os.walk(assets_dir):
                for file in files:
                    file_path = Path(root) / file
                    rel_path = file_path.relative_to(self.frontend_package_dir)
                    zipf.write(file_path, f"FE/{rel_path}")
                    print(f"   Added: {rel_path}")
    
    def create_nupkg_using_nuspec_template(self, output_dir: str = None) -> str:
        """Create .nupkg using the existing nuspec template."""
        if output_dir is None:
            output_dir = str(self.project_root / "bin" / self.build_config)
            
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Generate package filename
        nupkg_filename = f"{self.package_id}.{self.version}.nupkg"
        nupkg_path = output_path / nupkg_filename
        
        print(f"🔧 Creating package: {nupkg_filename}")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Step 1: Copy manifest files to temp directory
            print("📄 Copying backend manifest files...")
            shutil.copy2(self.manifest_dir / "WorkloadManifest.xml", temp_path)
            
            item1_path = self.manifest_dir / "Item1.xml"
            if item1_path.exists():
                shutil.copy2(item1_path, temp_path)
            else:
                self.create_item1_template(temp_path / "Item1.xml")
            
            # Step 2: Load and process nuspec template
            print("📋 Processing nuspec template...")
            nuspec_content = self.load_and_update_nuspec()
            nuspec_path = temp_path / self.nuspec_file.name
            nuspec_path.write_text(nuspec_content, encoding='utf-8')
            
            # Step 3: Create the .nupkg file
            print("📦 Creating .nupkg file...")
            with zipfile.ZipFile(nupkg_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add nuspec to root of package
                zipf.write(nuspec_path, self.nuspec_file.name)
                
                # Add BE files
                zipf.write(temp_path / "WorkloadManifest.xml", "BE/WorkloadManifest.xml")
                if (temp_path / "Item1.xml").exists():
                    zipf.write(temp_path / "Item1.xml", "BE/Item1.xml")
                
                # Add FE files
                self.add_frontend_files_from_nuspec(zipf, nuspec_content)
        
        return str(nupkg_path)
    
    def create_build_info(self, output_dir: Path) -> None:
        """Create build information file for tracking."""
        build_info = {
            "version": self.version,
            "configuration": self.build_config,
            "build_time": datetime.now(timezone.utc).isoformat(),
            "package_id": self.package_id,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "manifest_dir": str(self.manifest_dir),
            "frontend_dir": str(self.frontend_package_dir),
            "workload_name": self.get_workload_name()
        }
        
        build_info_file = output_dir / f"{self.package_id}.{self.version}.buildinfo.json"
        build_info_file.write_text(json.dumps(build_info, indent=2), encoding='utf-8')
        print(f"📄 Created build info: {build_info_file.name}")
    
    def validate_version_format(self) -> bool:
        """Validate semantic versioning format."""
        import re
        pattern = r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$'
        if not re.match(pattern, self.version):
            print(f"⚠️  Version '{self.version}' doesn't follow semantic versioning (e.g., 1.0.0, 1.0.0-beta.1)")
            return False
        return True
    
    def generate(self, output_dir: str = None) -> str:
        """
        Generate the manifest package using nuspec templates.
        
        Args:
            output_dir: Output directory for the .nupkg file
            
        Returns:
            Path to the generated .nupkg file
        """
        print(f"🔧 Generating Manifest Package v{self.version} ({self.build_config} configuration)")
        print(f"📁 Project root: {self.project_root}")
        print(f"📄 Manifest directory: {self.manifest_dir}")
        print(f"📋 Using nuspec: {self.nuspec_file.name}")
        print(f"🎨 Frontend package directory: {self.frontend_package_dir}")
        print()
        
        # Validate version format
        if not self.validate_version_format():
            print("⚠️  Consider using semantic versioning for production")
        
        # Step 1: Validate source files
        if not self.validate_source_files():
            raise FileNotFoundError("Required source files are missing")
        
        # Step 2: Run validation steps
        if not self.run_validation_steps():
            raise ValueError("Validation failed")
        
        # Step 3: Create the package
        nupkg_path = self.create_nupkg_using_nuspec_template(output_dir)
        
        # Step 4: Create build info
        output_path = Path(nupkg_path).parent
        self.create_build_info(output_path)
        
        print()
        print(f"✅ Manifest package created successfully!")
        print(f"📦 Package location: {nupkg_path}")
        print(f"📏 Package size: {Path(nupkg_path).stat().st_size:,} bytes")
        print(f"🏷️  Package ID: {self.package_id}")
        print(f"🔢 Version: {self.version}")
        print(f"⚙️  Configuration: {self.build_config}")
        
        return nupkg_path


def find_python_backend_root() -> str:
    """Find the Python Backend root directory."""
    current = Path(__file__).resolve().parent
    
    # If we're in the tools directory, parent should be Python Backend
    if current.name == 'tools' and (current.parent / 'src' / 'Packages' / 'manifest').exists():
        return str(current.parent)
    
    # Search up the directory tree
    search_path = current
    while search_path != search_path.parent:
        if (search_path / 'src' / 'Packages' / 'manifest').exists():
            return str(search_path)
        search_path = search_path.parent
    
    # Default to current directory
    return "."


def main():
    parser = argparse.ArgumentParser(
        description="Generate ManifestPackage.nupkg for Python Fabric Workload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --version 1.0.1
  %(prog)s --configuration Release --version 2.0.0
  %(prog)s --output-dir ./artifacts --version 1.2.3
  %(prog)s --project-root /path/to/PythonBackend --version 1.0.0
        """
    )
    
    parser.add_argument("--version", default="1.0.0", help="Package version (default: 1.0.0)")
    parser.add_argument("--configuration", choices=["Debug", "Release"], default="Debug", 
                       help="Build configuration (default: Debug)")
    parser.add_argument("--output-dir", help="Output directory (default: {project_root}/bin/{configuration})")
    parser.add_argument("--project-root", default=find_python_backend_root(), 
                       help="Project root directory (default: auto-detected)")
    
    args = parser.parse_args()
    
    try:
        generator = ManifestPackageGenerator(
            args.project_root, 
            args.version, 
            args.configuration
        )
        
        nupkg_path = generator.generate(args.output_dir)
        
        print("\n" + "="*70)
        print("🎉 SUCCESS: Manifest package generated!")
        print(f"📦 Location: {nupkg_path}")
        print("="*70)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if os.environ.get('DEBUG'):
            import traceback
            traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())