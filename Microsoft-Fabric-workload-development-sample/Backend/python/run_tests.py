#!/usr/bin/env python3
"""
Cross-platform test runner for the Python Backend.
Works on Windows, Linux, and macOS without modification.
"""

import sys
import subprocess
import os
import argparse
from pathlib import Path


class Colors:
    """ANSI color codes that work cross-platform."""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    
    @staticmethod
    def disable():
        """Disable colors for environments that don't support them."""
        Colors.RED = ''
        Colors.GREEN = ''
        Colors.YELLOW = ''
        Colors.BLUE = ''
        Colors.RESET = ''


# Check if the terminal supports colors
if not sys.stdout.isatty() or os.environ.get('NO_COLOR'):
    Colors.disable()
# Windows color support
elif sys.platform == 'win32':
    try:
        import colorama
        colorama.init()
    except ImportError:
        # If colorama is not available, disable colors on Windows
        if os.environ.get('TERM') is None:
            Colors.disable()


def print_colored(message, color=Colors.RESET):
    """Print a colored message."""
    print(f"{color}{message}{Colors.RESET}")


def setup_environment():
    """Setup the environment for running tests."""
    # Get the root directory
    root_dir = Path(__file__).parent.absolute()
    src_dir = root_dir / "src"
    
    # Set PYTHONPATH to include src directory
    env = os.environ.copy()
    
    if 'PYTHONPATH' in env:
        env['PYTHONPATH'] = f"{src_dir}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env['PYTHONPATH'] = str(src_dir)
    
    # Also add to sys.path for this process
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    
    return env


def check_virtual_env():
    """Check if a virtual environment is activated."""
    if not os.environ.get('VIRTUAL_ENV'):
        print_colored("Warning: Virtual environment not activated", Colors.YELLOW)
        print_colored("Please activate your virtual environment before running tests.", Colors.YELLOW)
        
        # Provide platform-specific instructions
        if sys.platform.startswith('win'):
            print("  Windows: .\\venv\\Scripts\\activate")
        else:
            print("  Linux/Mac: source venv/bin/activate")
        
        response = input("\nContinue anyway? (y/N): ").lower()
        if response != 'y':
            sys.exit(1)


def install_dependencies():
    """Install test dependencies."""
    print_colored("Installing test dependencies...", Colors.GREEN)
    requirements_path = Path(__file__).parent / "tests" / "requirements-test.txt"
    
    if not requirements_path.exists():
        print_colored(f"Error: {requirements_path} not found!", Colors.RED)
        sys.exit(1)
    
    # Also install main requirements
    main_requirements = Path(__file__).parent / "requirements.txt"
    if main_requirements.exists():
        print_colored("Installing main dependencies...", Colors.GREEN)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(main_requirements)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print_colored("Failed to install main dependencies:", Colors.RED)
            print(result.stderr)
            sys.exit(1)
    
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print_colored("Failed to install test dependencies:", Colors.RED)
        print(result.stderr)
        sys.exit(1)


def run_tests(test_type, specific_test=None):
    """Run the tests based on the specified type."""
    # Setup environment
    env = setup_environment()
    
    # Build pytest command
    cmd = [sys.executable, "-m", "pytest"]
    
    # Add common options
    cmd.extend(["-v", "--tb=short"])

    cmd.extend([
        "-W", "ignore::pytest.PytestUnknownMarkWarning",
        "-W", "ignore::PendingDeprecationWarning"
    ])
    
    if test_type == "unit":
        print_colored("Running unit tests...", Colors.GREEN)
        cmd.extend(["tests/", "-m", "unit"])
    
    elif test_type == "integration":
        print_colored("Running integration tests...", Colors.GREEN)
        cmd.extend(["tests/", "-m", "integration"])
    
    elif test_type == "controllers":
        print_colored("Running controller tests...", Colors.GREEN)
        cmd.extend(["tests/", "-m", "controllers"])
    
    elif test_type == "api":
        print_colored("Running API tests...", Colors.GREEN)
        cmd.extend(["tests/", "-m", "api"])
    
    elif test_type == "services":
        print_colored("Running service tests...", Colors.GREEN)
        cmd.extend(["tests/", "-m", "services"])
    
    elif test_type == "models":
        print_colored("Running model/domain entity tests...", Colors.GREEN)
        cmd.extend(["tests/", "-m", "models"])
    
    elif test_type == "coverage":
        print_colored("Running tests with coverage...", Colors.GREEN)
        cmd.extend(["tests/", "--cov=src", "--cov-report=html", "--cov-report=term"])
    
    elif test_type == "specific":
        if not specific_test:
            print_colored("Please specify a test file or pattern", Colors.RED)
            print("Usage: python run_tests.py specific <test_file_or_pattern>")
            sys.exit(1)
        print_colored(f"Running specific tests: {specific_test}", Colors.GREEN)
        cmd.extend(["tests/", "-k", specific_test, "-vv"])
    
    elif test_type == "parallel":
        print_colored("Running tests in parallel...", Colors.GREEN)
        cmd.extend(["tests/", "-n", "auto"])
    
    elif test_type == "watch":
        print_colored("Running tests in watch mode...", Colors.GREEN)
        # Use pytest-watch if available
        result = subprocess.run([sys.executable, "-m", "pip", "show", "pytest-watch"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            cmd = [sys.executable, "-m", "pytest_watch", "tests/", "--", "-v"]
        else:
            print_colored("pytest-watch not installed. Install it with: pip install pytest-watch", Colors.YELLOW)
            print_colored("Running normal test mode instead.", Colors.YELLOW)
            cmd.append("tests/")
    
    elif test_type == "debug":
        print_colored("Running tests with debugging output...", Colors.GREEN)
        cmd.extend(["tests/", "-vv", "-s", "--tb=long"])
    
    else:
        print_colored("Running all tests...", Colors.GREEN)
        cmd.append("tests/")
    
    # Run the tests with the modified environment
    print_colored(f"Executing: {' '.join(cmd)}", Colors.BLUE)
    print_colored(f"PYTHONPATH: {env.get('PYTHONPATH', 'Not set')}", Colors.BLUE)
    
    result = subprocess.run(cmd, env=env)
    
    if result.returncode == 0:
        print_colored("\n✓ Tests passed successfully!", Colors.GREEN)
        
        if test_type == "coverage":
            coverage_path = Path(__file__).parent / "htmlcov" / "index.html"
            print_colored(f"\nCoverage report generated at: {coverage_path}", Colors.BLUE)
    else:
        print_colored("\n✗ Tests failed!", Colors.RED)
        sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run tests for the Python Backend",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_tests.py              # Run all tests
  python run_tests.py unit         # Run unit tests only
  python run_tests.py integration  # Run integration tests only
  python run_tests.py controllers  # Run controller tests only
  python run_tests.py api         # Run API tests only
  python run_tests.py services    # Run service tests only
  python run_tests.py models      # Run model/domain entity tests only
  python run_tests.py coverage    # Run with coverage report
  python run_tests.py specific test_item_lifecycle  # Run specific tests
  python run_tests.py debug       # Run with debugging output
        """
    )
    
    parser.add_argument(
        'type',
        nargs='?',
        default='all',
        choices=['all', 'unit', 'integration', 'controllers', 'api', 'services', 'models', 'coverage', 'specific', 'parallel', 'watch', 'debug'],
        help='Type of tests to run'
    )
    
    parser.add_argument(
        'specific_test',
        nargs='?',
        help='Specific test pattern (only used with "specific" type)'
    )
    
    parser.add_argument(
        '--no-deps',
        action='store_true',
        help='Skip installing test dependencies'
    )
    
    args = parser.parse_args()
    
    print_colored("Python Backend Test Runner", Colors.GREEN)
    print("=" * 40)
    
    # Check virtual environment
    check_virtual_env()
    
    # Install dependencies unless skipped
    if not args.no_deps:
        install_dependencies()
    
    # Run tests
    run_tests(args.type, args.specific_test)


if __name__ == "__main__":
    main()