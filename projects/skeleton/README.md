# Skeleton Project

**Live demonstration of FORGE framework capabilities**

The Skeleton project is a **working example** that demonstrates all current FORGE capabilities in action. This is not just a template - it's a fully functional project showcasing real-world usage patterns for building test harnesses.

## 🎯 What This Project Demonstrates

### ⚙️ **Configuration Management**
- **YAML-based configuration** with environment-specific overrides
- **Preset system** for reusable configuration packages
- **CLI configuration overrides** for dynamic testing scenarios
- **Vault integration** for secure credential management

### 🔧 **Task-Based Toolboxes**
- **Cluster Information Toolbox**: Real cluster data gathering
- **@task decorators** for structured, observable operations
- **Context sharing** between tasks for state management
- **Artifact generation** with organized output files

### 🚀 **CLI & Orchestration**
- **Click-based CLI** with preset support and configuration overrides
- **Multi-phase execution** (prepare → test → cleanup)
- **Error handling** and exit code management
- **Integration patterns** between CLI, orchestration, and toolboxes

### 📊 **Observable Operations**
- **Comprehensive logging** with structured output
- **Artifact collection** in organized directories
- **Progress reporting** and status indicators
- **Configuration tracking** and preset application logs

## 🗂️ Project Structure

```
skeleton/
├── orchestration/
│   ├── cli.py                   # Feature-rich CLI with preset support
│   ├── ci.py                    # CI entrypoint integration
│   ├── config.yaml              # Base configuration with vault setup
│   ├── test_skeleton.py         # Main orchestration logic
│   ├── prepare_skeleton.py      # Environment preparation
│   └── presets.d/
│       ├── presets.yaml         # Collection of reusable presets
│       └── deep_testing.yaml   # Advanced testing configuration
└── toolbox/
    └── cluster_info/
        └── main.py              # Task-based cluster information gathering
```

## 🚀 Quick Start Examples

### 1. Basic Project Execution

```bash
# Standard phases - demonstrates complete workflow
./run_ci skeleton ci prepare    # Environment validation
./run_ci skeleton ci test       # Core functionality
./run_ci skeleton ci cleanup    # Resource cleanup
```

### 2. CLI with Preset Configuration

```bash
# Apply presets to modify behavior
cd projects/skeleton/orchestration
./cli.py --presets collect_cluster_info test
./cli.py --presets deep_testing test
./cli.py --presets collect_cluster_info --presets side_testing test
```

### 3. Direct Toolbox Execution

```bash
# Run toolbox independently
cd projects/skeleton/toolbox/cluster_info
python3 main.py --output-format json
python3 main.py --output-format yaml
```

## 🎛️ Configuration & Presets

### Available Presets

| Preset | Purpose | Configuration |
|--------|---------|---------------|
| `collect_cluster_info` | Enable cluster data gathering | Sets `skeleton.collect_cluster_info: true` |
| `side_testing` | Configure side testing parameters | Adds extra properties for load testing |
| `deep_testing` | Enable comprehensive testing | High-volume testing with multiple rates |

### Configuration Examples

```yaml
# config.yaml - Base configuration
skeleton:
  collect_cluster_info: false    # Control toolbox execution
  deep_testing: false           # Light vs comprehensive testing
  namespace: skeleton-dev       # Target namespace
  properties:
    rate: 1                     # Request rate
    max_requests: 30           # Request limit
    data: prompt_tokens=128    # Test data specification
```

### CLI Configuration Overrides

```bash
# Override any configuration value via CLI
./cli.py --presets deep_testing test                    # Apply preset
./cli.py test  # Use base configuration
```

## 🔍 What You'll See

### Sample Output

```bash
$ ./cli.py --presets collect_cluster_info test

INFO: Applying preset: collect_cluster_info
INFO: preset[collect_cluster_info] skeleton.collect_cluster_info --> True
INFO: === Skeleton Project Test Phase ===
INFO: Running the (fake) light testing ...
INFO: 
INFO: Fake test configuration:
skeleton:
  collect_cluster_info: true
  deep_testing: false
  namespace: skeleton-dev
  # ... more configuration

INFO: Running cluster information toolbox...
INFO: Timestamp: 2026-04-14T10:30:45-04:00
INFO: Current user: forge
INFO: Cluster nodes captured (3 nodes found)
INFO: ✅ Cluster information gathering completed successfully
```

### Generated Artifacts

```
$ARTIFACT_DIR/artifacts/
├── current_user.txt           # User information
├── cluster_nodes.yaml         # Node details (format based on CLI args)
├── cluster_version.txt        # OpenShift version
├── cluster_operators.txt      # Operator status
├── node_resources.txt         # Resource usage
├── storage_classes.txt        # Available storage
├── permissions.txt            # User permissions
└── summary_report.txt         # Consolidated summary
```

## 🛠️ Advanced Usage Patterns

### 1. Preset Combinations

```bash
# Combine multiple presets for complex scenarios
./cli.py --presets collect_cluster_info --presets deep_testing test

# Results in merged configuration:
# - cluster info gathering enabled
# - deep testing parameters applied
# - high request rates and volumes
```

### 2. Configuration Inspection

```bash
# View current effective configuration
./cli.py --presets deep_testing test | grep -A20 "Fake test configuration"
```

### 3. Vault Integration

The project demonstrates vault usage for secure credential management:

```yaml
# Vault configuration in config.yaml
vaults:
- psap-forge-notifications

notifications:
  vault:
    name: psap-forge-notifications
    github_token_key: github_token
    slack_webhook_key: slack_webhook_url
```

## 🏗️ Building Your Own Project

### Key Patterns Demonstrated

1. **Configuration Hierarchy**: Base config → Presets → CLI overrides
2. **Task Organization**: Structured @task functions with context sharing  
3. **Error Handling**: Proper exit codes and exception management
4. **Artifact Management**: Organized output with summary reporting
5. **CLI Design**: Feature-rich commands with multiple option types
6. **Integration**: Seamless orchestration ↔ toolbox communication

### Template Usage

Use this project as a **working reference** rather than copying it:

1. **Study the patterns** - See how configuration, presets, and toolboxes work together
2. **Understand the flow** - From CLI → orchestration → toolbox → artifacts
3. **Adapt the structure** - Use similar organization for your domain-specific testing
4. **Leverage the DSL** - @task decorators provide structure and observability

## 🔗 Integration Examples

### CI Pipeline Integration

```bash
# Automated CI usage
./run_ci skeleton ci prepare || exit 1
./run_ci skeleton ci test || exit 1  
./run_ci skeleton ci cleanup  # Always run cleanup

# With preset application
cd projects/skeleton/orchestration
./cli.py --presets production_settings test || exit 1
```

### Development Workflow

```bash
# Local development cycle
./cli.py test                                    # Quick test
./cli.py --presets collect_cluster_info test    # With cluster info
./cli.py --presets deep_testing test            # Full validation
```

## 📚 Learning Path

1. **Run the examples** - See the working system in action
2. **Examine the configuration** - Understand how presets modify behavior
3. **Study the toolbox** - Learn the task-based DSL patterns
4. **Review the CLI** - See advanced Click usage with FORGE integration
5. **Explore orchestration** - Understand phase organization and error handling

This project demonstrates that FORGE provides a **complete, production-ready framework** for building observable, configurable, and maintainable test harnesses.
./run_ci skeleton ci --help
```

## Creating Your Own Project

### Step 1: Copy Skeleton

```bash
cp -r projects/skeleton projects/your-project-name
cd projects/your-project-name
```

### Step 2: Customize

1. **Update `orchestration/ci.py`**:
   - Change `self.project_name` to your project name
   - Replace placeholder `echo` commands with actual test logic
   - Update the CLI description and help text

2. **Update `README.md`**:
   - Document your project's purpose and usage
   - Add specific setup instructions

3. **Add configuration** (optional):
   - Create `config.yaml` for project-specific settings
   - Reference it in your CI script

### Step 3: Implement Test Logic

Replace the example `echo` commands with your actual test logic:

#### Prepare Phase
```python
def prepare(self):
    self.log("Starting prepare phase...")

    # Example: Install dependencies
    if not self.execute_command("oc apply -f manifests/setup.yaml", "Deploy setup resources"):
        return 1

    # Example: Validate environment
    if not self.execute_command("oc get nodes", "Check cluster nodes"):
        return 1

    self.log("Prepare phase completed!", "success")
    return 0
```

#### Test Phase
```python
def test(self):
    self.log("Starting test phase...")

    # Example: Run performance tests
    if not self.execute_command(
        "python scripts/performance_test.py --config config.yaml", "Running performance tests"
    ):
        return 1

    # Example: Run functional tests
    if not self.execute_command("pytest tests/ -v", "Running functional tests"):
        return 1

    self.log("Test phase completed!", "success")
    return 0
```

#### Cleanup Phase
```python
def cleanup(self):
    self.log("Starting cleanup phase...")

    # Example: Remove test resources
    self.execute_command("oc delete -f manifests/", "Cleanup test resources")

    # Example: Generate reports
    self.execute_command("python scripts/generate_report.py", "Generate final report")

    self.log("Cleanup phase completed!", "success")
    return 0
```

## Key Patterns

### 1. Phase Structure

Each project should implement these standard phases:
- **prepare**: Set up environment and dependencies
- **test**: Execute main testing logic
- **cleanup**: Clean up resources and finalize

### 2. Command Execution

Use the `execute_command` method for consistent execution and logging:

```python
# Basic command execution
success = self.execute_command("your-command", "Description")
if not success:
    return 1  # Exit with error

# Command with complex logic
result = self.execute_command("kubectl get pods -o json", "Check pod status")
```

### 3. Error Handling

Always check command results and handle failures appropriately:

```python
if not self.execute_command("critical-command", "Critical step"):
    self.log("Critical step failed!", "error")
    return 1  # Exit with error code

# Cleanup commands can be non-critical
self.execute_command("cleanup-command", "Optional cleanup")
# Continue regardless of success
```

### 4. Logging

Use the logging methods for consistent output:

```python
self.log("Starting operation", "info")  # ℹ️ [project] Starting operation
self.log("Operation completed", "success")  # ✅ [project] Operation completed
self.log("Warning occurred", "warning")  # ⚠️ [project] Warning occurred
self.log("Error occurred", "error")  # ❌ [project] Error occurred
```

### 5. Verbose Mode

The framework automatically handles verbose mode:

```python
# In verbose mode, command details are automatically shown
# Your execute_command calls will show:
# - Command being executed
# - Command output (if any)
# - Execution duration
```

## Click CLI Structure

The skeleton uses Click groups to organize commands:

```python
@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx, verbose):
    """Project CI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    ctx.obj.verbose = verbose
    ctx.obj.runner = YourProjectTestRunner(verbose)


@cli.command()
@click.pass_context
def prepare(ctx):
    """Prepare phase - Set up environment and dependencies."""
    runner = ctx.obj.runner
    exit_code = runner.prepare()
    sys.exit(exit_code)
```

## Best Practices

### 1. Constitutional Compliance

- ✅ **CI-First**: Design for automated execution without user interaction
- ✅ **Observable**: Log important events and command execution
- ✅ **Reproducible**: Use deterministic operations and clear error codes
- ✅ **Scale-Aware**: Keep operations efficient and focused
- ✅ **AI Platform Specific**: Focus on OpenShift AI scenarios and tooling

### 2. Error Handling

- Always validate prerequisites in prepare phase
- Check command results and fail fast on errors
- Provide meaningful error messages with context
- Clean up resources even when tests fail (use try/except if needed)

### 3. Command Design

- Make commands idempotent when possible
- Use meaningful descriptions for all execute_command calls
- Test commands locally before adding to CI
- Consider timeouts for long-running operations

### 4. Configuration

- Keep project configuration in `config.yaml` or environment variables
- Make tests configurable for different environments
- Document all configuration options
- Use sensible defaults

## Testing the Skeleton

```bash
# Test individual phases
./run_ci skeleton ci prepare
./run_ci skeleton ci test
./run_ci skeleton ci cleanup

# Test with verbose output
./run_ci skeleton ci --verbose prepare

# See all available commands
./run_ci skeleton ci --help
```

## Integration with CI Systems

The skeleton is designed for easy CI integration:

```bash
# In your CI pipeline
./run_ci your-project ci prepare || exit 1
./run_ci your-project ci test || exit 1
./run_ci your-project ci cleanup  # Always run cleanup
```

## Next Steps

1. **Study the Code**: Review `orchestration/ci.py` to understand the patterns
2. **Copy and Customize**: Create your own project based on this skeleton
3. **Implement Tests**: Replace placeholder `echo` commands with real test logic
4. **Test Integration**: Verify your project works with the run_ci entrypoint
5. **Add Documentation**: Document your specific test scenarios and setup

## Support

- Review other projects in `projects/` for more examples
- Check the main FORGE documentation
- Study the run_ci entrypoint code in `projects/core/ci_entrypoint/`
