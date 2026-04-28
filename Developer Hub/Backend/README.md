# Microsoft Fabric Workload - Python FastAPI Backend

A comprehensive Python FastAPI backend implementation for Microsoft Fabric workload development, providing calculator workload functionality with full integration to the Fabric ecosystem.

## 🚀 Overview

This backend service implements the Microsoft Fabric Workload APIs using FastAPI, demonstrating how to build production-ready workloads that integrate seamlessly with the Fabric platform. The calculator workload showcases:

- **Item Lifecycle Management**: Create, read, update, and delete workload items
- **Job Execution**: Support for various job types (instant, scheduled, long-running)
- **OneLake Integration**: Direct integration with Fabric's data lake for file operations
- **Authentication & Authorization**: Microsoft Entra ID integration with proper token validation
- **Lakehouse Operations**: Create and manage calculations stored as text and parquet files

## 📋 Prerequisites

- **Python**: 3.11+ (recommended)
- **Microsoft Fabric Tenant**: Access to Microsoft Fabric workspace
- **Azure AD Application**: Registered application with proper permissions
- **Development Environment**: Windows 10/11, Linux, or macOS
- **Optional**: Docker and Docker Compose for containerized deployment

## 🛠️ Installation & Setup

### 1. Clone and Navigate

```bash
git clone <repository-url>
cd Backend
```

### 2. Create Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate
```

### 3. Install Dependencies

```bash
# Install production dependencies
pip install -r requirements.txt

# Install development dependencies (optional)
pip install -r tests/requirements-test.txt
```

### 4. Configuration Setup

#### Environment Variables
Create a `.env` file in the root directory:

```env
# Application Configuration
PYTHON_ENVIRONMENT=Development
DEBUG=false

# Azure AD Configuration
PUBLISHER_TENANT_ID=your-tenant-id
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
AUDIENCE=your-audience

# Server Configuration
HOST=0.0.0.0
PORT=5000
WORKERS=1

# SSL Configuration (Production)
SSL_KEYFILE=/path/to/private.key
SSL_CERTFILE=/path/to/certificate.crt

# Logging Configuration
LOG_LEVEL=Information

# Local OpenTelemetry tracing (enabled by default in docker-compose)
AGENTHUB_OTEL_ENABLED=1
AGENTHUB_OTEL_SAMPLE_RATE=1.0
AGENTHUB_OTEL_CONSOLE_EXPORTER=1
AGENTHUB_OTEL_TRACE_FILE=.data/otel-traces.jsonl
```

#### Configuration Files
The application uses JSON configuration files in [`src/`](src/) directory:

- [`appsettings.json`](src/appsettings.json) - Non-secret application defaults (server, logging, storage). **Do not put identity/secrets here.**
- [`appsettings.Development.json`](src/appsettings.Development.json) - Development overrides

**Identity & secrets** (`PublisherTenantId`, `ClientId`, `ClientSecret`, `Audience`, `GitHubClientId`) are the single source of truth in [`Developer Hub/.env`](../../.env) (gitignored). Copy [`Developer Hub/.env.example`](../../.env.example) to `Developer Hub/.env` and fill in your own values. Docker Compose injects these via `env_file:`; native runs load them via `python-dotenv` from `main.py`.

For AgentHub Azure management tools, the Entra app registration also needs the delegated API permission **Azure Service Management / user_impersonation** (`https://management.azure.com/user_impersonation`) with tenant admin consent where required. The signed-in user still needs Azure RBAC on the target scope, for example `Microsoft.Authorization/permissions/read` to inspect permissions and `Microsoft.Fabric/capacities/resume/action` to resume a Fabric capacity.

### Local OpenTelemetry

The backend has local OpenTelemetry tracing enabled by default when run through Docker Compose. It samples every trace, instruments FastAPI, HTTPX, Requests, aiohttp client calls, and SQLite, then writes compact JSON spans to stdout and to `Backend/.data/otel-traces.jsonl`.

Existing application logs are still the primary semantic breadcrumbs; each log line now includes the current OpenTelemetry trace and span ids as `tr:<trace_id>` and `sp:<span_id>`. Disable local tracing with `AGENTHUB_OTEL_ENABLED=0`, reduce volume with `AGENTHUB_OTEL_SAMPLE_RATE=0.1`, or turn off stdout span dumps with `AGENTHUB_OTEL_CONSOLE_EXPORTER=0` while keeping the trace file.

## 🚀 Running the Application

### Development Mode

```bash
# Method 1: Using Python directly
cd src
python main.py

# Method 2: Using uvicorn with module path
PYTHONPATH=src uvicorn fabric_api.main:app --host 0.0.0.0 --port 5000 --reload

# Method 3: Using the simplified uvicorn command
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

### Production Mode

```bash
# Set production environment
export PYTHON_ENVIRONMENT=Production

# Run with optimized settings
python src/main.py
```

### Docker Deployment

```bash
# Build and run with Docker Compose
docker-compose up --build

# Or build and run manually
docker build -t fabric-python-backend .
docker run -p 5000:5000 fabric-python-backend
```

## 📚 API Documentation

Once running, access the interactive API documentation:

- **OpenAPI/Swagger UI**: http://localhost:5000/docs
- **ReDoc**: http://localhost:5000/redoc  
- **OpenAPI Schema**: http://localhost:5000/openapi.json

### Core API Endpoints

#### Item Lifecycle Management
- `POST /workspaces/{workspaceId}/items/{itemType}/{itemId}` - Create item
- `PATCH /workspaces/{workspaceId}/items/{itemType}/{itemId}` - Update item  
- `DELETE /workspaces/{workspaceId}/items/{itemType}/{itemId}` - Delete item
- `GET /workspaces/{workspaceId}/items/{itemType}/{itemId}/payload` - Get item payload

#### Job Management
- `POST /workspaces/{workspaceId}/items/{itemType}/{itemId}/jobTypes/{jobType}/instances/{jobInstanceId}` - Create job instance
- `GET /workspaces/{workspaceId}/items/{itemType}/{itemId}/jobTypes/{jobType}/instances/{jobInstanceId}` - Get job status
- `POST /workspaces/{workspaceId}/items/{itemType}/{itemId}/jobTypes/{jobType}/instances/{jobInstanceId}/cancel` - Cancel job

#### Endpoint Resolution
- `POST /resolve-api-path-placeholder` - Resolve service endpoints

#### Extension APIs
- `GET /api/calculateText` - Calculate text operations
- `GET /api/getItems` - Get workspace items
- `POST /api/writeToLakehouseFile` - Write to lakehouse files

## 🏗️ Architecture

### Directory Structure

```
python/
├── src/
│   ├── constants/           # Application constants
│   ├── core/               # Core services and DI
│   ├── exceptions/         # Custom exceptions
│   ├── fabric_api/         # Generated API models and controllers
│   ├── impl/               # Implementation controllers
│   ├── items/              # Item domain models
│   ├── middleware/         # FastAPI middleware
│   ├── models/             # Data models
│   ├── services/           # Business logic services
│   ├── Packages/           # Fabric manifest packages
│   ├── appsettings.json    # Configuration
│   └── main.py            # Application entry point
├── tests/                  # Test suites
├── tools/                  # Development tools
├── docker-compose.yaml     # Docker composition
├── Dockerfile             # Container definition
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

### Key Components

#### Services (Singleton Pattern)
- **[`AuthenticationService`](src/services/authentication.py)**: Token validation and user authentication
- **[`AuthorizationService`](src/services/authorization.py)**: Permission validation
- **[`ConfigurationService`](src/services/configuration_service.py)**: Configuration management
- **[`ItemMetadataStore`](src/services/item_metadata_store.py)**: Item metadata persistence
- **[`OneLakeClientService`](src/services/onelake_client_service.py)**: OneLake file operations
- **[`LakehouseClientService`](src/services/lakehouse_client_service.py)**: Lakehouse integration

#### Controllers
- **[`ItemLifecycleController`](src/fabric_api/impl/item_lifecycle_controller.py)**: Handles item CRUD operations
- **[`JobsController`](src/fabric_api/impl/jobs_controller.py)**: Manages job execution
- **[`FabricExtensionController`](src/impl/fabric_extension_controller.py)**: Custom workload APIs

#### Models
- **[`AgentHubItem`](src/items/agenthub_item.py)**: AgentHub workload item (orchestration target)
- **[`BaseItem`](src/items/base_item.py)**: Abstract base for all items
- **API Models**: Generated from OpenAPI specification

## 🧪 Testing

All test configuration lives in **`pyproject.toml`** (`[tool.pytest.ini_options]` for
pytest, `[dependency-groups].dev` for test deps). There is no separate runner script
or `requirements-test.txt` — `pyproject.toml` is the single source of truth.

### Install dev dependencies (once)

```bash
uv sync --group dev      # installs runtime + dev deps into .venv
```

### Run tests

```bash
# All tests (uses addopts from pyproject: -v, coverage, strict markers)
uv run pytest

# Subsets via markers (declared in pyproject)
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m "unit and not slow"

# A single file or test
uv run pytest tests/unit/services/agenthub/test_session_store.py
uv run pytest -k test_create_session

# Parallel (pytest-xdist is in dev deps)
uv run pytest -n auto

# HTML coverage report (terminal coverage runs by default)
uv run pytest --cov-report=html
```

If you have an activated venv you can drop the `uv run` prefix and call `pytest` directly.

### Test Structure

```
tests/
├── unit/                  # Unit tests
│   ├── api/              # API layer tests
│   ├── controllers/      # Controller tests
│   └── services/         # Service tests
├── integration/          # Integration tests
├── conftest.py          # Test configuration
└── test_helpers.py      # Test utilities
```

### Test Markers

Use pytest markers to organize tests:
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.controllers` - Controller tests
- `@pytest.mark.api` - API tests
- `@pytest.mark.slow` - Slow running tests

## 🔐 Security & Authentication

### Microsoft Entra ID Integration
The application uses Microsoft Entra ID for authentication with the following flow:

1. **Subject Token**: User authentication token from Fabric
2. **App Token**: Service-to-service authentication
3. **Token Validation**: Validates both tokens against configured parameters

### Required Configuration
```json
{
    "PublisherTenantId": "your-tenant-id",
    "ClientId": "your-registered-app-id",
    "ClientSecret": "your-app-secret",
    "Audience": "your-fabric-audience"
}
```

### Security Headers
The application includes security middleware:
- CORS configuration
- Trusted host validation
- GZip compression
- Request/response logging

## 📊 Database & Storage

### Metadata Storage
- **File-based**: JSON files for item metadata
- **Location**: Platform-specific directories
  - Windows: `%APPDATA%\Microsoft_Fabric_Python_Backend\`
  - macOS: `~/Library/Application Support/Microsoft_Fabric_Python_Backend/`
  - Linux: `~/.config/Microsoft_Fabric_Python_Backend/`

### OneLake Integration
- Direct file operations in Fabric's data lake
- Support for text and parquet file formats
- Lakehouse table integration

## 🚢 Deployment

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Run development server
python src/main.py
```

### Docker Deployment
```bash
# Using Docker Compose
docker-compose up --build

# Using Docker directly
docker build -t fabric-backend .
docker run -p 5000:5000 \
  -e PUBLISHER_TENANT_ID=your-tenant-id \
  -e CLIENT_ID=your-client-id \
  -e CLIENT_SECRET=your-client-secret \
  fabric-backend
```

### Production Deployment
See [`deployment.md`](deployment.md) for detailed production deployment instructions including:
- Environment variable configuration
- SSL certificate setup
- Kubernetes deployment
- Load balancer configuration
- Monitoring setup

## 🔧 Configuration

### Application Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `PYTHON_ENVIRONMENT` | Environment mode | `Development` |
| `HOST` | Server host | `0.0.0.0` |
| `PORT` | Server port | `5000` |
| `WORKERS` | Worker processes | `1` |
| `DEBUG` | Debug mode | `false` |
| `LOG_LEVEL` | Logging level | `Information` |

### Authentication Settings

| Setting | Description | Required |
|---------|-------------|----------|
| `PUBLISHER_TENANT_ID` | Azure AD Tenant ID | Yes |
| `CLIENT_ID` | Application Client ID | Yes |
| `CLIENT_SECRET` | Application Secret | Yes |
| `AUDIENCE` | Token audience | Yes |

## 🔍 Monitoring & Logging

### Health Checks
- **Health endpoint**: `GET /health`
- **Readiness endpoint**: `GET /ready`
- **Metrics**: Available through application logs

### Logging Configuration
```json
{
    "Logging": {
        "LogLevel": {
            "Default": "Information",
            "Microsoft": "Warning",
            "Microsoft.Hosting.Lifetime": "Information"
        }
    }
}
```

### Log Locations
- **Development**: Console output
- **Production**: File-based logging in platform-specific directories

## 🛠️ Development Tools

### Code Quality
```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Lint code
flake8 src/ tests/
```

### Manifest Generation
```bash
# Generate workload manifest
python tools/manifest_package_generator.py
```

## 📝 Contributing

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

### Code Style
- Use [Black](https://black.readthedocs.io/) for code formatting
- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) guidelines
- Add type hints for all functions
- Include docstrings for public APIs

## 🐛 Troubleshooting

### Common Issues

**Port Already in Use**
```bash
# Find process using port 5000
lsof -i :5000  # macOS/Linux
netstat -ano | findstr :5000  # Windows

# Kill the process and restart
```

**Authentication Errors**
- Verify `PUBLISHER_TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, and `AUDIENCE` values
- Check Azure AD application registration
- Ensure proper scopes are configured

**Module Import Errors**
```bash
# Ensure PYTHONPATH is set correctly
export PYTHONPATH=src:$PYTHONPATH

# Or use the provided test runner
python run_tests.py
```

**Configuration Issues**
- Verify [`appsettings.json`](src/appsettings.json) is properly configured
- Check environment variables are set correctly
- Ensure file permissions for metadata storage

### Debug Mode
```bash
# Run with debug logging
DEBUG=true python src/main.py

# Run tests with debug output
python run_tests.py debug
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](../LICENSE) file for details.

## 🤝 Support

- **Documentation**: [Microsoft Fabric Workload Development](https://docs.microsoft.com/en-us/fabric/workload-development-kit/)
- **Issues**: Report issues in the GitHub repository
- **Community**: Join the Microsoft Fabric community discussions

## 📊 Project Status

- ✅ **Core APIs**: Item lifecycle, job management, endpoint resolution
- ✅ **Authentication**: Microsoft Entra ID integration
- ✅ **Storage**: OneLake and lakehouse integration
- ✅ **Testing**: Comprehensive test suite with 80%+ coverage
- ✅ **Documentation**: Full API documentation and deployment guides
- ✅ **Docker**: Container support for easy deployment

---

**Built with ❤️ using FastAPI for Microsoft Fabric**
