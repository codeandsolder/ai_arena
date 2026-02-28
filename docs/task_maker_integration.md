# Task-Maker-Rust Integration Design Document

## Overview
This document proposes the architectural changes required to transition the current grading sandbox from the custom C++ harness (`docker/test_harness.cpp`) to `task-maker-rust`. `task-maker-rust` is the standard tool for grading Italian Olympiad/IOI format tasks, providing robust test case generation, evaluation, and sandboxing out-of-the-box.

## 1. Installation & Dockerfile Updates

The current `Dockerfile.sandbox` relies on an Ubuntu base image with GCC/Clang and a custom `/opt/harness/run_test` binary. We will update the image to include `task-maker-rust`.

### Changes to `docker/Dockerfile.sandbox`:
- Add the `task-maker-rust` repository and install it via `apt-get`, or download the pre-compiled `.deb` binary directly from GitHub releases.
- Ensure `libseccomp2` (or `libseccomp-dev`) is installed, as `task-maker-rust` relies on it for `isolate`-like sandboxing.
- Remove the custom `test_harness.cpp` compilation step.

```dockerfile
# Add task-maker-rust repository and install
RUN echo "deb [signed-by=/etc/apt/keyrings/task-maker-rust.asc] https://artifacts.lucaversari.it/olimpiadi-informatica/task-maker-rust/latest/deb/$(lsb_release -cs) /" | tee /etc/apt/sources.list.d/task-maker-rust.list && \
    curl -s https://artifacts.lucaversari.it/signing-key.asc | tee /etc/apt/keyrings/task-maker-rust.asc > /dev/null && \
    apt-get update && \
    apt-get install -y task-maker-rust libseccomp2 && \
    rm -rf /var/lib/apt/lists/*
```

## 2. Invoking task-maker-rust in Sandbox

Currently, `benchmark_solution` in `backend/sandbox/benchmark.py` loops over `.in` and `.out` pairs manually and invokes the container for each pair. 
With `task-maker-rust`, the grading is automated at the task level.

### Changes to `backend/sandbox/benchmark.py`:
1. **Task Directory Structure**: `task-maker-rust` requires a valid CMS/IOI task structure (typically a directory with `task.yaml`, `gen/`, `sol/`, etc., or at least proper input/output files and a `task.yaml`).
2. **Command Execution**: Instead of running a test harness per test case, we mount the entire task directory and the student solution into the container, then execute:
   ```bash
   task-maker-rust --ui json <path_to_solution>
   ```
3. **Capture Output**: The `--ui json` flag ensures that `task-maker-rust` outputs its progress and results as a stream of JSON objects (one per line) to `stdout`.

## 3. Parsing the JSON Output

The JSON lines correspond to variants of the `UIMessage` enum from `task-maker-rust`. To map these events into our existing `TestCaseResult` and `BenchmarkSummary` structures, the python runner will parse the `stdout` line by line.

### Relevant `UIMessage` Events:

1. **`IOIEvaluation`**: 
   Emitted when a solution is run against a test case. When the execution finishes, the message looks like:
   ```json
   {
     "IOIEvaluation": {
       "subtask": 1,
       "testcase": 1,
       "solution": "/tmp/solution.cpp",
       "status": {
         "Done": {
           "result": {
             "status": "Success", 
             "resources": {
               "cpu_time": 0.05,
               "sys_time": 0.01,
               "wall_time": 0.06,
               "memory": 12048
             }
           }
         }
       },
       "part": 0,
       "num_parts": 1
     }
   }
   ```
   *Extraction:* We can extract `time_ms` (`cpu_time * 1000`) and `memory_kb` (`memory`). We can also identify crashes or Time Limit Exceeded (TLE) from the `status` (`TimeLimitExceeded`, `MemoryLimitExceeded`, `ReturnCode(code)`, etc.).

2. **`IOITestcaseScore`**:
   Emitted when the checker finishes verifying the output.
   ```json
   {
     "IOITestcaseScore": {
       "subtask": 1,
       "testcase": 1,
       "solution": "/tmp/solution.cpp",
       "score": 1.0,
       "message": "Correct output"
     }
   }
   ```
   *Extraction:* We use the `score` to determine the boolean `passed` flag (typically `score == 1.0` or > 0 depending on partial scoring).

3. **`IOITaskScore`**:
   Emitted at the very end summarizing the final score.
   ```json
   {
     "IOITaskScore": {
       "solution": "/tmp/solution.cpp",
       "score": 100.0
     }
   }
   ```

### Mapping to `TestCaseResult`:
- **`test_index` / `test_name`**: Can be derived from `subtask` and `testcase` IDs.
- **`passed`**: `True` if `IOITestcaseScore.score == 1.0`.
- **`time_ms`**: `IOIEvaluation.status.Done.result.resources.cpu_time * 1000`.
- **`memory_kb`**: `IOIEvaluation.status.Done.result.resources.memory`.
- **`verdict`**: 
  - If `IOIEvaluation` status is `TimeLimitExceeded` -> `"TLE"`
  - If `MemoryLimitExceeded` -> `"MLE"`
  - If `ReturnCode` / `Signal` -> `"RE"`
  - If `Success` and `IOITestcaseScore` is `1.0` -> `"AC"`
  - If `Success` and `IOITestcaseScore` is `0.0` -> `"WA"`

## 4. Implementation Steps

1. **Update `docker/Dockerfile.sandbox`** to install `task-maker-rust` (remove `test_harness.cpp` logic).
2. **Refactor `backend/sandbox/benchmark.py`**:
   - Change `_run_single_test` loop to a single container execution: `task-maker-rust --ui json <solution>`.
   - Implement a streaming or line-by-line JSON parser to aggregate `IOIEvaluation` and `IOITestcaseScore` messages.
   - Combine the parsed data into `TestCaseResult` objects.
   - Aggregate the `TestCaseResult`s into the final `BenchmarkSummary`.
3. **Verify Task Setup**: Ensure the system creates or mounts a `task.yaml` correctly so `task-maker-rust` knows the constraints and checker.
4. **Testing**: Run integration tests to ensure that AC, WA, TLE, MLE, and RE verdicts are properly tracked.