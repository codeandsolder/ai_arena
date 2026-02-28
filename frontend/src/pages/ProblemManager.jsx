import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Edit2, Trash2, Upload, Eye, EyeOff, FileArchive, FileCode, Download, Github, RefreshCw, CheckCircle2, XCircle, FlaskConical, Clock, MemoryStick, Code, Play } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import toast from 'react-hot-toast';
import {
  fetchProblems,
  fetchProblem,
  createProblem,
  updateProblem,
  deleteProblem,
  uploadTests,
  fetchTests,
  fetchSolutions,
  fetchTestFile,
  importProblems,
  importFromGithub,
  syncTestsFromGithub,
  verifyExampleSolution,
  verifyExampleSolutionStream,
} from '../api';

function ProblemManager() {
  const [problems, setProblems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingProblem, setEditingProblem] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [showImportForm, setShowImportForm] = useState(false);
  const [selectedProblem, setSelectedProblem] = useState(null);
  const [tests, setTests] = useState([]);
  const [solutions, setSolutions] = useState([]);
  const [previewTest, setPreviewTest] = useState(null);
  const [previewContent, setPreviewContent] = useState({ input: null, output: null });
  const [previewLoading, setPreviewLoading] = useState(false);
  const [verifyResults, setVerifyResults] = useState({});
  const [verifyingMap, setVerifyingMap] = useState({});

  const [formData, setFormData] = useState({
    name: '',
    slug: '',
    description_md: '',
    time_limit_ms: 1000,
    memory_limit_mb: 256,
    scoring_mode: 'binary',
  });

  const [importData, setImportData] = useState({
    url: '',
    download_tests: false,
  });

  const [importing, setImporting] = useState(false);
  const [syncingTests, setSyncingTests] = useState(false);

  const loadProblems = useCallback(async () => {
    try {
      const data = await fetchProblems();
      setProblems(data);
    } catch (error) {
      toast.error('Failed to load problems: ' + error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProblems();
  }, [loadProblems]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingProblem) {
        await updateProblem(editingProblem.id, formData);
        toast.success('Problem updated');
        // Refresh selected problem details if it's the one being edited
        if (selectedProblem?.id === editingProblem.id) {
          const updated = await fetchProblem(editingProblem.id);
          setSelectedProblem(updated);
        }
      } else {
        await createProblem(formData);
        toast.success('Problem created');
      }
      resetForm();
      loadProblems();
    } catch (error) {
      toast.error('Failed to save problem: ' + error.message);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Are you sure you want to delete this problem?')) return;
    try {
      await deleteProblem(id);
      toast.success('Problem deleted');
      if (selectedProblem?.id === id) {
        setSelectedProblem(null);
        setTests([]);
        setPreviewTest(null);
        setPreviewContent({ input: null, output: null });
      }
      loadProblems();
    } catch (error) {
      toast.error('Failed to delete problem: ' + error.message);
    }
  };

  const handleEdit = (problem) => {
    setEditingProblem(problem);
    setFormData({
      name: problem.name,
      slug: problem.slug,
      // Bug fix: use description_md consistently (was 'description' before)
      description_md: problem.description_md || '',
      time_limit_ms: problem.time_limit_ms,
      memory_limit_mb: problem.memory_limit_mb,
      scoring_mode: problem.scoring_mode,
    });
    setShowForm(true);
  };

  const resetForm = () => {
    setEditingProblem(null);
    setFormData({
      name: '',
      slug: '',
      // Bug fix: use description_md consistently (was 'description' before)
      description_md: '',
      time_limit_ms: 1000,
      memory_limit_mb: 256,
      // Bug fix: use valid scoring_mode value (was 'standard' before, backend expects binary|partial|custom)
      scoring_mode: 'binary',
    });
    setShowForm(false);
  };

  const handleImport = async (e) => {
    e.preventDefault();
    setImporting(true);
    try {
      const response = await importFromGithub(importData);
      toast.success(response.message || 'Import started');
      setShowImportForm(false);
      setTimeout(loadProblems, 2000);
    } catch (error) {
      toast.error('Failed to start import: ' + error.message);
    } finally {
      setImporting(false);
    }
  };

  const handleSyncTests = async (problemId) => {
    setSyncingTests(true);
    try {
      const response = await syncTestsFromGithub(problemId);
      toast.success(response.message || 'Sync started');
      setTimeout(async () => {
        loadProblems();
        if (selectedProblem?.id === problemId) {
          const updated = await fetchProblem(problemId);
          setSelectedProblem(updated);
          await Promise.all([
            loadTests(problemId),
            loadSolutions(problemId)
          ]);
        }
      }, 2000);
    } catch (error) {
      toast.error('Failed to sync tests: ' + error.message);
    } finally {
      setSyncingTests(false);
    }
  };

  const handleFileUpload = async (problemId, file) => {
    if (!file) return;
    try {
      await uploadTests(problemId, file);
      toast.success('Tests uploaded successfully');
      if (selectedProblem?.id === problemId) {
        loadTests(problemId);
      }
    } catch (error) {
      toast.error('Failed to upload tests: ' + error.message);
    }
  };

  const loadTests = async (problemId) => {
    try {
      const data = await fetchTests(problemId);
      // Bug fix: backend returns { problem_id, test_count, tests: [{filename, size_bytes}] }
      // Normalize into a flat list with consistent shape for the UI
      const normalized = (data.tests || []).map((t) => ({
        id: t.filename,
        name: t.filename,
        size_bytes: t.size_bytes,
      }));
      setTests(normalized);
    } catch (error) {
      toast.error('Failed to load tests: ' + error.message);
    }
  };

  const loadSolutions = async (problemId) => {
    try {
      const data = await fetchSolutions(problemId);
      setSolutions(data || []);
    } catch (error) {
      toast.error('Failed to load solutions: ' + error.message);
    }
  };

  // Bug fix: viewProblem now fetches full problem details (list endpoint omits description_md,
  // source_url, tests_downloaded, etc.)
  const viewProblem = async (problem) => {
    try {
      const full = await fetchProblem(problem.id);
      setSelectedProblem(full);
      setPreviewTest(null);
      setPreviewContent({ input: null, output: null });
      setVerifyResults({});
      setVerifyingMap({});
      await Promise.all([
        loadTests(problem.id),
        loadSolutions(problem.id)
      ]);
    } catch (error) {
      toast.error('Failed to load problem details: ' + error.message);
    }
  };

  // Bug fix: test preview fetches file content on demand via API instead of expecting
  // inline content that the backend never returns
  const handlePreviewTest = async (test) => {
    if (previewTest?.id === test.id) {
      setPreviewTest(null);
      setPreviewContent({ input: null, output: null });
      return;
    }

    setPreviewTest(test);
    setPreviewContent({ input: null, output: null });

    // Derive stem from filename (e.g. "001.in" -> "001")
    const stem = test.name.replace(/\.(in|out)$/, '');
    const inFile = stem + '.in';
    const outFile = stem + '.out';

    setPreviewLoading(true);
    try {
      const [inputRes, outputRes] = await Promise.all([
        fetchTestFile(selectedProblem.id, inFile),
        fetchTestFile(selectedProblem.id, outFile),
      ]);
      setPreviewContent({ input: inputRes, output: outputRes });
    } catch (error) {
      toast.error('Failed to load test preview: ' + error.message);
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleVerifyExample = async (solutionPath) => {
    setVerifyingMap(prev => ({ ...prev, [solutionPath]: true }));
    // Initialize empty results so the progress bar shows up
    setVerifyResults(prev => ({
      ...prev,
      [solutionPath]: {
        solution_file: solutionPath,
        compile_success: true,
        test_results: [],
        tests_passed: 0,
        tests_total: selectedProblem.test_count || 0,
        all_passed: false
      }
    }));

    try {
      await verifyExampleSolutionStream(selectedProblem.id, solutionPath, (data) => {
        if (data.type === 'test_result') {
          setVerifyResults(prev => {
            const current = prev[solutionPath] || { test_results: [] };
            const newResults = [...current.test_results, data.result];
            return {
              ...prev,
              [solutionPath]: {
                ...current,
                test_results: newResults,
                tests_passed: newResults.filter(r => r.passed).length,
              }
            };
          });
        } else if (data.type === 'final') {
          setVerifyResults(prev => ({
            ...prev,
            [solutionPath]: data.result
          }));
        } else if (data.type === 'error') {
          toast.error('Verification error: ' + data.message);
        }
      });
    } catch (error) {
      toast.error('Verification failed: ' + error.message);
    } finally {
      setVerifyingMap(prev => ({ ...prev, [solutionPath]: false }));
    }
  };

  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Problem Manager</h1>
          <p className="text-gray-400 mt-1">Manage competitive programming problems</p>
        </div>
        <div className="flex space-x-2">
          <button
            onClick={() => {
              setShowImportForm(!showImportForm);
              setShowForm(false);
            }}
            className="btn-secondary flex items-center space-x-2"
          >
            {showImportForm ? <EyeOff className="h-4 w-4" /> : <Github className="h-4 w-4" />}
            <span>{showImportForm ? 'Cancel' : 'Import from GitHub'}</span>
          </button>
          <button
            onClick={() => {
              setShowForm(!showForm);
              setShowImportForm(false);
            }}
            className="btn-primary flex items-center space-x-2"
          >
            {showForm ? <EyeOff className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
            <span>{showForm ? 'Cancel' : 'New Problem'}</span>
          </button>
        </div>
      </div>

      {/* Import Form */}
      {showImportForm && (
        <div className="card border-blue-500/30">
          <h2 className="text-lg font-semibold mb-4 flex items-center">
            <Github className="h-5 w-5 mr-2 text-blue-400" />
            Import Problem from GitHub (IOI Format)
          </h2>
          <form onSubmit={handleImport} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">GitHub URL</label>
              <input
                type="url"
                value={importData.url}
                onChange={(e) => setImportData({ ...importData, url: e.target.value })}
                className="input"
                placeholder="https://github.com/austrian-olympiad-informatics/ioi-tasks/tree/main/ioi2023-soccer"
                required
              />
            </div>
            <div className="flex items-center space-x-2">
              <input
                type="checkbox"
                id="download_tests"
                checked={importData.download_tests}
                onChange={(e) => setImportData({ ...importData, download_tests: e.target.checked })}
                className="rounded border-gray-700 bg-gray-800 text-blue-500 focus:ring-blue-500"
              />
              <label htmlFor="download_tests" className="text-sm text-gray-300">
                Download tests immediately
              </label>
            </div>
            <p className="text-xs text-gray-400">
              The problem will be imported from the specified GitHub repository.
              Tests can be downloaded now or later from the problem details page.
            </p>
            <div className="flex space-x-3">
              <button type="submit" className="btn-primary" disabled={importing}>
                {importing ? 'Starting Import...' : 'Start Import'}
              </button>
              <button type="button" onClick={() => setShowImportForm(false)} className="btn-secondary">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Form */}
      {showForm && (
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">
            {editingProblem ? 'Edit Problem' : 'Create Problem'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Name</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="input"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Slug</label>
                <input
                  type="text"
                  value={formData.slug}
                  onChange={(e) => setFormData({ ...formData, slug: e.target.value })}
                  className="input"
                  placeholder="problem-slug"
                  required
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Description (Markdown)</label>
              {/* Bug fix: was formData.description, now formData.description_md */}
              <textarea
                value={formData.description_md}
                onChange={(e) => setFormData({ ...formData, description_md: e.target.value })}
                className="input-textarea"
                rows={6}
                placeholder="Problem description in Markdown..."
              />
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Time Limit (ms)</label>
                <input
                  type="number"
                  value={formData.time_limit_ms}
                  onChange={(e) => setFormData({ ...formData, time_limit_ms: parseInt(e.target.value) })}
                  className="input"
                  min="1"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Memory Limit (MB)</label>
                <input
                  type="number"
                  value={formData.memory_limit_mb}
                  onChange={(e) => setFormData({ ...formData, memory_limit_mb: parseInt(e.target.value) })}
                  className="input"
                  min="1"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Scoring Mode</label>
                {/* Bug fix: options now match backend enum (binary|partial|custom), was standard/icpc/ioi */}
                <select
                  value={formData.scoring_mode}
                  onChange={(e) => setFormData({ ...formData, scoring_mode: e.target.value })}
                  className="input"
                >
                  <option value="binary">Binary</option>
                  <option value="partial">Partial</option>
                  <option value="custom">Custom</option>
                </select>
              </div>
            </div>
            <div className="flex space-x-3">
              <button type="submit" className="btn-primary">
                {editingProblem ? 'Update' : 'Create'}
              </button>
              <button type="button" onClick={resetForm} className="btn-secondary">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Problem List */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Problems ({problems.length})</h2>
          {problems.length === 0 ? (
            <div className="card text-center py-8 text-gray-500">
              No problems yet. Create your first problem!
            </div>
          ) : (
            <div className="space-y-3">
              {problems.map((problem) => (
                <div
                  key={problem.id}
                  className={`card p-4 cursor-pointer transition-colors ${
                    selectedProblem?.id === problem.id ? 'border-blue-500' : ''
                  }`}
                  onClick={() => viewProblem(problem)}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="font-semibold">{problem.name}</h3>
                      {problem.short_description && (
                        <p className="text-xs text-blue-400 mt-1 line-clamp-2">{problem.short_description}</p>
                      )}
                      {problem.tags && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {JSON.parse(problem.tags).map((tag, idx) => (
                            <span key={idx} className="px-1.5 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px] border border-blue-500/20">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                      <p className="text-sm text-gray-400 mt-1">{problem.slug}</p>
                      <div className="flex items-center space-x-4 mt-2 text-xs text-gray-500">
                        <span>{problem.time_limit_ms}ms</span>
                        <span>{problem.memory_limit_mb}MB</span>
                        <span className="capitalize">{problem.scoring_mode}</span>
                      </div>
                    </div>
                    <div className="flex items-center space-x-1">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          // handleEdit needs the full problem; fetch it if we're editing from list
                          if (selectedProblem?.id === problem.id) {
                            handleEdit(selectedProblem);
                          } else {
                            fetchProblem(problem.id).then(handleEdit).catch((err) =>
                              toast.error('Failed to load problem: ' + err.message)
                            );
                          }
                        }}
                        className="p-2 text-gray-400 hover:bg-gray-700 rounded-lg"
                      >
                        <Edit2 className="h-4 w-4" />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(problem.id); }}
                        className="p-2 text-red-400 hover:bg-red-400/10 rounded-lg"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Problem Details */}
        {selectedProblem && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Problem Details</h2>
            </div>

            {/* Description */}
            <div className="card">
              <h3 className="font-medium mb-2">Description</h3>
              {selectedProblem.short_description && (
                <div className="mb-4 p-3 bg-blue-500/5 border border-blue-500/10 rounded-lg">
                  <p className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-1">Summary</p>
                  <p className="text-sm text-gray-300 italic">{selectedProblem.short_description}</p>
                  {selectedProblem.tags && (
                    <div className="flex flex-wrap gap-1 mt-3">
                      {JSON.parse(selectedProblem.tags).map((tag, idx) => (
                        <span key={idx} className="px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded text-[11px] border border-blue-400/30">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div className="prose prose-invert prose-sm max-w-none max-h-64 overflow-y-auto">
                {/* Bug fix: was selectedProblem.description (undefined); now description_md from full fetch */}
                <ReactMarkdown>{selectedProblem.description_md || 'No description'}</ReactMarkdown>
              </div>
            </div>

            {/* GitHub Info & Sync */}
            {selectedProblem.source_url && (
              <div className="card border-blue-500/20">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center space-x-2 text-blue-400">
                    <Github className="h-4 w-4" />
                    <span className="text-sm font-medium">Source: IOI GitHub</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    {selectedProblem.tests_downloaded ? (
                      <span className="flex items-center text-xs text-green-400 bg-green-400/10 px-2 py-1 rounded-full">
                        <CheckCircle2 className="h-3 w-3 mr-1" />
                        Tests Ready
                      </span>
                    ) : (
                      <span className="flex items-center text-xs text-yellow-400 bg-yellow-400/10 px-2 py-1 rounded-full">
                        <XCircle className="h-3 w-3 mr-1" />
                        Tests Missing
                      </span>
                    )}
                  </div>
                </div>

                <p className="text-xs text-gray-400 mb-4 truncate" title={selectedProblem.source_url}>
                  {selectedProblem.source_url}
                </p>

                {!selectedProblem.tests_downloaded && (
                  <button
                    onClick={() => handleSyncTests(selectedProblem.id)}
                    disabled={syncingTests}
                    className="w-full btn-primary py-2 text-sm flex items-center justify-center space-x-2"
                  >
                    <RefreshCw className={`h-4 w-4 ${syncingTests ? 'animate-spin' : ''}`} />
                    <span>{syncingTests ? 'Syncing...' : 'Sync Tests from GitHub'}</span>
                  </button>
                )}
              </div>
            )}

            {/* Solutions List */}
            {selectedProblem.tests_downloaded && (
              <div className="card">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-medium flex items-center">
                    <Code className="h-4 w-4 mr-2" />
                    Available Solutions
                  </h3>
                  {solutions.length === 0 && !Object.values(verifyingMap).some(Boolean) && (
                    <span className="text-xs text-gray-500">No solutions found in repository</span>
                  )}
                </div>
                
                {solutions.length > 0 && (
                  <div className="space-y-3">
                    {solutions.map((sol) => {
                      const result = verifyResults[sol.path];
                      const isVerifying = verifyingMap[sol.path];

                      return (
                        <div key={sol.path} className="space-y-2">
                          <div className="flex items-center justify-between p-2 bg-gray-950/50 rounded-lg border border-gray-800 hover:border-gray-700 transition-colors">
                            <div className="flex-1 min-w-0 mr-4">
                              <div className="text-sm font-medium truncate" title={sol.path}>{sol.name}</div>
                              <div className="flex items-center space-x-3 mt-1">
                                {sol.expected_score !== null && (
                                  <span className="text-[10px] text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded">
                                    Exp. Score: {sol.expected_score}
                                  </span>
                                )}
                                {sol.expected_verdict && (
                                  <span className="text-[10px] text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded">
                                    Exp. Verdict: {sol.expected_verdict}
                                  </span>
                                )}
                              </div>
                            </div>
                            <button
                              onClick={() => handleVerifyExample(sol.path)}
                              disabled={isVerifying}
                              className="btn-secondary text-xs py-1 px-3 flex items-center space-x-1"
                            >
                              {isVerifying ? (
                                <FlaskConical className="h-3 w-3 animate-pulse text-blue-400" />
                              ) : (
                                <Play className="h-3 w-3" />
                              )}
                              <span>Test</span>
                            </button>
                          </div>
                          
                          {/* Progress Bar */}
                          {result && result.compile_success && (
                            <div className="px-1">
                              <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-gray-800">
                                {result.test_results && result.test_results.map((t, i) => (
                                  <div
                                    key={i}
                                    className={`h-full border-r border-gray-900 last:border-0 ${
                                      t.passed ? 'bg-green-500' : 'bg-red-500'
                                    }`}
                                    style={{ width: `${100 / (result.tests_total || 1)}%` }}
                                    title={`Test ${t.test_index}: ${t.verdict}`}
                                  />
                                ))}
                                {isVerifying && result.test_results && result.test_results.length < (result.tests_total || 0) && (
                                  <div 
                                    className="h-full bg-blue-500/30 animate-pulse"
                                    style={{ width: `${100 - (result.test_results.length * 100 / (result.tests_total || 1))}%` }}
                                  />
                                )}
                              </div>
                              <div className="flex justify-between mt-1 px-0.5">
                                <span className={`text-[10px] font-medium ${result.all_passed ? 'text-green-400' : 'text-red-400'}`}>
                                  {result.tests_passed}/{result.tests_total || selectedProblem.test_count} passed
                                </span>
                                <span className="text-[10px] text-gray-500">
                                  {result.tests_total > 0 ? ((result.tests_passed / result.tests_total) * 100).toFixed(0) : 0}%
                                </span>
                              </div>
                            </div>
                          )}

                          {result && !result.compile_success && (
                            <div className="px-2 py-1 bg-red-900/20 border border-red-500/30 rounded text-[10px] text-red-400 font-mono">
                              Compilation Failed
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* Test Upload */}
            <div className="card">
              <h3 className="font-medium mb-3 flex items-center">
                <FileArchive className="h-4 w-4 mr-2" />
                Test Cases
              </h3>
              <div className="border-2 border-dashed border-gray-700 rounded-lg p-6 text-center">
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => handleFileUpload(selectedProblem.id, e.target.files[0])}
                  className="hidden"
                  id={`test-upload-${selectedProblem.id}`}
                />
                <label
                  htmlFor={`test-upload-${selectedProblem.id}`}
                  className="cursor-pointer flex flex-col items-center"
                >
                  <Upload className="h-8 w-8 text-gray-500 mb-2" />
                  <span className="text-sm text-gray-400">
                    Drop .zip file here or click to upload
                  </span>
                  <span className="text-xs text-gray-600 mt-1">
                    Contains .in and .out files
                  </span>
                </label>
              </div>

              {/* Test List */}
              {tests.length > 0 && (
                <div className="mt-4">
                  <p className="text-sm text-gray-400 mb-2">{tests.length} file(s)</p>
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {tests.map((test) => (
                      <button
                        key={test.id}
                        onClick={() => handlePreviewTest(test)}
                        className="w-full flex items-center justify-between p-2 bg-gray-800 hover:bg-gray-750 rounded text-left"
                      >
                        <div className="flex items-center space-x-2">
                          <FileCode className="h-4 w-4 text-gray-500" />
                          <span className="text-sm font-mono">{test.name}</span>
                        </div>
                        <div className="flex items-center space-x-2 text-xs text-gray-500">
                          <span>{formatFileSize(test.size_bytes)}</span>
                          {previewTest?.id === test.id ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Verify Example Result Detail (shown when clicking a test result) */}
            {Object.values(verifyResults).map(res => (
              <div key={res.solution_file} className={`card border ${res.all_passed ? 'border-green-500/30' : 'border-red-500/30'}`}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-medium flex items-center space-x-2">
                    <FlaskConical className="h-4 w-4" />
                    <span>Example Solution Verification</span>
                  </h3>
                  <button
                    onClick={() => setVerifyResults(prev => {
                      const next = { ...prev };
                      delete next[res.solution_file];
                      return next;
                    })}
                    className="text-gray-500 hover:text-gray-300 text-xs"
                  >
                    <XCircle className="h-4 w-4" />
                  </button>
                </div>

                <p className="text-xs text-gray-500 font-mono mb-3 truncate" title={res.solution_file}>
                  {res.solution_file}
                </p>

                {!res.compile_success ? (
                  <div>
                    <div className="flex items-center space-x-2 text-red-400 mb-2">
                      <XCircle className="h-4 w-4" />
                      <span className="text-sm font-medium">Compilation Failed</span>
                    </div>
                    <pre className="bg-gray-950 p-3 rounded text-xs font-mono text-red-300 overflow-x-auto max-h-40 whitespace-pre-wrap">
                      {res.compile_log}
                    </pre>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {/* Summary row */}
                    <div className="flex items-center flex-wrap gap-4">
                      <div className={`flex items-center space-x-1.5 text-sm font-medium ${res.all_passed ? 'text-green-400' : 'text-red-400'}`}>
                        {res.all_passed
                          ? <CheckCircle2 className="h-4 w-4" />
                          : <XCircle className="h-4 w-4" />}
                        <span>{res.tests_passed}/{res.tests_total} tests passed</span>
                      </div>

                      <div className="flex items-center space-x-1.5 text-sm font-bold text-blue-400 bg-blue-400/10 px-2 py-0.5 rounded">
                        <span>Score: {res.score !== undefined 
                          ? (typeof res.score === 'number' ? res.score.toFixed(2) : res.score)
                          : ((res.tests_passed / res.tests_total) * 100).toFixed(2)
                        }</span>
                        {solutions.find(s => s.path === res.solution_file)?.expected_score !== null && (
                          <span className="text-xs text-gray-500 font-normal ml-1">
                            (Expected: {solutions.find(s => s.path === res.solution_file)?.expected_score})
                          </span>
                        )}
                      </div>

                      <div className="flex items-center space-x-4">
                        {res.avg_time_ms != null && (
                          <div className="flex items-center space-x-1 text-xs text-gray-400">
                            <Clock className="h-3 w-3" />
                            <span>avg {res.avg_time_ms < 1 ? res.avg_time_ms.toFixed(2) : res.avg_time_ms.toFixed(1)}ms</span>
                          </div>
                        )}
                        {res.max_time_ms != null && (
                          <div className="flex items-center space-x-1 text-xs text-gray-400">
                            <Clock className="h-3 w-3" />
                            <span>max {res.max_time_ms < 1 ? res.max_time_ms.toFixed(2) : res.max_time_ms.toFixed(1)}ms</span>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Per-test breakdown */}
                    {res.test_results.length > 0 && (
                      <div className="max-h-48 overflow-y-auto space-y-1">
                        {[...res.test_results]
                          .sort((a, b) => (a.test_index || 0) - (b.test_index || 0))
                          .map((t) => (
                            <div
                              key={t.test_index}
                            className={`flex items-center justify-between px-2 py-1 rounded text-xs ${
                              t.passed ? 'bg-green-900/20 text-green-300' : 'bg-red-900/20 text-red-300'
                            }`}
                          >
                            <div className="flex items-center space-x-2">
                              {t.passed
                                ? <CheckCircle2 className="h-3 w-3 text-green-400 shrink-0" />
                                : <XCircle className="h-3 w-3 text-red-400 shrink-0" />}
                              <span className="font-mono">test {t.test_index}</span>
                              <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${
                                t.verdict === 'AC' ? 'bg-green-800/40' :
                                t.verdict === 'TLE' ? 'bg-yellow-800/40 text-yellow-300' :
                                'bg-red-800/40'
                              }`}>{t.verdict}</span>
                            </div>
                            <div className="flex items-center space-x-3 text-gray-400">
                              {t.time_ms != null && (
                                <span>{t.time_ms < 1 ? t.time_ms.toFixed(2) : t.time_ms.toFixed(1)}ms</span>
                              )}
                              {t.memory_kb != null && <span>{(t.memory_kb / 1024).toFixed(1)}MB</span>}
                              {t.error && <span className="text-red-400 truncate max-w-32" title={t.error}>{t.error}</span>}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Test Preview */}
            {/* Bug fix: content is now loaded on demand via fetchTestFile, not expected inline */}
            {previewTest && (
              <div className="card">
                <h3 className="font-medium mb-3">
                  Test Preview: {previewTest.name.replace(/\.(in|out)$/, '')}
                </h3>
                {previewLoading ? (
                  <div className="text-center py-4 text-gray-400 text-sm">Loading...</div>
                ) : (
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs text-gray-400 mb-1">Input:</p>
                      <pre className="bg-gray-950 p-3 rounded text-xs font-mono text-gray-300 overflow-x-auto max-h-48">
                        {previewContent.input ?? 'N/A'}
                      </pre>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 mb-1">Expected Output:</p>
                      <pre className="bg-gray-950 p-3 rounded text-xs font-mono text-gray-300 overflow-x-auto max-h-48">
                        {previewContent.output ?? 'N/A'}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default ProblemManager;
