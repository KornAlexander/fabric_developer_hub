# Testing Guide for Python Backend

This guide explains how to set up and run tests for the Microsoft Fabric Python Backend sample.

## 📋 Prerequisites

- **Python 3.8+** installed on your system
- **pip** package manager
- **Virtual environment** (strongly recommended)

## 🚀 Quick Start

### 1. Set up your environment

First, navigate to the Python Backend directory:
```bash
cd Backend
cd python
```

Create and activate a virtual environment:

**Windows:**
```bash
python -m venv venv
.\venv\Scripts\activate
```

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

Install both application and test dependencies:
```bash
pip install -r requirements.txt
pip install -r tests/requirements-test.txt
```

### 3. Run tests

The easiest way is to use our cross-platform test runner:
```bash
python run_tests.py
```

## 📊 Test Commands

### Using the Test Runner Script

| Command | Description |
|---------|-------------|
| `python run_tests.py` | Run all tests |
| `python run_tests.py unit` | Run only unit tests |
| `python run_tests.py integration` | Run only integration tests |
| `python run_tests.py coverage` | Run tests with coverage report |
| `python run_tests.py specific test_name` | Run tests matching a pattern |
| `python run_tests.py parallel` | Run tests in parallel (faster) |
| `python run_tests.py watch` | Auto-run tests on file changes |
| `python run_tests.py debug` | Run with verbose debugging output |

### Using pytest Directly

For more control, you can use pytest commands:

```bash
# Run all tests with verbose output
pytest tests/ -v

# Run with coverage and generate HTML report
pytest tests/ --cov=src --cov-report=html --cov-report=term

# Run specific test file
pytest tests/unit/controllers/test_item_lifecycle_controller.py -v

# Run tests matching a keyword
pytest tests/ -k "test_create_item" -v

# Run tests by marker
pytest tests/ -m unit        # Unit tests only
pytest tests/ -m integration  # Integration tests only
pytest tests/ -m api         # API tests only
```

## 📁 Test Structure

```
python/
├── src/                     # Application source code
├── tests/
│   ├── __init__.py         # Package initialization
│   ├── conftest.py         # Pytest configuration and shared fixtures
│   ├── test_fixtures.py    # Common test data (UUIDs, payloads, etc.)
│   ├── test_helpers.py     # Helper utilities for tests
│   ├── constants.py        # Test constants and expected responses
│   ├── requirements-test.txt    # Test-specific dependencies
│   └── unit/               # Unit tests
│       ├── api/            # API endpoint tests
│       └── controllers/    # Controller tests
├── run_tests.py            # Cross-platform test runner
└── pytest.ini              # Pytest configuration
```

## 📈 Coverage Reports

After running tests with coverage (`python run_tests.py coverage`):

1. **HTML Report**: Open `htmlcov/index.html` in your browser
2. **Terminal Report**: Coverage summary is displayed in the terminal
3. **XML Report**: `coverage.xml` is generated for CI/CD integration

The project has a minimum coverage requirement of **80%**.

## ✍️ Writing Tests

### Test File Naming
- Test files must start with `test_`
- Example: `test_item_lifecycle_controller.py`

### Test Structure
```python
import pytest
from tests.test_fixtures import TestFixtures
from tests.test_helpers import TestHelpers

@pytest.mark.unit  # Mark the test type
@pytest.mark.controllers  # Mark the component being tested
class TestYourFeature:
    """Test cases for YourFeature."""
    
    @pytest.mark.asyncio  # For async tests
    async def test_something_works(self, client, mock_authentication_service):
        """Test that something works correctly."""
        # Arrange
        headers = {"authorization": "Bearer token"}
        
        # Act
        response = client.get("/endpoint", headers=headers)
        
        # Assert
        assert response.status_code == 200
```

### Available Test Markers
- `@pytest.mark.unit` - Fast, isolated unit tests
- `@pytest.mark.integration` - Tests integrating multiple components
- `@pytest.mark.api` - API endpoint tests
- `@pytest.mark.controllers` - Controller layer tests
- `@pytest.mark.services` - Service layer tests
- `@pytest.mark.slow` - Long-running tests
- `@pytest.mark.smoke` - Critical tests for CI/CD

### Common Fixtures (from conftest.py)
- `client` - FastAPI test client
- `valid_headers` - Pre-configured valid request headers
- `mock_authentication_service` - Mocked authentication service
- `mock_item_factory` - Mocked item factory
- `app` - FastAPI application instance

## 🔧 Troubleshooting

### Common Issues

**1. Import Errors**
```bash
# Ensure you're in the python directory
cd Backend
cd python

# Verify PYTHONPATH includes src
echo $PYTHONPATH  # Linux/macOS
echo %PYTHONPATH%  # Windows
```

**2. Missing Dependencies**
```bash
# Reinstall all dependencies
pip install -r requirements.txt -r tests/requirements-test.txt
```

**3. Virtual Environment Not Active**
- Look for `(venv)` prefix in your terminal
- If missing, activate it again (see Quick Start)

**4. Tests Failing Due to Async Issues**
- Ensure you're using `@pytest.mark.asyncio` for async tests
- Check that mocks are created with `AsyncMock` for async methods

**5. Coverage Not Meeting Threshold**
- Run `python run_tests.py coverage` to see uncovered lines
- Focus on testing error cases and edge conditions

## 🚀 CI/CD Integration

### GitHub Actions Example
```yaml
name: Run Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.8'
    
    - name: Install dependencies
      run: |
        cd Backend
        cd python
        pip install -r requirements.txt
        pip install -r tests/requirements-test.txt
    
    - name: Run tests with coverage
      run: |
        cd Backend
        cd python
        python run_tests.py coverage
    
    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./python/coverage.xml
        fail_ci_if_error: true
```

## 💡 Tips and Best Practices

1. **Always use a virtual environment** to avoid dependency conflicts
2. **Run tests before committing** code changes
3. **Write tests for new features** as you develop them
4. **Use descriptive test names** that explain what is being tested
5. **Keep tests focused** - one test should verify one behavior
6. **Use fixtures** to avoid code duplication
7. **Mock external dependencies** to keep tests fast and isolated

## 📞 Need Help?

If you encounter issues:
1. Check the troubleshooting section above
2. Review the test output carefully - pytest provides detailed error messages
3. Check existing tests for examples
4. Ensure all dependencies are installed correctly

Happy testing! 🎉