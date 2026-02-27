import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Edit2, Trash2, Upload, Eye, EyeOff, FileArchive, FileCode, Download, Github, RefreshCw, CheckCircle2, XCircle, FlaskConical, Clock, MemoryStick } from 'lucide-react';
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
  fetchTestFile,
  importProblems,
  importFromGithub,
  syncTestsFromGithub,
  verifyExampleSolution,
} from '../api';

function ProblemManager() {
  const [problems, setProblems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingProblem, setEditingProblem] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [showImportForm, setShowImportForm] = useState(false);
  const [selectedProblem, setSelectedProblem] = useState(null);
  const [tests, setTests] = useState([]);
  const [previewTest, setPreviewTest] = useState(null);
  const [previewContent, setPreviewContent] = useState({ input: null, output: null });
  const [previewLoading, setPreviewLoading] = useState(false);
  const [verifyResult, setVerifyResult] = useState(null);
  const [verifying, setVerifying] = useState(false);

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
          loadTests(problemId);
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

  // Bug fix: viewProblem now fetches full problem details (list endpoint omits description_md,
  // source_url, tests_downloaded, etc.)
  const viewProblem = async (problem) => {
    try {
      const full = await fetchProblem(problem.id);
      setSelectedProblem(full);
      setPreviewTest(null);
      setPreviewContent({ input: null, output: null });
      setVerifyResult(null);
      await loadTests(problem.id);
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

  const handleVerifyExample = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const result = await verifyExampleSolution(selectedProblem.id);
      setVerifyResult(result);
    } catch (error) {
      toast.error('Verification failed: ' + error.message);
    } finally {
      setVerifying(false);
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
                      <p className="text-sm text-gray-400">{problem.slug}</p>
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
              {selectedProblem.tests_downloaded && (
                <button
                  onClick={handleVerifyExample}
                  disabled={verifying}
                  className="btn-secondary flex items-center space-x-2 text-sm py-1.5"
                >
                  <FlaskConical className={`h-4 w-4 ${verifying ? 'animate-pulse text-blue-400' : ''}`} />
                  <span>{verifying ? 'Verifying...' : 'Verify Example Solution'}</span>
                </button>
              )}
            </div>

            {/* Description */}
            <div className="card">
              <h3 className="font-medium mb-2">Description</h3>
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

            {/* Verify Example Result */}
            {verifyResult && (
              <div className={`card border ${verifyResult.all_passed ? 'border-green-500/30' : 'border-red-500/30'}`}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-medium flex items-center space-x-2">
                    <FlaskConical className="h-4 w-4" />
                    <span>Example Solution Verification</span>
                  </h3>
                  <button
                    onClick={() => setVerifyResult(null)}
                    className="text-gray-500 hover:text-gray-300 text-xs"
                  >
                    <XCircle className="h-4 w-4" />
                  </button>
                </div>

                <p className="text-xs text-gray-500 font-mono mb-3 truncate" title={verifyResult.solution_file}>
                  {verifyResult.solution_file}
                </p>

                {!verifyResult.compile_success ? (
                  <div>
                    <div className="flex items-center space-x-2 text-red-400 mb-2">
                      <XCircle className="h-4 w-4" />
                      <span className="text-sm font-medium">Compilation Failed</span>
                    </div>
                    <pre className="bg-gray-950 p-3 rounded text-xs font-mono text-red-300 overflow-x-auto max-h-40 whitespace-pre-wrap">
                      {verifyResult.compile_log}
                    </pre>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {/* Summary row */}
                    <div className="flex items-center space-x-4">
                      <div className={`flex items-center space-x-1.5 text-sm font-medium ${verifyResult.all_passed ? 'text-green-400' : 'text-red-400'}`}>
                        {verifyResult.all_passed
                          ? <CheckCircle2 className="h-4 w-4" />
                          : <XCircle className="h-4 w-4" />}
                        <span>{verifyResult.tests_passed}/{verifyResult.tests_total} tests passed</span>
                      </div>
                      {verifyResult.avg_time_ms != null && (
                        <div className="flex items-center space-x-1 text-xs text-gray-400">
                          <Clock className="h-3 w-3" />
                          <span>avg {verifyResult.avg_time_ms.toFixed(1)}ms</span>
                        </div>
                      )}
                      {verifyResult.max_time_ms != null && (
                        <div className="flex items-center space-x-1 text-xs text-gray-400">
                          <Clock className="h-3 w-3" />
                          <span>max {verifyResult.max_time_ms.toFixed(1)}ms</span>
                        </div>
                      )}
                    </div>

                    {/* Per-test breakdown */}
                    {verifyResult.test_results.length > 0 && (
                      <div className="max-h-48 overflow-y-auto space-y-1">
                        {verifyResult.test_results.map((t) => (
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
                              {t.time_ms != null && <span>{t.time_ms.toFixed(1)}ms</span>}
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
            )}

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