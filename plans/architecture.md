# Architecture Design: External Problem Downloader

## 1. Selected Repository
**Chosen Source:** **Hendrycks APPS (Automated Programming Progress Standard) Dataset**
- **Reasoning:** APPS is a comprehensive, free, LeetCode-like repository containing over 10,000 coding problems with varying difficulty levels (introductory, interview, competition). Unlike the Codeforces API (which often only exposes sample test cases) or CSES (no official test case API), APPS provides full problem descriptions in Markdown (`question.txt`) and extensive, structured test sets (inputs and outputs in `input_output.json`). It is freely accessible via GitHub and the HuggingFace Datasets API, making it perfectly suited for an automated ingestion pipeline.

## 2. Current Architecture Context
Based on the review of the current system:
- **Database Schema (`backend/database/models.py`)**: The `Problem` model requires `name`, `slug`, `description_md`, `time_limit_ms`, `memory_limit_mb`, `test_count`, and `scoring_mode`.
- **API and File Storage (`backend/api/problems.py`)**: Problems are stored in the database, while test cases are managed on the filesystem at `backend/data/problems/{problem_id}/tests/`. Test cases must follow a specific naming convention: paired `.in` and `.out` files (e.g., `1.in`, `1.out`). The `test_count` field in the database tracks the number of these pairs.

## 3. Technical Specification of Changes

### A. New Module: Problem Ingestion Service
Create a new module `backend/services/problem_ingestion.py` (or a CLI script `backend/scripts/import_problems.py`) responsible for fetching and parsing external problems.

#### Components:
1. **Fetcher:**
   - Use the `httpx` or `requests` library to fetch problem data from the APPS HuggingFace dataset endpoint or GitHub repository.
   - Support fetching a single problem by ID or batch fetching.

2. **Parser & Transformer:**
   - **Metadata Extraction:** Extract the problem title (if available, otherwise derive from the directory name) and convert it into a URL-friendly `slug`.
   - **Content:** Read `question.txt` and map it to `description_md`.
   - **Limits:** Set default values for `time_limit_ms` (e.g., 2000) and `memory_limit_mb` (e.g., 256) since APPS doesn't enforce strict limits natively, or map them if metadata exists.

3. **Test Case Processing:**
   - Read and parse `input_output.json`.
   - Extract the lists of `inputs` and `outputs`.
   - For each input/output pair (index `i`), generate `i.in` and `i.out` file contents.

### B. Database & Filesystem Integration
1. **Database Insertion:**
   - Use the existing `AsyncSession` to create a new `Problem` record.
   - Handle unique constraints on `slug` (append a unique identifier or skip if it exists).
   - Flush the session to obtain the generated `problem_id`.
2. **Filesystem Storage:**
   - Call the existing `get_problem_tests_dir(problem_id)` helper from `backend.api.problems`.
   - Create the directory if it doesn't exist.
   - Write the generated `.in` and `.out` files into this directory.
3. **Completion:**
   - Update the `test_count` on the `Problem` record using the `count_test_cases` helper.
   - Commit the transaction.

### C. Optional API Endpoint (If Triggered via UI)
If the ingestion needs to be triggered from the `ai_arena` frontend:
- Add a new endpoint to `backend/api/problems.py` (e.g., `POST /problems/import`).
- Accept a source URL or problem ID.
- Execute the ingestion service asynchronously to prevent blocking the HTTP request.