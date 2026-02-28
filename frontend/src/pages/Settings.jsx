import React, { useState, useEffect } from 'react';
import { Save, RotateCcw } from 'lucide-react';
import toast from 'react-hot-toast';
import PromptEditor from '../components/PromptEditor';

const MODEL_SUGGESTIONS = [
  'anthropic/claude-3.5-haiku',
    'nvidia/nemotron-3-nano-30b-a3b:free',
    'openai/gpt-oss-120b:free',
    'qwen/qwen3.5-397b-a17b',
    'minimax/minimax-m2.5',
    'x-ai/grok-4.1-fast',
    'z-ai/glm-5:nitro',
    'stepfun/step-3.5-flash:free',
    'mistralai/mistral-nemo',
    'openai/gpt-5-nano',
    'moonshotai/kimi-k2.5:free',
    'qwen/qwen3-coder',
    'qwen/qwen3-next-80b-a3b-instruct:free',
    'cognitivecomputations/dolphin-mistral-24b-venice-edition:free',
    'google/gemini-3-flash-preview',
];

const DEFAULT_PROMPT_VARIABLES = [
  { name: 'problem_description', description: 'Full problem description' },
  { name: 'problem_name', description: 'Problem name' },
  { name: 'previous_round_summary', description: 'Summary of previous round' },
  { name: 'current_round', description: 'Current round number' },
  { name: 'total_rounds', description: 'Total number of rounds' },
];

const DEFAULT_SYSTEM_PROMPT = `You are an expert competitive programmer. Your task is to write efficient C++ code to solve the given problem.

Focus on:
- Correctness: Handle all edge cases
- Efficiency: Optimize for time and memory constraints
- Code quality: Clean, well-commented code

{problem_description}`;

const DEFAULT_MODEL = 'google/gemini-2.5-flash';

function Settings() {
  const [defaultModel, setDefaultModel] = useState(DEFAULT_MODEL);
  const [defaultPrompt, setDefaultPrompt] = useState(DEFAULT_SYSTEM_PROMPT);

  useEffect(() => {
    const savedModel = localStorage.getItem('defaultRunModel');
    const savedPrompt = localStorage.getItem('defaultRunPrompt');
    if (savedModel) setDefaultModel(savedModel);
    if (savedPrompt) setDefaultPrompt(savedPrompt);
  }, []);

  const handleSave = () => {
    localStorage.setItem('defaultRunModel', defaultModel);
    localStorage.setItem('defaultRunPrompt', defaultPrompt);
    toast.success('Settings saved successfully');
  };

  const handleReset = () => {
    if (window.confirm('Are you sure you want to reset to defaults?')) {
      setDefaultModel(DEFAULT_MODEL);
      setDefaultPrompt(DEFAULT_SYSTEM_PROMPT);
      localStorage.removeItem('defaultRunModel');
      localStorage.removeItem('defaultRunPrompt');
      toast.success('Settings reset to defaults');
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Settings</h1>
        <div className="flex space-x-3">
          <button
            onClick={handleReset}
            className="btn-secondary flex items-center space-x-2"
          >
            <RotateCcw className="h-4 w-4" />
            <span>Reset to Defaults</span>
          </button>
          <button
            onClick={handleSave}
            className="btn-primary flex items-center space-x-2"
          >
            <Save className="h-4 w-4" />
            <span>Save Settings</span>
          </button>
        </div>
      </div>

      <div className="card p-6 space-y-6 bg-gray-900 border border-gray-700 rounded-lg">
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">
            Default Run Model
          </label>
          <input
            type="text"
            list="model-suggestions"
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
            className="input w-full bg-gray-800 border-gray-700 text-white rounded-md p-2 focus:ring-2 focus:ring-blue-500 outline-none"
            placeholder="e.g. google/gemini-2.5-flash"
          />
          <datalist id="model-suggestions">
            {MODEL_SUGGESTIONS.map(s => <option key={s} value={s} />)}
          </datalist>
          <p className="mt-1 text-xs text-gray-500">
            This model will be used by default for new runs.
          </p>
        </div>

        <PromptEditor
          label="Default System Prompt"
          value={defaultPrompt}
          onChange={setDefaultPrompt}
          variables={DEFAULT_PROMPT_VARIABLES}
          description="The default system prompt for new runs."
        />
      </div>
    </div>
  );
}

export default Settings;
