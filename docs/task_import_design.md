# IOI Task Import Design

## Overview
The current task ingestion system (`backend/services/problem_ingestion.py`) is designed for the Hendrycks APPS dataset and needs to be completely refactored or augmented to support importing tasks from IOI GitHub repositories (e.g., `https://github.com/austrian-olympiad-informatics/ioi-tasks/tree/main/ioi2023-soccer`).

## 1. Fetching from GitHub
Instead of hardcoding base URLs and raw fetches for APPS structure, we will use the **GitHub REST API** to inspect the repository contents dynamically.

- **URL Parsing**: The provided URL (e.g., `https://github.com/austrian-olympiad-informatics/ioi-tasks/tree/main/ioi2023-soccer`) will be parsed to extract:
  - Owner: `austrian-olympiad-informatics`
  - Repo: `ioi-tasks`
  - Ref/Branch: `main`
  - Path: `ioi2023-soccer`
- **Fetching Contents**: We will use the GitHub API `GET /repos/{owner}/{repo}/contents/{path}?ref={ref}` to list the files in the problem directory.
- **Specs First**: We will search for a `statement` directory or `.pdf` statement files directly. By examining the JSON response from the GitHub API, we will obtain the `download_url` for the PDF and fetch it.
- **Authentication**: To avoid severe rate-limiting (60 requests/hour for unauthenticated IPs), the backend should support an optional `GITHUB_TOKEN` environment variable.

## 2. PDF to Markdown Conversion
IOI tasks are typically provided as PDF files. We need to convert these PDFs into Markdown format (`description_md` in `ProblemBase`).
- **Recommended Library**: `pymupdf4llm` (based on `PyMuPDF`/`fitz`).
- **Reasoning**: This library is specifically tailored for extracting text, tables, and standard formatting from PDFs into LLM-friendly Markdown. It preserves the logical reading order and headers much better than standard extractors like `pdfminer` or plain `pypdf`.
- **Alternative**: `Marker` (by VikParuchuri) is another powerful option but has a larger footprint and more dependencies (like PyTorch). `pymupdf4llm` is lightweight and performs excellently for cleanly formatted documents like IOI tasks.

## 3. API Changes and Lazy Loading of Tests
Currently, the import API is `POST /problems/import` taking `start_id` and `end_id`. We will modify this or create a new endpoint specifically for GitHub imports.

### A. New Problem Import Request Endpoint
- **Endpoint**: `POST /problems/import/github`
- **Payload**:
  ```json
  {
    "github_url": "https://github.com/austrian-olympiad-informatics/ioi-tasks/tree/main/ioi2023-soccer"
  }
  ```
- **Action**:
  1. Parses the GitHub URL.
  2. Fetches the repository contents via GitHub API.
  3. Downloads the statement PDF.
  4. Parses the PDF to Markdown using `pymupdf4llm`.
  5. Creates a new `Problem` record with the parsed Markdown, storing the GitHub URL or source information in a new database column (or metadata field) for later test fetching.
  6. **Critically**: Does *not* download the `tests/` directory at this stage. Sets `test_count = 0` or marks tests as `unloaded`.

### B. New Test Download Endpoint
To satisfy the "must only download tests if explicitly selected by the user" requirement, we will introduce a new endpoint.
- **Endpoint**: `POST /problems/{problem_id}/tests/sync-github`
- **Action**:
  1. Retrieves the problem from the database to find its source GitHub URL.
  2. Uses the GitHub API to list contents of the `tests/` directory (or similar standard IOI test directory) within that problem's path.
  3. Fetches each `.in` and `.out` file using their `download_url`.
  4. Saves them to the local `PROBLEMS_DIR / str(problem_id) / "tests"`.
  5. Updates the `test_count` for the problem in the database.
- **Consideration**: Test directories can contain many files. This process should ideally be run as a FastAPI `BackgroundTasks` job to avoid timeout, similar to the current batch import logic. A WebSocket or polling mechanism could notify the frontend of progress.

## 4. Database Schema Changes
To support the lazy loading and connection back to the GitHub source:
- Update the `Problem` model (`backend/database/models.py`) to include:
  - `source_url` (String, nullable): To store the GitHub URL it was imported from.
  - Optionally, a `tests_downloaded` (Boolean, default False) to indicate the test sync state.

## 5. Architectural Flow
1. **User (Frontend)** requests import with a GitHub URL.
2. **API (`api/problems.py`)** receives the request and triggers `IOIIngestor` (a new class in `problem_ingestion.py`).
3. **`IOIIngestor`** calls GitHub API, downloads PDF, converts via `pymupdf4llm`, and persists the `Problem` to the DB.
4. **API** responds with the created problem summary (tests = 0).
5. **User (Frontend)** views the problem, sees tests are missing, and clicks "Download Tests".
6. **API** receives request to `/problems/{id}/tests/sync-github`.
7. **`IOIIngestor`** runs in background, paginates through the GitHub `tests/` tree, downloads all pairs, and updates the local filesystem and DB `test_count`.