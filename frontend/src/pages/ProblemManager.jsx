import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Edit2, Trash2, Upload, Eye, EyeOff, FileArchive, FileCode } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import toast from 'react-hot-toast';
import { fetchProblems, createProblem, updateProblem, deleteProblem, uploadTests, fetchTests } from '../api';

function ProblemManager() {
  const [problems, setProblems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingProblem, setEditingProblem] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [selectedProblem, setSelectedProblem] = useState(null);
  const [tests, setTests] = useState([]);
  const [previewTest, setPreviewTest] = useState(null);

  const [formData, setFormData] = useState({
    name: '',
    slug: '',
    description: '',
    time_limit_ms: 1000,
    memory_limit_mb: 256,
    scoring_mode: 'standard',
  });

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
      description: problem.description || '',
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
      description: '',
      time_limit_ms: 1000,
      memory_limit_mb: 256,
      scoring_mode: 'standard',
    });
    setShowForm(false);
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
      setTests(data);
    } catch (error) {
      toast.error('Failed to load tests: ' + error.message);
    }
  };

  const viewProblem = async (problem) => {
    setSelectedProblem(problem);
    await loadTests(problem.id);
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
        <button
          onClick={() => setShowForm(!showForm)}
          className="btn-primary flex items-center space-x-2"
        >
          {showForm ? <EyeOff className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
          <span>{showForm ? 'Cancel' : 'New Problem'}</span>
        </button>
      </div>

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
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
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
                <select
                  value={formData.scoring_mode}
                  onChange={(e) => setFormData({ ...formData, scoring_mode: e.target.value })}
                  className="input"
                >
                  <option value="standard">Standard</option>
                  <option value="icpc">ICPC</option>
                  <option value="ioi">IOI</option>
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
                        onClick={(e) => { e.stopPropagation(); handleEdit(problem); }}
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
            <h2 className="text-lg font-semibold">Problem Details</h2>
            
            {/* Description */}
            <div className="card">
              <h3 className="font-medium mb-2">Description</h3>
              <div className="prose prose-invert prose-sm max-w-none max-h-64 overflow-y-auto">
                <ReactMarkdown>{selectedProblem.description || 'No description'}</ReactMarkdown>
              </div>
            </div>

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
                  <p className="text-sm text-gray-400 mb-2">{tests.length} test(s)</p>
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {tests.map((test) => (
                      <button
                        key={test.id}
                        onClick={() => setPreviewTest(previewTest?.id === test.id ? null : test)}
                        className="w-full flex items-center justify-between p-2 bg-gray-800 hover:bg-gray-750 rounded text-left"
                      >
                        <div className="flex items-center space-x-2">
                          <FileCode className="h-4 w-4 text-gray-500" />
                          <span className="text-sm font-mono">{test.name}</span>
                        </div>
                        <div className="flex items-center space-x-2 text-xs text-gray-500">
                          <span>{formatFileSize(test.input_size)}</span>
                          <span>{formatFileSize(test.output_size)}</span>
                          {previewTest?.id === test.id ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Test Preview */}
            {previewTest && (
              <div className="card">
                <h3 className="font-medium mb-3">Test Preview: {previewTest.name}</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-xs text-gray-400 mb-1">Input:</p>
                    <pre className="bg-gray-950 p-3 rounded text-xs font-mono text-gray-300 overflow-x-auto max-h-48">
                      {previewTest.input_content || 'N/A'}
                    </pre>
                  </div>
                  <div>
                    <p className="text-xs text-gray-400 mb-1">Expected Output:</p>
                    <pre className="bg-gray-950 p-3 rounded text-xs font-mono text-gray-300 overflow-x-auto max-h-48">
                      {previewTest.output_content || 'N/A'}
                    </pre>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default ProblemManager;