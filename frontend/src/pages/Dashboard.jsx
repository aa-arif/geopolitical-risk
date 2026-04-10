import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { getCountries } from '../api';

const RISK_ORDER = { CRITICAL: 0, HIGH: 1, ELEVATED: 2, LOW: 3 };
const RISK_COLORS = {
  CRITICAL: '#ef4444',
  HIGH: '#f59e0b',
  ELEVATED: '#eab308',
  LOW: '#22c55e',
};
const RISK_BG = {
  CRITICAL: 'rgba(239, 68, 68, 0.08)',
  HIGH: 'rgba(245, 158, 11, 0.08)',
  ELEVATED: 'rgba(234, 179, 8, 0.08)',
  LOW: 'rgba(34, 197, 94, 0.08)',
};

function ProbabilityBar({ value }) {
  const pct = Math.round(value * 100);
  let color;
  if (pct >= 50) color = '#ef4444';
  else if (pct >= 30) color = '#f59e0b';
  else if (pct >= 15) color = '#eab308';
  else color = '#22c55e';

  return (
    <div className="prob-bar-container">
      <div className="prob-bar-track">
        <motion.div
          className="prob-bar-fill"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1, ease: [0.4, 0, 0.2, 1], delay: 0.3 }}
          style={{ background: `linear-gradient(90deg, ${color}88, ${color})` }}
        />
      </div>
      <span className="prob-bar-label mono" style={{ color }}>{pct}%</span>
    </div>
  );
}

function ConfidenceIndicator({ value }) {
  const levelMap = { low: 1, medium: 3, high: 5 };
  const bars = 5;
  const filled = typeof value === 'string' ? (levelMap[value] || 0) : 0;
  const label = typeof value === 'string' ? value : 'N/A';
  return (
    <div className="confidence-indicator" title={`Confidence: ${label}`}>
      {Array.from({ length: bars }, (_, i) => (
        <div
          key={i}
          className={`confidence-bar ${i < filled ? 'filled' : ''}`}
        />
      ))}
    </div>
  );
}

function RiskCard({ country, index }) {
  const riskColor = RISK_COLORS[country.risk_level] || '#888';
  const riskBg = RISK_BG[country.risk_level] || 'transparent';

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.08, ease: [0.4, 0, 0.2, 1] }}
    >
      <Link to={`/country/${country.iso3}`} className="risk-card" style={{ borderColor: `${riskColor}20` }}>
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: '2px',
          background: `linear-gradient(90deg, transparent, ${riskColor}60, transparent)`,
        }} />

        <div className="risk-card-header">
          <h3 className="risk-card-name">{country.name}</h3>
          <span className="risk-badge" style={{ background: riskColor }}>
            {country.risk_level}
          </span>
        </div>

        <div className="risk-card-body">
          <div className="risk-card-row">
            <span className="label">Fused Probability</span>
            <ProbabilityBar value={country.current_probability ?? 0} />
          </div>

          <div className="risk-card-tracks">
            <div className="track-item">
              <span className="label">Track A (structural)</span>
              <span className="mono track-val" style={{ color: 'var(--track-a)' }}>
                {country.track_a != null ? Math.round(country.track_a * 100) + '%' : '--'}
              </span>
            </div>
            <div className="track-item">
              <span className="label">Track B (LLM)</span>
              <span className="mono track-val" style={{ color: 'var(--track-b)' }}>
                {country.track_b != null ? Math.round(country.track_b * 100) + '%' : '--'}
              </span>
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
    </motion.div>
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
    <motion.div
      className="dashboard"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      <div className="page-header">
        <motion.h2
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.4 }}
        >Risk Overview</motion.h2>
        <span className="subtitle mono">{countries.length} countries monitored</span>
      </div>
      <div className="risk-grid">
        {countries.map((c, i) => (
          <RiskCard key={c.iso3} country={c} index={i} />
        ))}
      </div>
    </motion.div>
  );
}
