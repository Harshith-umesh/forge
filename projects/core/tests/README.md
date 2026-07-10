# FORGE Toolbox Tests

This directory contains tests for the unified toolbox script (`run_toolbox.py`).

## Test Files

- **`test_run_toolbox.py`** - Unit tests (pytest-compatible)
  - Tests project discovery
  - Tests command discovery  
  - Tests description extraction
  - Tests error handling

- **`test_run_toolbox_cli.py`** - CLI integration tests (pytest-compatible)
  - Tests command line argument parsing
  - Tests help output
  - Tests project-specific listings
  - Tests error scenarios

- **`simple_test_toolbox.py`** - Simple standalone test runner
- **`run_toolbox_tests.py`** - Legacy test runner

## Running Tests

### With pytest (recommended in CI):
```bash
python -m pytest projects/core/tests/test_run_toolbox.py -v
python -m pytest projects/core/tests/test_run_toolbox_cli.py -v
```

### Standalone (for local testing):
```bash
python projects/core/tests/simple_test_toolbox.py
python projects/core/tests/run_toolbox_tests.py
```

## GitHub Actions Integration

The tests are automatically included in the existing `test_toolbox_dsl.yml` workflow because:

- Tests are located in `projects/core/tests/` (configured in `pyproject.toml`)
- Test files follow the `test_*.py` naming pattern
- Test functions follow the `test_*` naming pattern  
- Tests are pytest-compatible

## What the Tests Validate

1. **Project Discovery**: Ensures the script can find all projects with toolbox directories
2. **Command Discovery**: Ensures commands are properly discovered within projects (must have main.py)
3. **Description Extraction**: Validates that docstrings are extracted from both commands and projects
4. **CLI Interface**: Tests all command line argument combinations and error handling
5. **Import Handling**: Ensures proper cleanup of sys.path modifications

## Expected Test Results

All tests should pass in a properly configured FORGE environment. The tests validate:

- ✅ Basic functionality (discovery, descriptions, error handling)
- ✅ CLI interface (argument parsing, help, listings)
- ✅ Error scenarios (invalid projects/commands)

If tests fail, check:
1. That FORGE_HOME is properly set or discoverable
2. That expected projects exist with toolbox directories  
3. That toolbox commands have `main.py` files with `run` functions
4. That project `__init__.py` files have docstrings
