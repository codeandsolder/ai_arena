import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Clock, DollarSign, Hash, Brain, MessageSquare } from 'lucide-react';
import toast from 'react-hot-toast';
import { fetchApiCall } from '../api';

function ApiCallLog() {
  const { id } = useParams();
  const navigate = useNavigate();
  
  const [apiCall, setApiCall] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('prompt');

  useEffect(() => {
    loadData();
  }, [id]);

  const loadData = async () => {
    try {
      const data = await fetchApiCall(id);
      setApiCall(data);
    } catch (error) {
      toast.error('Failed to load API call: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const formatTokens = (count) => {
    if (!count) return 'N/A';
    return count.toLocaleString();
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">Loading...</div>;
  }

  if (!apiCall) {
    return <div className="text-center py-12 text-gray-400">API call not found</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center space-x-4">
        <button
          onClick={() => navigate(-1)}
          className="p-2 hover:bg-gray-800 rounded-lg"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">API Call Details</h1>
          <div className="flex items-center space-x-4 mt-1 text-gray-400">
            <span>{apiCall.model}</span>
            <span className="bg-gray-800 px-2 py-0.5 rounded text-sm">{apiCall.purpose}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main content - Three panels */}
        <div className="lg:col-span-2 space-y-4">
          {/* Tab buttons */}
          <div className="flex space-x-2 border-b border-gray-700">
            <button
              onClick={() => setActiveTab('prompt')}
              className={`px-4 py-2 font-medium text-sm border-b-2 transition-colors ${
                activeTab === 'prompt'
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-gray-300'
              }`}
            >
              <div className="flex items-center space-x-2">
                <MessageSquare className="h-4 w-4" />
                <span>Prompt</span>
              </div>
            </button>
            <button
              onClick={() => setActiveTab('thinking')}
              className={`px-4 py-2 font-medium text-sm border-b-2 transition-colors ${
                activeTab === 'thinking'
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-gray-300'
              }`}
            >
              <div className="flex items-center space-x-2">
                <Brain className="h-4 w-4" />
                <span>Thinking</span>
              </div>
            </button>
            <button
              onClick={() => setActiveTab('response')}
              className={`px-4 py-2 font-medium text-sm border-b-2 transition-colors ${
                activeTab === 'response'
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-gray-300'
              }`}
            >
              <div className="flex items-center space-x-2">
                <MessageSquare className="h-4 w-4" />
                <span>Response</span>
              </div>
            </button>
          </div>

          {/* Tab content */}
          <div className="card min-h-[500px]">
            {activeTab === 'prompt' && (
              <div>
                <h3 className="text-sm font-medium text-gray-400 mb-3">Full Prompt</h3>
                <pre className="bg-gray-950 p-4 rounded-lg text-sm font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap">
                  {apiCall.prompt || 'No prompt available'}
                </pre>
              </div>
            )}
            
            {activeTab === 'thinking' && (
              <div>
                <h3 className="text-sm font-medium text-gray-400 mb-3">Thinking / Reasoning</h3>
                {apiCall.thinking ? (
                  <pre className="bg-gray-950 p-4 rounded-lg text-sm font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap">
                    {apiCall.thinking}
                  </pre>
                ) : (
                  <p className="text-gray-500 italic">No thinking/reasoning tokens available for this model</p>
                )}
              </div>
            )}
            
            {activeTab === 'response' && (
              <div>
                <h3 className="text-sm font-medium text-gray-400 mb-3">Full Response</h3>
                <pre className="bg-gray-950 p-4 rounded-lg text-sm font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap">
                  {apiCall.response || 'No response available'}
                </pre>
              </div>
            )}
          </div>
        </div>

        {/* Metadata sidebar */}
        <div className="space-y-4">
          <div className="card">
            <h3 className="text-sm font-medium text-gray-400 mb-4">Metadata</h3>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-gray-400">Model</span>
                <span className="font-mono text-sm">{apiCall.model}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-400">Purpose</span>
                <span className="text-sm">{apiCall.purpose}</span>
              </div>
              <div className="border-t border-gray-700 pt-4">
                <div className="flex items-center justify-between">
                  <span className="text-gray-400 flex items-center">
                    <Hash className="h-4 w-4 mr-1" />
                    Input Tokens
                  </span>
                  <span className="font-mono">{formatTokens(apiCall.input_tokens)}</span>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-400 flex items-center">
                  <Hash className="h-4 w-4 mr-1" />
                  Output Tokens
                </span>
                <span className="font-mono">{formatTokens(apiCall.output_tokens)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-400 flex items-center">
                  <Brain className="h-4 w-4 mr-1" />
                  Thinking Tokens
                </span>
                <span className="font-mono">{formatTokens(apiCall.thinking_tokens)}</span>
              </div>
              <div className="border-t border-gray-700 pt-4">
                <div className="flex items-center justify-between">
                  <span className="text-gray-400 flex items-center">
                    <DollarSign className="h-4 w-4 mr-1" />
                    Cost
                  </span>
                  <span className="font-mono text-green-400">
                    ${apiCall.cost?.toFixed(6) || '0.000000'}
                  </span>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-400 flex items-center">
                  <Clock className="h-4 w-4 mr-1" />
                  Latency
                </span>
                <span className="font-mono">{apiCall.latency_ms} ms</span>
              </div>
              <div className="border-t border-gray-700 pt-4">
                <div className="flex items-center justify-between">
                  <span className="text-gray-400">Timestamp</span>
                  <span className="text-sm">
                    {apiCall.created_at ? new Date(apiCall.created_at).toLocaleString() : '-'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Token usage visualization */}
          <div className="card">
            <h3 className="text-sm font-medium text-gray-400 mb-4">Token Distribution</h3>
            {apiCall.input_tokens || apiCall.output_tokens ? (
              <div className="space-y-3">
                {apiCall.input_tokens > 0 && (
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400">Input</span>
                      <span className="text-gray-300">{formatTokens(apiCall.input_tokens)}</span>
                    </div>
                    <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-blue-500"
                        style={{ 
                          width: `${(apiCall.input_tokens / (apiCall.input_tokens + apiCall.output_tokens)) * 100}%` 
                        }}
                      />
                    </div>
                  </div>
                )}
                {apiCall.output_tokens > 0 && (
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400">Output</span>
                      <span className="text-gray-300">{formatTokens(apiCall.output_tokens)}</span>
                    </div>
                    <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-green-500"
                        style={{ 
                          width: `${(apiCall.output_tokens / (apiCall.input_tokens + apiCall.output_tokens)) * 100}%` 
                        }}
                      />
                    </div>
                  </div>
                )}
                {apiCall.thinking_tokens > 0 && (
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400">Thinking</span>
                      <span className="text-gray-300">{formatTokens(apiCall.thinking_tokens)}</span>
                    </div>
                    <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-purple-500"
                        style={{ 
                          width: `${(apiCall.thinking_tokens / (apiCall.input_tokens + apiCall.output_tokens + apiCall.thinking_tokens)) * 100}%` 
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-gray-500 text-sm">No token data available</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default ApiCallLog;