import React from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

const COLORS = [
  '#3b82f6', // blue
  '#22c55e', // green
  '#f59e0b', // amber
  '#ef4444', // red
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#84cc16', // lime
];

function BenchmarkChart({ data, metric = 'score' }) {
  // Transform data for Recharts
  // Input: [{round: 1, model: 'gpt-4', value: 100}, ...]
  // Output: [{round: 1, 'gpt-4': 100, 'claude': 90, ...}, ...]
  
  const rounds = [...new Set(data.map(d => d.round))].sort((a, b) => a - b);
  const models = [...new Set(data.map(d => d.model))];
  
  const chartData = rounds.map(round => {
    const roundData = { round: `R${round}` };
    models.forEach(model => {
      const entry = data.find(d => d.round === round && d.model === model);
      roundData[model] = entry ? entry.value : null;
    });
    return roundData;
  });

  const getYAxisLabel = () => {
    switch (metric) {
      case 'score':
        return 'Score';
      case 'time':
        return 'Time (ms)';
      case 'memory':
        return 'Memory (KB)';
      default:
        return metric;
    }
  };

  const formatValue = (value) => {
    if (value === null || value === undefined) return '-';
    if (metric === 'score') return value.toFixed(2);
    if (metric === 'time') return `${value.toFixed(2)} ms`;
    if (metric === 'memory') return `${(value / 1024).toFixed(2)} MB`;
    return value;
  };

  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 shadow-lg">
          <p className="text-gray-300 font-medium mb-2">{label}</p>
          {payload.map((entry, index) => (
            entry.value !== null && (
              <div key={index} className="flex items-center space-x-2 text-sm">
                <div 
                  className="w-3 h-3 rounded-full" 
                  style={{ backgroundColor: entry.color }}
                />
                <span className="text-gray-400">{entry.name}:</span>
                <span className="text-gray-100 font-mono">
                  {formatValue(entry.value)}
                </span>
              </div>
            )
          ))}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="w-full h-80">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis 
            dataKey="round" 
            stroke="#9ca3af"
            tick={{ fill: '#9ca3af' }}
          />
          <YAxis 
            stroke="#9ca3af"
            tick={{ fill: '#9ca3af' }}
            label={{ 
              value: getYAxisLabel(), 
              angle: -90, 
              position: 'insideLeft',
              style: { fill: '#9ca3af' }
            }}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend 
            wrapperStyle={{ paddingTop: '20px' }}
          />
          {models.map((model, index) => (
            <Line
              key={model}
              type="monotone"
              dataKey={model}
              stroke={COLORS[index % COLORS.length]}
              strokeWidth={2}
              dot={{ fill: COLORS[index % COLORS.length], strokeWidth: 0, r: 4 }}
              activeDot={{ r: 6 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default BenchmarkChart;