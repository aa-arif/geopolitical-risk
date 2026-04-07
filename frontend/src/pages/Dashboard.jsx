import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getCountries } from '../api';

const RISK_ORDER = { CRITICAL: 0, HIGH: 1, ELEVATED: 2, LOW: 3 };
const RISK_COLORS = {
  CRITICAL: '#ff3b3b',
  HIGH: '#ff8c00',
  ELEVATED: '#ffd600',
  LOW: '#00e676',
};

function ProbabilityBar({ value }) {
  const pct = Math.round(value * 100);
  let color;
  if (pct >= 70) color = '#ff3b3b';
  else if (pct >= 45) color = '#ff8c00';
  else if (pct >= 20) color = '#ffd600';
  else color = '#00e676';

  return (
    <div className="prob-bar-container">
      <div className="prob-bar-track">
        <div className="prob-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="prob-bar-label mono">{pct}%</span>
    </div>
  );
}

function ConfidenceIndicator({ value }) {
  const bars = 5;
  const filled = Math.round(value * bars);
  return (
    <div className="confidence-indicator" title={`Confidence: ${Math.round(value * 100)}%`}>
      {Array.from({ length: bars }, (_, i) => (
        <div
          key={i}
          className={`confidence-bar ${i < filled ? 'filled' : ''}`}
        />
      ))}
    </div>
  );
}

function RiskCard({ country }) {
  const riskColor = RISK_COLORS[country.risk_level] || '#888';
  return (
    <Link to={`/country/${country.iso3}`} className="risk-card">
      <div className="risk-card-header">
        <h3 className="risk-card-name">{country.name}</h3>
        <span className="risk-badge" style={{ background: riskColor }}>
          {country.risk_level}
        </span>
      </div>

      <div className="risk-card-body">
        <div className="risk-card-row">
          <span className="label">Fused Probability</span>
          <ProbabilityBar value={country.current_probability} />
        </div>

        <div className="risk-card-tracks">
          <div className="track-item">
            <span className="label">Track A (structural)</span>
            <span className="mono track-val">{Math.round(country.track_a * 100)}%</span>
          </div>
          <div className="track-item">
            <span className="label">Track B (LLM)</span>
            <span className="mono track-val">{Math.round(country.track_b * 100)}%</span>
          </div>
        </div>

        <div className="risk-card-footer">
          <div className="confidence-row">
            <span className="label">Confidence</span>
            <ConfidenceIndicator value={country.confidence} />
          </div>
          <span className="prediction-date mono">
            {country.prediction_date}
          </span>
        </div>
      </div>
    </Link>
  );
}

export default function Dashboard() {
  const [countries, setCountries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    getCountries()
      .then((data) => {
        const sorted = [...data.countries].sort(
          (a, b) => (RISK_ORDER[a.risk_level] ?? 99) - (RISK_ORDER[b.risk_level] ?? 99)
        );
        setCountries(sorted);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="loading">Loading intelligence data...</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <div className="dashboard">
      <div className="page-header">
        <h2>Risk Overview</h2>
        <span className="subtitle mono">{countries.length} countries monitored</span>
      </div>
      <div className="risk-grid">
        {countries.map((c) => (
          <RiskCard key={c.iso3} country={c} />
        ))}
      </div>
    </div>
  );
}
