import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, Trash2, Save, Play, ChevronDown, ChevronUp } from 'lucide-react';
import toast from 'react-hot-toast';
import { fetchProblems, fetchRun, createRun, updateRun, startRun } from '../api';
import PromptEditor from '../components/PromptEditor';

const MODEL_SUGGESTIONS = [
  'anthropic/claude-sonnet-4-20250514',
  'anthropic/claude-opus-4',
  'google/gemini-2.5-pro-preview-03-25',
  'openai/gpt-4o',
  'openai/gpt-4o-mini',
  'openai/o3-mini',
  'deepseek/deepseek-chat',
  'x-ai/grok-3-beta',
];

const DEFAULT_MODEL_CONFIG = {
  slug: '',
  display_name: '',
  max_tokens: 16384,
  temperature: 0.3,
  thinking_budget: null,
};

const DEFAULT_PROMPT_VARIABLES = [
  { name: 'problem_description', description: 'Full problem description' },
  { name: 'problem_name', description: 'Problem name' },
  { name: 'previous_round_summary', description: 'Summary of previous round' },
  { name: 'current_round', description: 'Current round number' },
  { name: 'total_rounds', description: 'Total number of rounds' },
];

const DEFAULT_PROMPTS = {
  system: `You are an expert competitive programmer. Your task is to write efficient C++ code to solve the given problem.

Focus on:
- Correctness: Handle all edge cases
- Efficiency: Optimize for time and memory constraints
- Code quality: Clean, well-commented code

{problem_description}`,
  round_1_user: `Please solve this problem:

{problem_description}

Write the complete C++ solution.`,
  round_n_user: `Based on the previous round, here's what happened:

{previous_round_summary}

Please improve the solution. Consider:
- Optimizing the algorithm if it was too slow
- Fixing any bugs that caused wrong answers
- Reducing memory usage if needed

Write the complete improved C++ solution.`,
  judge_system: `You are a code judge. Evaluate the C++ solution for correctness, efficiency, and code quality.`,
  judge_user: `Problem:
{problem_description}

Solution:
{solution_code}

Please evaluate this solution.`,
  error_retry_user: `The solution had errors. Error message:
{error_message}

Please fix the issues and provide a corrected solution.`,
  response_format: `Return your response in this JSON format:
{
  "code": "complete C++ code here",
  "explanation": "brief explanation of your approach"
}`,
};

function RunConfig() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEditing = Boolean(id);

  const [problems, setProblems] = useState([]);
  const [loading, setLoading] = useState(isEditing);
  const [saving, setSaving] = useState(false);
  
  // Form sections expanded state
  const [expandedSections, setExpandedSections] = useState({
    basic: true,
    models: true,
    judge: true,
    prompts: true,
    scoring: true,
    execution: true,
  });

  // Form data
  const [formData, setFormData] = useState({
    name: '',
    problem_id: '',
    models: [{ ...DEFAULT_MODEL_CONFIG, slug: localStorage.getItem('defaultRunModel') || '' }],
    judge_model: { ...DEFAULT_MODEL_CONFIG },
    prompts: { ...DEFAULT_PROMPTS, system: localStorage.getItem('defaultRunPrompt') || DEFAULT_PROMPTS.system },
    scoring_weights: {
      correctness_weight: 0.5,
      speed_weight: 0.3,
      memory_weight: 0.2,
    },
    time_bonus_multiplier: 1.0,
    penalty_wrong_answer: 0,
    execution_config: {
      allow_error_retry: true,
      max_error_retries: 2,
      parallel_models: true,
      benchmark_runs: 3,
      warmup_runs: 1,
    },
  });

  useEffect(() => {
    loadProblems();
    if (isEditing) {
      loadRun();
    }
  }, [id]);

  const loadProblems = async () => {
    try {
      const data = await fetchProblems();
      setProblems(data);
    } catch (error) {
      toast.error('Failed to load problems: ' + error.message);
    }
  };

  const loadRun = async () => {
    try {
      const run = await fetchRun(id);
      setFormData({
        name: run.name || '',
        problem_id: run.problem_id || '',
        models: run.models?.length ? run.models : [{ ...DEFAULT_MODEL_CONFIG }],
        judge_model: run.judge_model || { ...DEFAULT_MODEL_CONFIG },
        prompts: { ...DEFAULT_PROMPTS, ...run.prompts },
        scoring_weights: run.scoring_weights || {
          correctness_weight: 0.5,
          speed_weight: 0.3,
          memory_weight: 0.2,
        },
        time_bonus_multiplier: run.time_bonus_multiplier || 1.0,
        penalty_wrong_answer: run.penalty_wrong_answer || 0,
        execution_config: run.execution_config || {
          allow_error_retry: true,
          max_error_retries: 2,
          parallel_models: true,
          benchmark_runs: 3,
          warmup_runs: 1,
        },
      });
    } catch (error) {
      toast.error('Failed to load run: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const toggleSection = (section) => {
    setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }));
  };

  const addModel = () => {
    setFormData(prev => ({
      ...prev,
      models: [...prev.models, { ...DEFAULT_MODEL_CONFIG, slug: localStorage.getItem('defaultRunModel') || '' }],
    }));
  };

  const removeModel = (index) => {
    setFormData(prev => ({
      ...prev,
      models: prev.models.filter((_, i) => i !== index),
    }));
  };

  const updateModel = (index, field, value) => {
    setFormData(prev => ({
      ...prev,
      models: prev.models.map((m, i) => 
        i === index ? { ...m, [field]: value } : m
      ),
    }));
  };

  const updateJudgeModel = (field, value) => {
    setFormData(prev => ({
      ...prev,
      judge_model: { ...prev.judge_model, [field]: value },
    }));
  };

  const updatePrompt = (key, value) => {
    setFormData(prev => ({
      ...prev,
      prompts: { ...prev.prompts, [key]: value },
    }));
  };

  const handleSubmit = async (e, startAfterSave = false) => {
    e.preventDefault();
    setSaving(true);

    try {
      const payload = {
        ...formData,
        models: formData.models.filter(m => m.slug.trim()),
      };

      let runId;
      if (isEditing) {
        await updateRun(id, payload);
        runId = id;
        toast.success('Run updated successfully');
      } else {
        const result = await createRun(payload);
        runId = result.id;
        toast.success('Run created successfully');
      }

      if (startAfterSave) {
        const rounds = prompt('Number of rounds to run:', '10');
        if (rounds) {
          await startRun(runId, parseInt(rounds));
          toast.success('Run started');
        }
      }

      navigate(`/runs/${runId}`);
    } catch (error) {
      toast.error('Failed to save run: ' + error.message);
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">Loading...</div>;
  }

  const SectionHeader = ({ title, section, icon: Icon }) => (
    <button
      type="button"
      onClick={() => toggleSection(section)}
      className="w-full flex items-center justify-between p-4 bg-gray-800 hover:bg-gray-750 rounded-t-lg border border-gray-700"
    >
      <div className="flex items-center space-x-2">
        {Icon && <Icon className="h-5 w-5 text-blue-400" />}
        <h3 className="text-lg font-semibold">{title}</h3>
      </div>
      {expandedSections[section] ? (
        <ChevronUp className="h-5 w-5 text-gray-400" />
      ) : (
        <ChevronDown className="h-5 w-5 text-gray-400" />
      )}
    </button>
  );

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          {isEditing ? 'Edit Run' : 'Create New Run'}
        </h1>
        <div className="flex space-x-3">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="btn-secondary"
          >
            Cancel
          </button>
          <button
            onClick={(e) => handleSubmit(e, false)}
            disabled={saving}
            className="btn-primary flex items-center space-x-2"
          >
            <Save className="h-4 w-4" />
            <span>{saving ? 'Saving...' : 'Save'}</span>
          </button>
          {!isEditing && (
            <button
              onClick={(e) => handleSubmit(e, true)}
              disabled={saving}
              className="btn-success flex items-center space-x-2"
            >
              <Play className="h-4 w-4" />
              <span>Save & Start</span>
            </button>
          )}
        </div>
      </div>

      <form onSubmit={(e) => handleSubmit(e)} className="space-y-6">
        {/* Basic Settings */}
        <div className="border border-gray-700 rounded-lg overflow-hidden">
          <SectionHeader title="Basic Settings" section="basic" />
          {expandedSections.basic && (
            <div className="p-4 bg-gray-900 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">
                  Run Name
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  className="input"
                  placeholder="My Optimization Run"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">
                  Problem
                </label>
                <select
                  value={formData.problem_id}
                  onChange={(e) => setFormData(prev => ({ ...prev, problem_id: e.target.value }))}
                  className="input"
                  required
                >
                  <option value="">Select a problem...</option>
                  {problems.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>

        {/* Models */}
        <div className="border border-gray-700 rounded-lg overflow-hidden">
          <SectionHeader title="Models" section="models" />
          {expandedSections.models && (
            <div className="p-4 bg-gray-900 space-y-4">
              {formData.models.map((model, index) => (
                <div key={index} className="card p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="font-medium">Model {index + 1}</h4>
                    {formData.models.length > 1 && (
                      <button
                        type="button"
                        onClick={() => removeModel(index)}
                        className="p-1 text-red-400 hover:bg-red-400/10 rounded"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-gray-400">Model Slug</label>
                      <input
                        type="text"
                        list="model-suggestions"
                        value={model.slug}
                        onChange={(e) => updateModel(index, 'slug', e.target.value)}
                        className="input text-sm"
                        placeholder="google/gemini-3-flash-preview"
                        required
                      />
                      <datalist id="model-suggestions">
                        {MODEL_SUGGESTIONS.map(s => <option key={s} value={s} />)}
                      </datalist>
                    </div>
                    <div>
                      <label className="text-xs text-gray-400">Display Name</label>
                      <input
                        type="text"
                        value={model.display_name}
                        onChange={(e) => updateModel(index, 'display_name', e.target.value)}
                        className="input text-sm"
                        placeholder="Claude Sonnet 4"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400">Max Tokens</label>
                      <input
                        type="number"
                        value={model.max_tokens}
                        onChange={(e) => updateModel(index, 'max_tokens', parseInt(e.target.value))}
                        className="input text-sm"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400">Temperature</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={model.temperature}
                        onChange={(e) => updateModel(index, 'temperature', parseFloat(e.target.value))}
                        className="input text-sm"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-gray-400">Thinking Budget (optional)</label>
                    <input
                      type="number"
                      value={model.thinking_budget || ''}
                      onChange={(e) => updateModel(index, 'thinking_budget', e.target.value ? parseInt(e.target.value) : null)}
                      className="input text-sm"
                      placeholder="Leave empty for default"
                    />
                  </div>
                </div>
              ))}
              <button
                type="button"
                onClick={addModel}
                className="btn-secondary w-full flex items-center justify-center space-x-2"
              >
                <Plus className="h-4 w-4" />
                <span>Add Model</span>
              </button>
            </div>
          )}
        </div>

        {/* Judge Model */}
        <div className="border border-gray-700 rounded-lg overflow-hidden">
          <SectionHeader title="Judge Model" section="judge" />
          {expandedSections.judge && (
            <div className="p-4 bg-gray-900 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-400">Model Slug</label>
                  <input
                    type="text"
                    value={formData.judge_model.slug}
                    onChange={(e) => updateJudgeModel('slug', e.target.value)}
                    className="input text-sm"
                    placeholder="google/gemini-3-flash-preview"
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-400">Display Name</label>
                  <input
                    type="text"
                    value={formData.judge_model.display_name}
                    onChange={(e) => updateJudgeModel('display_name', e.target.value)}
                    className="input text-sm"
                    placeholder="Claude Judge"
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-400">Max Tokens</label>
                  <input
                    type="number"
                    value={formData.judge_model.max_tokens}
                    onChange={(e) => updateJudgeModel('max_tokens', parseInt(e.target.value))}
                    className="input text-sm"
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-400">Temperature</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="1"
                    value={formData.judge_model.temperature}
                    onChange={(e) => updateJudgeModel('temperature', parseFloat(e.target.value))}
                    className="input text-sm"
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Prompts */}
        <div className="border border-gray-700 rounded-lg overflow-hidden">
          <SectionHeader title="Prompts" section="prompts" />
          {expandedSections.prompts && (
            <div className="p-4 bg-gray-900 space-y-4">
              <PromptEditor
                label="System Prompt"
                value={formData.prompts.system}
                onChange={(v) => updatePrompt('system', v)}
                variables={DEFAULT_PROMPT_VARIABLES}
                description="The main system prompt for code generation"
              />
              <PromptEditor
                label="Round 1 User Prompt"
                value={formData.prompts.round_1_user}
                onChange={(v) => updatePrompt('round_1_user', v)}
                variables={DEFAULT_PROMPT_VARIABLES}
                description="Prompt for the first round"
              />
              <PromptEditor
                label="Round N User Prompt"
                value={formData.prompts.round_n_user}
                onChange={(v) => updatePrompt('round_n_user', v)}
                variables={DEFAULT_PROMPT_VARIABLES}
                description="Prompt for subsequent rounds with previous context"
              />
              <PromptEditor
                label="Judge System Prompt"
                value={formData.prompts.judge_system}
                onChange={(v) => updatePrompt('judge_system', v)}
                variables={DEFAULT_PROMPT_VARIABLES}
                description="System prompt for the judge model"
              />
              <PromptEditor
                label="Judge User Prompt"
                value={formData.prompts.judge_user}
                onChange={(v) => updatePrompt('judge_user', v)}
                variables={DEFAULT_PROMPT_VARIABLES}
                description="Prompt for judge evaluation"
              />
              <PromptEditor
                label="Error Retry User Prompt"
                value={formData.prompts.error_retry_user}
                onChange={(v) => updatePrompt('error_retry_user', v)}
                variables={[...DEFAULT_PROMPT_VARIABLES, { name: 'error_message', description: 'Error message from compilation/execution' }]}
                description="Prompt for retrying after errors"
              />
              <PromptEditor
                label="Response Format"
                value={formData.prompts.response_format}
                onChange={(v) => updatePrompt('response_format', v)}
                variables={[]}
                description="Expected response format template"
              />
            </div>
          )}
        </div>

        {/* Scoring */}
        <div className="border border-gray-700 rounded-lg overflow-hidden">
          <SectionHeader title="Scoring" section="scoring" />
          {expandedSections.scoring && (
            <div className="p-4 bg-gray-900 space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Correctness Weight</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="1"
                    value={formData.scoring_weights.correctness_weight}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      scoring_weights: { ...prev.scoring_weights, correctness_weight: parseFloat(e.target.value) }
                    }))}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Speed Weight</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="1"
                    value={formData.scoring_weights.speed_weight}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      scoring_weights: { ...prev.scoring_weights, speed_weight: parseFloat(e.target.value) }
                    }))}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Memory Weight</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="1"
                    value={formData.scoring_weights.memory_weight}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      scoring_weights: { ...prev.scoring_weights, memory_weight: parseFloat(e.target.value) }
                    }))}
                    className="input"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Time Bonus Multiplier</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={formData.time_bonus_multiplier}
                    onChange={(e) => setFormData(prev => ({ ...prev, time_bonus_multiplier: parseFloat(e.target.value) }))}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Wrong Answer Penalty</label>
                  <input
                    type="number"
                    step="1"
                    min="0"
                    value={formData.penalty_wrong_answer}
                    onChange={(e) => setFormData(prev => ({ ...prev, penalty_wrong_answer: parseInt(e.target.value) }))}
                    className="input"
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Execution Options */}
        <div className="border border-gray-700 rounded-lg overflow-hidden">
          <SectionHeader title="Execution Options" section="execution" />
          {expandedSections.execution && (
            <div className="p-4 bg-gray-900 space-y-4">
              <div className="flex items-center space-x-4">
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={formData.execution_config.allow_error_retry}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      execution_config: { ...prev.execution_config, allow_error_retry: e.target.checked }
                    }))}
                    className="rounded border-gray-600 bg-gray-800 text-blue-500"
                  />
                  <span className="text-sm">Allow Error Retry</span>
                </label>
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={formData.execution_config.parallel_models}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      execution_config: { ...prev.execution_config, parallel_models: e.target.checked }
                    }))}
                    className="rounded border-gray-600 bg-gray-800 text-blue-500"
                  />
                  <span className="text-sm">Parallel Models</span>
                </label>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Max Error Retries</label>
                  <input
                    type="number"
                    min="0"
                    value={formData.execution_config.max_error_retries}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      execution_config: { ...prev.execution_config, max_error_retries: parseInt(e.target.value) }
                    }))}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Benchmark Runs</label>
                  <input
                    type="number"
                    min="1"
                    value={formData.execution_config.benchmark_runs}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      execution_config: { ...prev.execution_config, benchmark_runs: parseInt(e.target.value) }
                    }))}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Warmup Runs</label>
                  <input
                    type="number"
                    min="0"
                    value={formData.execution_config.warmup_runs}
                    onChange={(e) => setFormData(prev => ({
                      ...prev,
                      execution_config: { ...prev.execution_config, warmup_runs: parseInt(e.target.value) }
                    }))}
                    className="input"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </form>
    </div>
  );
}

export default RunConfig;