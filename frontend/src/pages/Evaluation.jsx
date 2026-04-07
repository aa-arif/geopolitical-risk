import React, { useEffect, useState } from 'react';
import { getEvaluation, getTrackComparison } from '../api';
import CalibrationChart from '../components/CalibrationChart';

function ScoreCard({ label, value, format }) {
  let display;
  if (format === 'percent') {
    display = `${(value * 100).toFixed(1)}%`;
  } else if (format === 'brier') {
    display = value.toFixed(4);
  } else if (typeof value === 'number') {
    display = value.toLocaleString();
  } else {
    display = String(value ?? 'N/A');
  }

  return (
    <div className="score-card">
      <span className="score-label">{label}</span>
      <span className="score-value mono">{display}</span>
    </div>
  );
}

function TrackComparisonTable({ data }) {
  if (!data) return null;

  const rows = Array.isArray(data)
    ? data
    : data.tracks || data.comparison || [];

  if (rows.length === 0 && typeof data === 'object' && !Array.isArray(data)) {
    const entries = Object.entries(data);
    return (
      <div className="track-comparison">
        <h3 className="section-title">Track Comparison</h3>
        <table className="eval-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, val]) => (
              <tr key={key}>
                <td>{key.replace(/_/g, ' ')}</td>
                <td className="mono">
                  {typeof val === 'number' ? val.toFixed(4) : JSON.stringify(val)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div className="track-comparison">
      <h3 className="section-title">Track Comparison</h3>
      <table className="eval-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col.replace(/_/g, ' ')}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col} className="mono">
                  {typeof row[col] === 'number' ? row[col].toFixed(4) : String(row[col] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Evaluation() {
  const [evalData, setEvalData] = useState(null);
  const [trackData, setTrackData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([getEvaluation(), getTrackComparison()])
      .then(([ev, tr]) => {
        setEvalData(ev);
        setTrackData(tr);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="loading">Loading evaluation metrics...</div>;
  if (error) return <div className="error">Error: {error}</div>;

  const calibration = evalData?.calibration;

  return (
    <div className="evaluation">
      <div className="page-header">
        <h2>Model Evaluation</h2>
        <span className="subtitle mono">Prediction performance metrics</span>
      </div>

      <div className="scores-grid">
        <ScoreCard label="Resolved Predictions" value={evalData?.n_resolved} />
        <ScoreCard label="Brier Score (aggregate)" value={evalData?.brier_aggregate} format="brier" />
        <ScoreCard label="Base Rate" value={evalData?.base_rate} format="percent" />
      </div>

      <div className="eval-charts">
        <div className="detail-card">
          <h3 className="section-title">Calibration Curve</h3>
          {calibration ? (
            <CalibrationChart data={calibration} />
          ) : (
            <div className="no-data">No calibration data available</div>
          )}
        </div>
      </div>

      <div className="detail-card">
        <TrackComparisonTable data={trackData} />
      </div>
    </div>
  );
}
