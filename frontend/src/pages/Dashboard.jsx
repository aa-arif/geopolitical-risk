import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { getCountries } from '../api';
import WorldMap from '../components/WorldMap';

const RISK_ORDER = { CRITICAL: 0, HIGH: 1, ELEVATED: 2, LOW: 3 };
const RISK_COLORS = { CRITICAL: '#ef4444', HIGH: '#f59e0b', ELEVATED: '#eab308', LOW: '#22c55e' };

function ProbabilityBar({ value }) {
  const pct = Math.round(value * 100);
  const color = pct >= 50 ? '#ef4444' : pct >= 30 ? '#f59e0b' : pct >= 15 ? '#eab308' : '#22c55e';
  return (
    <div className="prob-bar-container">
      <div className="prob-bar-track">
        <motion.div className="prob-bar-fill"
          initial={{ width: 0 }} animate={{ width: `${pct}%` }}
          transition={{ duration: 1.2, ease: [.4,0,.2,1], delay: .2 }}
          style={{ background: `linear-gradient(90deg, ${color}66, ${color})` }} />
      </div>
      <span className="prob-bar-label mono" style={{ color }}>{pct}%</span>
    </div>
  );
}

function ConfidenceIndicator({ value }) {
  const levelMap = { low: 1, medium: 3, high: 5 };
  const filled = typeof value === 'string' ? (levelMap[value] || 0) : 0;
  return (
    <div className="confidence-indicator" title={`Confidence: ${value || 'N/A'}`}>
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className={`confidence-bar ${i < filled ? 'filled' : ''}`} />
      ))}
    </div>
  );
}

function RiskCard({ country, index }) {
  const riskColor = RISK_COLORS[country.risk_level] || '#555';
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: .35, delay: index * 0.04, ease: [.4,0,.2,1] }}
    >
      <Link to={`/country/${country.iso3}`} className="risk-card">
        <div className="risk-card-header">
          <h3 className="risk-card-name">{country.name}</h3>
          <span className="risk-badge" style={{ background: riskColor }}>{country.risk_level || 'N/A'}</span>
        </div>
        <div className="risk-card-body">
          <div className="risk-card-row">
            <span className="label">Fused Probability</span>
            <ProbabilityBar value={country.current_probability ?? 0} />
          </div>
          <div className="risk-card-tracks">
            <div className="track-item">
              <span className="label">Track A</span>
              <span className="mono track-val" style={{ color: 'var(--track-a)' }}>
                {country.track_a != null ? Math.round(country.track_a * 100) + '%' : '--'}
              </span>
            </div>
            <div className="track-item">
              <span className="label">Track B</span>
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
            <span className="prediction-date mono">{country.prediction_date}</span>
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
  const [view, setView] = useState('grid');

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

  const highCount = countries.filter(c => c.risk_level === 'HIGH' || c.risk_level === 'CRITICAL').length;
  const elevatedCount = countries.filter(c => c.risk_level === 'ELEVATED').length;

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: .3 }}>
      <div className="page-header">
        <div>
          <motion.h2 initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: .4 }}>
            Risk Overview
          </motion.h2>
          <span className="subtitle mono">{countries.length} countries monitored</span>
        </div>
        <div className="page-header-right">
          {highCount > 0 && (
            <span className="stat-pill">
              <span className="dot" style={{ background: '#f59e0b' }} />
              <span className="mono" style={{ color: '#f59e0b' }}>{highCount} HIGH</span>
            </span>
          )}
          {elevatedCount > 0 && (
            <span className="stat-pill">
              <span className="dot" style={{ background: '#eab308' }} />
              <span className="mono" style={{ color: '#eab308' }}>{elevatedCount} ELEVATED</span>
            </span>
          )}
          <div className="view-toggle">
            <button className={view === 'grid' ? 'active' : ''} onClick={() => setView('grid')}>Grid</button>
            <button className={view === 'map' ? 'active' : ''} onClick={() => setView('map')}>Map</button>
          </div>
        </div>
      </div>

      <AnimatePresence mode="wait">
        {view === 'grid' ? (
          <motion.div key="grid" className="risk-grid"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: .2 }}>
            {countries.map((c, i) => <RiskCard key={c.iso3} country={c} index={i} />)}
          </motion.div>
        ) : (
          <motion.div key="map"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: .2 }}>
            <WorldMap countries={countries} />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
