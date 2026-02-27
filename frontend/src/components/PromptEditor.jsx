import React, { useState, useCallback } from 'react';
import { HelpCircle, Info } from 'lucide-react';

function PromptEditor({ value, onChange, variables = [], label, description }) {
  const [showHelp, setShowHelp] = useState(false);
  const [focusedVar, setFocusedVar] = useState(null);

  // Highlight variables in the text
  const renderHighlightedText = useCallback((text) => {
    if (!text) return '';
    
    // Escape HTML
    let html = text
      .replace(/&/g, '&')
      .replace(/</g, '<')
      .replace(/>/g, '>');
    
    // Highlight variables
    variables.forEach(variable => {
      const regex = new RegExp(`\\{${variable.name}\\}`, 'g');
      html = html.replace(regex, `<mark class="bg-blue-500/30 text-blue-300 px-1 rounded">{${variable.name}}</mark>`);
    });
    
    return html;
  }, [variables]);

  const handleInput = (e) => {
    onChange(e.target.value);
  };

  const insertVariable = (varName) => {
    const textarea = document.getElementById(`prompt-${label}`);
    if (!textarea) return;
    
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const newValue = value.substring(0, start) + `{${varName}}` + value.substring(end);
    onChange(newValue);
    
    // Focus back and set cursor position
    setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(start + varName.length + 2, start + varName.length + 2);
    }, 0);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <label className="text-sm font-medium text-gray-300">
            {label}
          </label>
          {description && (
            <div className="group relative">
              <Info className="h-4 w-4 text-gray-500 cursor-help" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-2 bg-gray-800 text-xs text-gray-300 rounded-lg shadow-lg border border-gray-700 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                {description}
              </div>
            </div>
          )}
        </div>
        {variables.length > 0 && (
          <button
            type="button"
            onClick={() => setShowHelp(!showHelp)}
            className="flex items-center space-x-1 text-xs text-blue-400 hover:text-blue-300"
          >
            <HelpCircle className="h-3 w-3" />
            <span>Variables</span>
          </button>
        )}
      </div>

      {/* Variable help panel */}
      {showHelp && variables.length > 0 && (
        <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-3">
          <p className="text-xs text-gray-400 mb-2">Click to insert variable:</p>
          <div className="flex flex-wrap gap-2">
            {variables.map((variable) => (
              <button
                key={variable.name}
                type="button"
                onClick={() => insertVariable(variable.name)}
                onMouseEnter={() => setFocusedVar(variable)}
                onMouseLeave={() => setFocusedVar(null)}
                className="text-xs bg-blue-500/20 text-blue-300 px-2 py-1 rounded hover:bg-blue-500/30 transition-colors"
              >
                {`{${variable.name}}`}
              </button>
            ))}
          </div>
          {focusedVar && (
            <p className="mt-2 text-xs text-gray-400">
              <span className="font-mono text-blue-300">{`{${focusedVar.name}}`}</span>
              {' - '}{focusedVar.description}
            </p>
          )}
        </div>
      )}

      {/* Textarea */}
      <textarea
        id={`prompt-${label}`}
        value={value}
        onChange={handleInput}
        rows={8}
        className="input-textarea font-mono"
        placeholder="Enter prompt template..."
      />

      {/* Variable reference */}
      <div className="text-xs text-gray-500">
        Available variables: {variables.map(v => `{${v.name}}`).join(', ')}
      </div>
    </div>
  );
}

export default PromptEditor;