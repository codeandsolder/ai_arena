/**
 * Test Harness for AI Optimization Arena
 * 
 * Runs solution binaries with resource limits and measures performance.
 * 
 * Usage: ./run_test <solution_binary> <input_file> <expected_output_file> 
 *                  <time_limit_ms> <memory_limit_mb> <num_runs> <warmup_runs>
 */

#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <signal.h>
#include <fcntl.h>
#include <errno.h>
#include <ctime>

// JSON output helpers
std::string escape_json(const std::string& str) {
    std::ostringstream oss;
    for (char c : str) {
        switch (c) {
            case '"': oss << "\\\""; break;
            case '\\': oss << "\\\\"; break;
            case '\b': oss << "\\b"; break;
            case '\f': oss << "\\f"; break;
            case '\n': oss << "\\n"; break;
            case '\r': oss << "\\r"; break;
            case '\t': oss << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    oss << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                } else {
                    oss << c;
                }
        }
    }
    return oss.str();
}

// Get current time in milliseconds using CLOCK_MONOTONIC
double get_time_ms() {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return -1.0;
    }
    return static_cast<double>(ts.tv_sec) * 1000.0 + static_cast<double>(ts.tv_nsec) / 1000000.0;
}

// Read entire file contents
std::string read_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        return "";
    }
    std::stringstream buffer;
    buffer << file.rdbuf();
    return buffer.str();
}

// Trim whitespace from both ends
std::string trim(const std::string& str) {
    size_t start = str.find_first_not_of(" \t\n\r");
    if (start == std::string::npos) return "";
    size_t end = str.find_last_not_of(" \t\n\r");
    return str.substr(start, end - start + 1);
}

// Get memory usage (VmPeak in KB) from /proc/<pid>/status
long get_memory_kb(pid_t pid) {
    std::string path = "/proc/" + std::to_string(pid) + "/status";
    std::ifstream file(path);
    if (!file.is_open()) {
        return -1;
    }
    
    std::string line;
    while (std::getline(file, line)) {
        if (line.find("VmPeak:") == 0) {
            // Format: "VmPeak:    12345 kB"
            size_t colon_pos = line.find(':');
            if (colon_pos != std::string::npos) {
                std::string value_str = line.substr(colon_pos + 1);
                // Remove "kB" and whitespace
                size_t kb_pos = value_str.find("kB");
                if (kb_pos != std::string::npos) {
                    value_str = value_str.substr(0, kb_pos);
                }
                return std::stol(value_str);
            }
        }
    }
    return -1;
}

// Run single test iteration
struct TestResult {
    bool passed;
    std::string actual_output;
    double time_ms;
    long memory_kb;
    int exit_code;
    std::string error;
    std::string verdict;
};

TestResult run_single_test(
    const std::string& solution_binary,
    const std::string& input_file,
    const std::string& expected_output_file,
    long time_limit_ms,
    long memory_limit_mb
) {
    TestResult result;
    result.passed = false;
    result.time_ms = -1.0;
    result.memory_kb = -1;
    result.exit_code = -1;
    result.verdict = "RE";
    
    // Read expected output
    std::string expected_output = trim(read_file(expected_output_file));
    
    // Create pipes for stdout capture
    int stdout_pipe[2];
    if (pipe(stdout_pipe) == -1) {
        result.error = "Failed to create stdout pipe: " + std::string(strerror(errno));
        return result;
    }
    
    // Open input file
    int input_fd = open(input_file.c_str(), O_RDONLY);
    if (input_fd == -1) {
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        result.error = "Failed to open input file: " + std::string(strerror(errno));
        return result;
    }
    
    // Fork child process
    pid_t pid = fork();
    
    if (pid < 0) {
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        close(input_fd);
        result.error = "Fork failed: " + std::string(strerror(errno));
        return result;
    }
    
    if (pid == 0) {
        // Child process
        
        // Close read end of stdout pipe
        close(stdout_pipe[0]);
        
        // Redirect stdin from input file
        if (dup2(input_fd, STDIN_FILENO) == -1) {
            _exit(100);
        }
        close(input_fd);
        
        // Redirect stdout to pipe
        if (dup2(stdout_pipe[1], STDOUT_FILENO) == -1) {
            _exit(100);
        }
        close(stdout_pipe[1]);
        
        // Set memory limit (RLIMIT_AS for address space)
        struct rlimit mem_limit;
        mem_limit.rlim_cur = memory_limit_mb * 1024 * 1024;  // Convert MB to bytes
        mem_limit.rlim_max = memory_limit_mb * 1024 * 1024;
        if (setrlimit(RLIMIT_AS, &mem_limit) != 0) {
            _exit(101);
        }
        
        // Set CPU time limit (RLIMIT_CPU)
        struct rlimit cpu_limit;
        cpu_limit.rlim_cur = (time_limit_ms + 999) / 1000 + 1;  // Ceiling to seconds + 1 buffer
        cpu_limit.rlim_max = cpu_limit.rlim_cur + 1;
        setrlimit(RLIMIT_CPU, &cpu_limit);
        
        // Execute solution
        execl(solution_binary.c_str(), solution_binary.c_str(), nullptr);
        
        // If execl returns, it failed
        _exit(102);
    }
    
    // Parent process
    close(stdout_pipe[1]);
    close(input_fd);
    
    // Record start time
    double start_time = get_time_ms();
    
    // Read stdout from child
    std::string output;
    char buffer[4096];
    ssize_t bytes_read;
    
    while ((bytes_read = read(stdout_pipe[0], buffer, sizeof(buffer) - 1)) > 0) {
        buffer[bytes_read] = '\0';
        output += buffer;
    }
    close(stdout_pipe[0]);
    
    // Wait for child with timeout
    int status;
    pid_t wait_result;
    double elapsed_ms = 0;
    bool timed_out = false;
    
    while (true) {
        wait_result = waitpid(pid, &status, WNOHANG);
        
        if (wait_result == pid) {
            // Child exited
            break;
        } else if (wait_result == 0) {
            // Still running, check timeout
            elapsed_ms = get_time_ms() - start_time;
            
            if (elapsed_ms > time_limit_ms) {
                // Kill the child process
                kill(pid, SIGKILL);
                timed_out = true;
                // Wait for the killed process
                waitpid(pid, &status, 0);
                break;
            }
            
            // Small sleep to avoid busy waiting
            usleep(1000);  // 1ms
        } else {
            // Error
            result.error = "waitpid failed: " + std::string(strerror(errno));
            return result;
        }
    }
    
    // Record end time
    double end_time = get_time_ms();
    result.time_ms = end_time - start_time;
    
    // Get memory usage before process exits completely
    result.memory_kb = get_memory_kb(pid);
    
    // Determine verdict
    if (timed_out) {
        result.verdict = "TLE";
        result.exit_code = -1;
        result.error = "Time limit exceeded";
    } else if (WIFSIGNALED(status)) {
        int sig = WTERMSIG(status);
        if (sig == SIGKILL || sig == SIGXCPU) {
            result.verdict = "TLE";
            result.error = "Time limit exceeded (signal " + std::to_string(sig) + ")";
        } else if (sig == SIGSEGV || sig == SIGABRT) {
            result.verdict = "MLE";
            result.error = "Memory limit exceeded or segmentation fault (signal " + std::to_string(sig) + ")";
        } else {
            result.verdict = "RE";
            result.error = "Runtime error (signal " + std::to_string(sig) + ")";
        }
        result.exit_code = -sig;
    } else if (WIFEXITED(status)) {
        result.exit_code = WEXITSTATUS(status);
        
        if (result.exit_code != 0) {
            result.verdict = "RE";
            result.error = "Runtime error (exit code " + std::to_string(result.exit_code) + ")";
        } else {
            // Check output
            result.actual_output = output;
            std::string trimmed_output = trim(output);
            
            if (trimmed_output == expected_output) {
                result.passed = true;
                result.verdict = "AC";
            } else {
                result.verdict = "WA";
                result.error = "Wrong answer";
            }
        }
    } else {
        result.verdict = "RE";
        result.error = "Unknown process status";
    }
    
    return result;
}

// Calculate median from vector of doubles
double calculate_median(std::vector<double>& values) {
    if (values.empty()) {
        return 0.0;
    }
    
    std::sort(values.begin(), values.end());
    size_t n = values.size();
    
    if (n % 2 == 0) {
        return (values[n / 2 - 1] + values[n / 2]) / 2.0;
    } else {
        return values[n / 2];
    }
}

int main(int argc, char* argv[]) {
    // Check arguments
    if (argc != 8) {
        std::cerr << "Usage: " << argv[0] << " <solution_binary> <input_file> <expected_output_file> "
                  << "<time_limit_ms> <memory_limit_mb> <num_runs> <warmup_runs>" << std::endl;
        return 1;
    }
    
    std::string solution_binary = argv[1];
    std::string input_file = argv[2];
    std::string expected_output_file = argv[3];
    long time_limit_ms = std::stol(argv[4]);
    long memory_limit_mb = std::stol(argv[5]);
    int num_runs = std::stoi(argv[6]);
    int warmup_runs = std::stoi(argv[7]);
    
    // Validate arguments
    if (num_runs < 1) num_runs = 1;
    if (warmup_runs < 0) warmup_runs = 0;
    
    // Storage for all successful run times
    std::vector<double> all_times;
    std::string final_output;
    std::string final_error;
    std::string final_verdict = "AC";
    long final_memory_kb = 0;
    int final_exit_code = 0;
    bool any_passed = false;
    
    // Run warmup iterations (not counted in results)
    for (int i = 0; i < warmup_runs; i++) {
        TestResult warmup = run_single_test(
            solution_binary, input_file, expected_output_file,
            time_limit_ms, memory_limit_mb
        );
        // Warmup results are discarded
    }
    
    // Run measured iterations
    for (int i = 0; i < num_runs; i++) {
        TestResult run = run_single_test(
            solution_binary, input_file, expected_output_file,
            time_limit_ms, memory_limit_mb
        );
        
        // Store the output from first run (or any run that produces output)
        if (i == 0 || final_output.empty()) {
            final_output = run.actual_output;
            final_error = run.error;
            final_verdict = run.verdict;
            final_memory_kb = run.memory_kb;
            final_exit_code = run.exit_code;
        }
        
        if (run.passed) {
            any_passed = true;
        }
        
        // Only record time if not TLE
        if (run.verdict != "TLE" && run.time_ms > 0) {
            all_times.push_back(run.time_ms);
        }
        
        // If we get a definitive failure (WA, RE, MLE), we can stop early
        // But we continue for TLE to confirm it's consistent
        if (run.verdict == "WA" || run.verdict == "RE" || run.verdict == "MLE") {
            final_verdict = run.verdict;
            final_error = run.error;
            final_exit_code = run.exit_code;
            // Don't break - run all iterations for complete timing data
        }
    }
    
    // Calculate median time
    double median_time = calculate_median(all_times);
    
    // Determine final passed status
    // Passed only if at least one run passed and no WA/RE/MLE
    bool final_passed = any_passed && 
                        final_verdict != "WA" && 
                        final_verdict != "RE" && 
                        final_verdict != "MLE";
    
    // Build all_times JSON array
    std::string all_times_json = "[";
    for (size_t i = 0; i < all_times.size(); i++) {
        if (i > 0) all_times_json += ", ";
        char buf[32];
        snprintf(buf, sizeof(buf), "%.3f", all_times[i]);
        all_times_json += buf;
    }
    all_times_json += "]";
    
    // Output JSON result
    std::cout << "{";
    std::cout << "\"passed\": " << (final_passed ? "true" : "false") << ", ";
    std::cout << "\"actual_output\": \"" << escape_json(final_output) << "\", ";
    std::cout << "\"time_ms\": ";
    if (median_time > 0) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%.3f", median_time);
        std::cout << buf;
    } else {
        std::cout << "null";
    }
    std::cout << ", ";
    std::cout << "\"memory_kb\": " << final_memory_kb << ", ";
    std::cout << "\"exit_code\": " << final_exit_code << ", ";
    std::cout << "\"error\": \"" << escape_json(final_error) << "\", ";
    std::cout << "\"verdict\": \"" << final_verdict << "\", ";
    std::cout << "\"all_times_ms\": " << all_times_json;
    std::cout << "}" << std::endl;
    
    return 0;
}
