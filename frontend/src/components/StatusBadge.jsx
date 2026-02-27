import React from 'react';

const statusConfig = {
  // Run statuses
  configured: {
    color: 'bg-gray-600',
    text: 'text-gray-100',
    label: 'Configured',
  },
  running: {
    color: 'bg-green-600',
    text: 'text-white',
    label: 'Running',
  },
  paused: {
    color: 'bg-yellow-600',
    text: 'text-white',
    label: 'Paused',
  },
  completed: {
    color: 'bg-blue-600',
    text: 'text-white',
    label: 'Completed',
  },
  error: {
    color: 'bg-red-600',
    text: 'text-white',
    label: 'Error',
  },
  // Round/Solution statuses
  pending: {
    color: 'bg-gray-600',
    text: 'text-gray-100',
    label: 'Pending',
  },
  generating: {
    color: 'bg-blue-600',
    text: 'text-white',
    label: 'Generating',
  },
  compiling: {
    color: 'bg-yellow-600',
    text: 'text-white',
    label: 'Compiling',
  },
  testing: {
    color: 'bg-orange-600',
    text: 'text-white',
    label: 'Testing',
  },
  judged: {
    color: 'bg-blue-600',
    text: 'text-white',
    label: 'Judged',
  },
  // Test verdicts
  AC: {
    color: 'bg-green-600',
    text: 'text-white',
    label: 'AC',
  },
  WA: {
    color: 'bg-red-600',
    text: 'text-white',
    label: 'WA',
  },
  TLE: {
    color: 'bg-yellow-600',
    text: 'text-white',
    label: 'TLE',
  },
  MLE: {
    color: 'bg-orange-600',
    text: 'text-white',
    label: 'MLE',
  },
  RE: {
    color: 'bg-gray-600',
    text: 'text-gray-100',
    label: 'RE',
  },
  CE: {
    color: 'bg-purple-600',
    text: 'text-white',
    label: 'CE',
  },
};

const sizeConfig = {
  sm: 'px-2 py-0.5 text-xs',
  md: 'px-3 py-1 text-sm',
  lg: 'px-4 py-2 text-base',
};

function StatusBadge({ status, size = 'md', customLabel = null }) {
  const config = statusConfig[status] || statusConfig.pending;
  const sizeClass = sizeConfig[size];
  
  return (
    <span
      className={`inline-flex items-center font-medium rounded-full ${config.color} ${config.text} ${sizeClass}`}
    >
      {customLabel || config.label}
    </span>
  );
}

export default StatusBadge;