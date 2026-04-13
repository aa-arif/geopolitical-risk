import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { getCountries, getAlerts } from '../api';
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
            <span className="label">Violence Probability</span>
            <ProbabilityBar value={country.current_probability ?? 0} />
          </div>
          <div className="risk-card-tracks">
            <div className="track-item">
              <span className="label" title="Structural model based on regime type, development indicators, and conflict history">Structural</span>
              <span className="mono track-val" style={{ color: 'var(--track-a)' }}>
                {country.track_a != null ? Math.round(country.track_a * 100) + '%' : '--'}
              </span>
            </div>
            <div className="track-item">
              <span className="label" title="AI analysis of current news, ACLED events, and causal reasoning">AI Analysis</span>
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
  const [alerts, setAlerts] = useState([]);
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
    getAlerts(14).then(d => setAlerts(d.alerts || [])).catch(() => {});
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

      <div style={{
        padding: '.55rem .85rem', marginBottom: '1rem',
        background: 'rgba(56,189,248,.04)', border: '1px solid rgba(56,189,248,.1)',
        borderRadius: '8px', fontSize: '.72rem', lineHeight: '1.55', color: '#6b8aab',
      }}>
        <strong style={{ color: '#94a3b8' }}>What we predict:</strong> Probability of a significant escalation in political violence (battles, armed attacks, mass civilian casualties) within a 30-day window, exceeding the country's historical baseline. Combines structural indicators with AI-driven news analysis.
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

      {/* Active Predictions */}
      <motion.div initial={{opacity:0}} animate={{opacity:1}} transition={{delay:0.3}} style={{marginTop:'1.5rem'}}>
        <h3 style={{fontSize:'.72rem', fontWeight:700, color:'var(--text-2)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:'.6rem'}}>Active Predictions</h3>
        <div style={{display:'flex', gap:'.6rem', overflowX:'auto', paddingBottom:'.5rem', scrollbarWidth:'none', WebkitOverflowScrolling:'touch'}}>
          {countries.map(c => {
            const daysLeft = c.window_end ? Math.max(0, Math.ceil((new Date(c.window_end) - new Date()) / 86400000)) : null;
            const riskColor = RISK_COLORS[c.risk_level] || '#555';
            return (
              <Link to={`/country/${c.iso3}`} key={c.iso3} style={{
                minWidth:'140px', padding:'.7rem', background:'var(--bg-2)', border:'1px solid var(--border-0)',
                borderRadius:'var(--r-sm)', textDecoration:'none', color:'inherit', flexShrink:0,
              }}>
                <div style={{fontSize:'.68rem', color:'var(--text-2)', fontWeight:600}}>{c.name}</div>
                <div className="mono" style={{fontSize:'1.2rem', fontWeight:800, color:riskColor, margin:'.2rem 0'}}>
                  {Math.round((c.current_probability??0)*100)}%
                </div>
                <div className="mono" style={{fontSize:'.6rem', color:'var(--text-2)'}}>
                  {c.resolved ? (c.actual_outcome === 1 ? 'VIOLENCE ESCALATION' : 'BELOW THRESHOLD') : daysLeft != null ? `${daysLeft}d remaining` : 'Pending'}
                </div>
              </Link>
            );
          })}
        </div>
      </motion.div>

      {/* Recent Activity */}
      <motion.div initial={{opacity:0}} animate={{opacity:1}} transition={{delay:0.4}} style={{marginTop:'1.5rem'}}>
        <h3 style={{fontSize:'.72rem', fontWeight:700, color:'var(--text-2)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:'.6rem'}}>Recent Activity</h3>
        {alerts.length === 0 ? (
          <div style={{fontSize:'.78rem', color:'var(--text-2)', padding:'.8rem', background:'var(--bg-2)', borderRadius:'var(--r-sm)'}}>
            Change alerts will appear here as predictions evolve.
          </div>
        ) : (
          <div style={{background:'var(--bg-2)', borderRadius:'var(--r-sm)', border:'1px solid var(--border-0)'}}>
            {alerts.slice(0,8).map((a,i) => {
              const rising = a.delta > 0;
              const color = rising ? 'var(--critical)' : 'var(--low)';
              const arrow = rising ? '\u2191' : '\u2193';
              const daysAgo = Math.floor((Date.now() - new Date(a.alert_date).getTime()) / 86400000);
              const when = daysAgo === 0 ? 'today' : `${daysAgo}d ago`;
              return (
                <div key={i} style={{padding:'.55rem .8rem', borderBottom: i < alerts.length-1 ? '1px solid var(--border-0)' : 'none', display:'flex', alignItems:'center', gap:'.6rem'}}>
                  <span className="mono" style={{color, fontWeight:700, fontSize:'.85rem', minWidth:'20px'}}>{arrow}</span>
                  <span className="mono" style={{color, fontWeight:700, fontSize:'.78rem', minWidth:'50px'}}>{(a.delta*100).toFixed(1)}pp</span>
                  <span style={{flex:1, fontSize:'.75rem', color:'var(--text-1)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{a.alert_text}</span>
                  <span className="mono" style={{fontSize:'.6rem', color:'var(--text-2)', flexShrink:0}}>{when}</span>
                </div>
              );
            })}
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}
