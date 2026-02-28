#!/usr/bin/env python3
"""
Verify all problems in the database by calling the local API.

This script fetches all problems, finds the "correct" solution for each,
and triggers the verification endpoint. It runs outside the FastAPI
context and assumes the backend is running locally.
"""

import argparse
import sys
import time
import requests
from requests.exceptions import ConnectionError

def main():
    parser = argparse.ArgumentParser(description="Verify all problems via local API.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1", help="Base API URL")
    args = parser.parse_args()

    base_url = args.url

    # 1. Fetch all problems
    try:
        print(f"Fetching problems from {base_url}/problems...")
        resp = requests.get(f"{base_url}/problems", timeout=10)
        resp.raise_for_status()
        problems = resp.json()
    except ConnectionError:
        print(f"Error: Could not connect to API at {base_url}. Is the backend running?")
        sys.exit(1)
    except Exception as e:
        print(f"Error fetching problems: {e}")
        sys.exit(1)

    if not problems:
        print("No problems found.")
        return

    print(f"Found {len(problems)} problems.")
    
    results = []

    for problem in problems:
        problem_id = problem.get("id")
        problem_name = problem.get("name") or problem.get("title") or f"Problem {problem_id}"
        
        print(f"\n--- Verifying Problem {problem_id}: {problem_name} ---")

        # 2. Fetch solutions
        try:
            sol_resp = requests.get(f"{base_url}/problems/{problem_id}/solutions", timeout=10)
            sol_resp.raise_for_status()
            solutions = sol_resp.json()
        except Exception as e:
            print(f"  [ERROR] Failed to fetch solutions: {e}")
            results.append((problem_id, problem_name, "ERROR", "Failed to fetch solutions"))
            continue

        if not solutions:
            print("  [WARN] No solutions found for this problem.")
            results.append((problem_id, problem_name, "WARN", "No solutions found"))
            continue

        # 3. Pick the best solution
        # Prefer expected_verdict == "correct", then highest expected_score, then first one
        best_solution = None
        for sol in solutions:
            if sol.get("expected_verdict") == "correct":
                best_solution = sol
                break
        
        if not best_solution:
            # Try by highest score
            scored_sols = [s for s in solutions if s.get("expected_score") is not None]
            if scored_sols:
                best_solution = max(scored_sols, key=lambda s: s.get("expected_score"))
            else:
                best_solution = solutions[0]

        sol_path = best_solution.get("path")
        print(f"  Selected solution: {sol_path} (Expected verdict: {best_solution.get('expected_verdict')}, Score: {best_solution.get('expected_score')})")

        # 4. Verify the solution
        try:
            print(f"  Verifying... (this might take a while)")
            start_time = time.time()
            verify_resp = requests.post(
                f"{base_url}/problems/{problem_id}/verify-example",
                json={"solution_path": sol_path},
                timeout=300  # Up to 5 minutes per problem
            )
            verify_resp.raise_for_status()
            verify_data = verify_resp.json()
            elapsed = time.time() - start_time
            
            # 5. Process results
            compile_success = verify_data.get("compile_success", False)
            all_passed = verify_data.get("all_passed", False)
            tests_passed = verify_data.get("tests_passed", 0)
            tests_total = verify_data.get("tests_total", 0)
            score = verify_data.get("score")
            
            if not compile_success:
                print(f"  [FAIL] Compilation failed in {elapsed:.1f}s")
                results.append((problem_id, problem_name, "FAIL", "Compilation failed"))
            elif all_passed:
                print(f"  [SUCCESS] All {tests_passed}/{tests_total} tests passed! Score: {score} ({elapsed:.1f}s)")
                results.append((problem_id, problem_name, "SUCCESS", f"{tests_passed}/{tests_total} tests, Score: {score}"))
            else:
                print(f"  [FAIL] {tests_passed}/{tests_total} tests passed. Score: {score} ({elapsed:.1f}s)")
                results.append((problem_id, problem_name, "FAIL", f"{tests_passed}/{tests_total} tests, Score: {score}"))
                
        except Exception as e:
            print(f"  [ERROR] Verification failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  Response: {e.response.text}")
            results.append((problem_id, problem_name, "ERROR", str(e)))

    # 6. Print summary
    print("\n" + "="*50)
    print("VERIFICATION SUMMARY")
    print("="*50)
    success_count = sum(1 for r in results if r[2] == "SUCCESS")
    print(f"Total problems: {len(results)}")
    print(f"Successfully verified: {success_count}")
    print(f"Failed/Errors: {len(results) - success_count}")
    print("-"*50)
    
    for pid, pname, status, details in results:
        print(f"[{status:7s}] {pid}: {pname} - {details}")

if __name__ == "__main__":
    main()