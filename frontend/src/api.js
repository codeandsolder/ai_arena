const BASE_URL = '/api/v1';

// Helper for API requests
async function apiRequest(endpoint, options = {}) {
  const url = `${BASE_URL}${endpoint}`;
  const config = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  };

  if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
    config.body = JSON.stringify(options.body);
  }

  const response = await fetch(url, config);
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

// Problem API
export const fetchProblems = () => apiRequest('/problems/');

export const importProblems = (data) => apiRequest('/problems/import', {
  method: 'POST',
  body: data,
});

export const importFromGithub = (data) => apiRequest('/problems/import/github', {
  method: 'POST',
  body: data,
});

export const syncTestsFromGithub = (problemId) => apiRequest(`/problems/${problemId}/tests/sync-github`, {
  method: 'POST',
});

export const fetchProblem = (id) => apiRequest(`/problems/${id}`);

export const createProblem = (data) => apiRequest('/problems/', {
  method: 'POST',
  body: data,
});

export const updateProblem = (id, data) => apiRequest(`/problems/${id}`, {
  method: 'PUT',
  body: data,
});

export const deleteProblem = (id) => apiRequest(`/problems/${id}`, {
  method: 'DELETE',
});

export const uploadTests = (id, zipFile) => {
  const formData = new FormData();
  formData.append('file', zipFile);
  
  return apiRequest(`/problems/${id}/tests`, {
    method: 'POST',
    headers: {},
    body: formData,
  });
};

export const fetchTests = (id) => apiRequest(`/problems/${id}/tests`);

export const fetchSolutions = (id) => apiRequest(`/problems/${id}/solutions`);

export const verifyExampleSolution = (id, solutionPath) => apiRequest(`/problems/${id}/verify-example`, {
  method: 'POST',
  body: solutionPath ? { solution_path: solutionPath } : {},
});

export const verifyExampleSolutionStream = async (id, solutionPath, onUpdate) => {
  const url = `${BASE_URL}/problems/${id}/verify-example-stream`;
  const body = solutionPath ? { solution_path: solutionPath } : {};
  
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // Keep partial line in buffer

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.substring(6));
          onUpdate(data);
        } catch (e) {
          console.error('Failed to parse SSE data', e, line);
        }
      }
    }
  }
};

export const fetchTestFile = (problemId, filename) => {
  return fetch(`${BASE_URL}/problems/${problemId}/tests/${filename}`).then(async (res) => {
    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`HTTP ${res.status}: ${errorText || 'Failed to fetch test file'}`);
    }
    return res.text();
  });
};

export const fetchStatus = () => apiRequest('/status');

// Run API
export const fetchRuns = () => apiRequest('/runs/');

export const fetchRun = (id) => apiRequest(`/runs/${id}`);

export const createRun = (data) => apiRequest('/runs/', {
  method: 'POST',
  body: data,
});

export const updateRun = (id, data) => apiRequest(`/runs/${id}`, {
  method: 'PUT',
  body: data,
});

export const deleteRun = (id) => apiRequest(`/runs/${id}`, {
  method: 'DELETE',
});

export const startRun = (id, numRounds = null) => {
  const params = numRounds !== null ? `?num_rounds=${numRounds}` : '';
  return apiRequest(`/runs/${id}/start${params}`, {
    method: 'POST',
  });
};

export const pauseRun = (id) => apiRequest(`/runs/${id}/pause`, {
  method: 'POST',
});

export const resumeRun = (id, numRounds = null) => {
  const params = numRounds !== null ? `?num_rounds=${numRounds}` : '';
  return apiRequest(`/runs/${id}/resume${params}`, {
    method: 'POST',
  });
};

// Round API
export const fetchRounds = (runId) => apiRequest(`/runs/${runId}/rounds`);

export const fetchRound = (id) => apiRequest(`/rounds/${id}`);

// Solution API
export const fetchSolution = (id) => apiRequest(`/solutions/${id}`);

export const fetchSolutionTests = (id) => apiRequest(`/solutions/${id}/tests`);

// API Calls API
export const fetchApiCalls = (runId, page = 1, pageSize = 50) => 
  apiRequest(`/runs/${runId}/api-calls?page=${page}&page_size=${pageSize}`);

export const fetchApiCall = (id) => apiRequest(`/api-calls/${id}`);

// WebSocket Helper
export const createWebSocket = (runId, onMessage) => {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/runs/${runId}`;
  const ws = new WebSocket(wsUrl);
  
  ws.onopen = () => {
    console.log(`WebSocket connected for run ${runId}`);
  };
  
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessage(data);
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error);
    }
  };
  
  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };
  
  ws.onclose = () => {
    console.log(`WebSocket disconnected for run ${runId}`);
  };
  
  return ws;
};

// Default export for convenience
export default {
  // Problems
  fetchProblems,
  fetchProblem,
  importProblems,
  importFromGithub,
  syncTestsFromGithub,
  createProblem,
  updateProblem,
  deleteProblem,
  uploadTests,
  fetchTests,
  fetchTestFile,
  verifyExampleSolution,
  // Runs
  fetchRuns,
  fetchRun,
  createRun,
  updateRun,
  deleteRun,
  startRun,
  pauseRun,
  resumeRun,
  // Rounds
  fetchRounds,
  fetchRound,
  // Solutions
  fetchSolution,
  fetchSolutionTests,
    // API Calls
  fetchApiCalls,
  fetchApiCall,
  // Status
  fetchStatus,
  // WebSocket
  createWebSocket,
};
