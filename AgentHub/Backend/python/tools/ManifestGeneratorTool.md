# Manifest Package Generator for Python Backend

This tool generates the **ManifestPackage.nupkg** file required for Microsoft Fabric workload registration. It creates the exact same package structure as the C# backend by using the same `.nuspec` template files.

## 🚀 Quick Start

```bash
# Navigate to tools directory
cd Backend/python/tools

# Generate your manifest package (takes < 5 seconds)
python manifest_package_generator.py --version 1.0.0
```

**That's it!** You now have:
- ✅ `ManifestPackage.1.0.0.nupkg` in `python/bin/Debug/`
- ✅ `ManifestPackage.1.0.0.buildinfo.json` with build metadata
- ✅ Your package validated and ready to use

## 📦 What Does This Tool Do?

The manifest package tells Microsoft Fabric how to register and communicate with your Python backend. It contains:

```
ManifestPackage.1.0.0.nupkg
├── BE/                           # Backend configuration
│   ├── WorkloadManifest.xml     # Your workload settings
│   └── Item1.xml                # Item type definitions
└── FE/                          # Frontend assets
    ├── Product.json             # UI configuration
    ├── Item1.json              # Item-specific UI
    └── assets/                 # Images & translations
```

## 🎯 Common Scenarios

### Development Environment

**First time setup:**
```bash
# 1. Generate the manifest package
cd Backend/python/tools
python manifest_package_generator.py --version 1.0.0

# 2. Create your own workload-dev-mode.json with the generated package path

# 3. Start your FastAPI backend
cd ../src
python main.py

# 4. Register with DevGateway
```

**Making changes to manifest files:**
```bash
# Edit your files in src/Packages/manifest/
# Regenerate the package with a new version
cd Backend/python/tools
python manifest_package_generator.py --version 1.0.1
```

### Production Deployment

**Generate a release package:**
```bash
# Uses the Release template (ManifestPackageRelease.nuspec)
python manifest_package_generator.py --configuration Release --version 2.0.0
```

**CI/CD Pipeline:**
```bash
# In your deployment script
cd Backend/python/tools
python manifest_package_generator.py \
  --configuration Release \
  --version $BUILD_NUMBER \
  --output-dir $ARTIFACT_PATH
```

## 🔧 Understanding Template Selection

The `--configuration` parameter selects which `.nuspec` template file to use:

| Parameter | Template File Used | Output Package | When to Use |
|-----------|-------------------|----------------|-------------|
| `Debug` (default) | `ManifestPackageDebug.nuspec` | `ManifestPackage.X.X.X.nupkg` | Development & testing |
| `Release` | `ManifestPackageRelease.nuspec` | `ManifestPackageRelease.X.X.X.nupkg` | Production deployment |

**Note**: Unlike C#, Python doesn't compile code. The "configuration" only affects which template is used for package metadata.

## 📋 Prerequisites

Before running the generator, ensure these files exist:

### Required Files
```
python/
└── src/Packages/manifest/
    ├── WorkloadManifest.xml        # Your workload configuration
    ├── ManifestPackageDebug.nuspec   # Debug template
    └── ManifestPackageRelease.nuspec # Release template
```

### Optional Files
- `src/Packages/manifest/Item1.xml` - Item definitions (auto-generated if missing)
- `Frontend/Package/` - Frontend assets (works without them)

## 🛠️ Command Line Options

```bash
python manifest_package_generator.py [OPTIONS]

Options:
  --version VERSION          Package version (default: 1.0.0)
  --configuration CONFIG     Debug or Release (default: Debug)
  --output-dir DIR          Output directory (default: ./bin/{configuration})
  --project-root DIR        Project root (default: auto-detected)
```

### Examples

```bash
# Generate development package with custom version
python manifest_package_generator.py --version 1.0.1

# Generate release package
python manifest_package_generator.py --configuration Release --version 2.0.0

# Custom output location
python manifest_package_generator.py --version 1.0.1 --output-dir ./artifacts

# Run from any directory
cd /anywhere
python /path/to/manifest_package_generator.py --project-root /path/to/PythonBackend --version 1.0.0
```

## 🐛 Troubleshooting

### "Missing required files"
```
❌ Missing required files:
   - src/Packages/manifest/WorkloadManifest.xml
```
**Solution:** Ensure all required files exist. The tool shows the exact paths it's looking for.

### "WorkloadName must have 'Org.' prefix"
```
❌ WorkloadName 'MyWorkload' must have 'Org.' prefix
```
**Solution:** Edit WorkloadManifest.xml to use format like `Org.CompanyName.WorkloadName`

### "Manifest directory not found"
```
❌ Error: Manifest directory not found at: ...
Current directory: ...
Project root: ...
```
**Solution:** Run from the tools directory or specify `--project-root` explicitly.

### Package seems empty
```bash
# Inspect package contents
unzip -l ManifestPackage.1.0.0.nupkg

# On Windows
7z l ManifestPackage.1.0.0.nupkg
```

## 🔄 Complete Development Workflow

### Local Development Script
```bash
#!/bin/bash
# dev-start.sh - Complete development setup

echo "🚀 Starting Fabric Python Backend Development"

# 1. Generate/update manifest package
cd Backend/pthon/tools
python manifest_package_generator.py --version 1.0.1

# 2. Start backend
cd ../src
python main.py
```

### Windows Batch Script
```batch
@echo off
REM dev-start.bat - Windows development setup

echo Starting Fabric Python Backend Development...

REM 1. Generate/update manifest package
cd Backend\python\tools
python manifest_package_generator.py --version 1.0.1

REM 2. Start backend
cd ..\src
python main.py
```

### Docker Integration
```dockerfile
# In your Dockerfile
WORKDIR /app/python

# Copy source files
COPY . .

# Generate manifest package during build
RUN cd tools && python manifest_package_generator.py --configuration Release --version ${BUILD_VERSION:-1.0.0}

# Start the backend
WORKDIR /app/python/src
CMD ["python", "main.py"]
```

### GitHub Actions
```yaml
name: Build and Test
on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        cd Backend/python
        pip install -r requirements.txt
    
    - name: Generate manifest packages
      run: |
        cd Backend/python/tools
        # Development package
        python manifest_package_generator.py --version 1.0.${{ github.run_number }}
        # Release package
        python manifest_package_generator.py --configuration Release --version 1.0.${{ github.run_number }}
    
    - name: Run tests
      run: |
        cd Backend/python
        pytest
    
    - name: Upload artifacts
      uses: actions/upload-artifact@v3
      with:
        name: manifest-packages
        path: python/bin/**/*.nupkg
```

## 📝 Important Notes

1. **Backend Start Command**: Always start your backend from the `src` directory using `python main.py`
2. **Version Management**: Use semantic versioning (1.0.0, 1.0.1, etc.) for your packages
3. **Build Info**: Each package generates a `.buildinfo.json` file for tracking
4. **Auto-detection**: The tool automatically finds your Python Backend directory when run from tools/
5. **Compatibility**: This tool produces the exact same output as the C# build process

## 🆘 Getting Help

1. **Check error messages** - They show exact file paths and current directory
2. **Use DEBUG mode** - Set `DEBUG=1` environment variable for detailed output
3. **Inspect the package** - Use `unzip -l` to verify contents
4. **Check the templates** - Review `.nuspec` files for package structure

## 📚 Additional Resources

- [Backend Setup Guide](../README.md) - Complete backend documentation
- [Fabric Workload Documentation](https://docs.microsoft.com/fabric) - Official docs
- [API Reference](../docs/api.md) - Your API endpoints

---

**Quick Reference:**
- **Generate package:** `python manifest_package_generator.py --version 1.0.1`
- **Start backend:** `cd Backend/python/src && python main.py`
- **Check package:** `unzip -l ManifestPackage.1.0.1.nupkg`
- **Debug mode:** `DEBUG=1 python manifest_package_generator.py --version 1.0.1`