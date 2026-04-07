import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { getCountryDetail, getCountryHistory } from '../api';
import RiskGauge from '../components/RiskGauge';
import TimeSeriesChart from '../components/TimeSeriesChart';

const RISK_COLORS = {
  CRITICAL: '#ff3b3b',
  HIGH: '#ff8c00',
  ELEVATED: '#ffd600',
  LOW: '#00e676',
};

function ExecutiveSummary({ text }) {
  if (!text) return null;
  return (
    <div className="executive-summary" style={{
      background: 'rgba(79, 195, 247, 0.08)',
      border: '1px solid rgba(79, 195, 247, 0.2)',
      borderRadius: '6px',
      padding: '1rem 1.2rem',
      marginBottom: '1.5rem',
      lineHeight: '1.65',
      fontSize: '0.92rem',
    }}>
      <strong style={{ color: '#4fc3f7', marginRight: '0.5rem' }}>EXECUTIVE SUMMARY</strong>
      <span>{text}</span>
    </div>
  );
}

function ReasoningChains({ chains, narrative }) {
  const [expanded, setExpanded] = useState(true);

  // Collect all causal factors and counterarguments from all chain objects
  const allFactors = [];
  const allCounterargs = [];

  if (Array.isArray(chains)) {
    for (const chain of chains) {
      for (const f of (chain.causal_factors || [])) {
        allFactors.push(f);
      }
      for (const c of (chain.counterarguments || [])) {
        allCounterargs.push(c);
      }
    }
  }

  if (allFactors.length === 0 && !narrative) return null;

  return (
    <div className="reasoning-chain">
      <h4 className="section-label" onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        Reasoning Chains ({allFactors.length} causal factors) {expanded ? '[-]' : '[+]'}
      </h4>

      {expanded && (
        <>
          {narrative && (
            <div style={{ marginBottom: '1rem', padding: '0.8rem', background: 'rgba(255,255,255,0.03)', borderRadius: '4px' }}>
              <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#6b7f99', marginBottom: '0.4rem', letterSpacing: '0.05em' }}>Supervisor Narrative</div>
              <div style={{ fontSize: '0.85rem', lineHeight: '1.6' }}>{narrative}</div>
            </div>
          )}

          {allFactors.length > 0 && (
            <ul className="factor-list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {allFactors.map((f, i) => {
                const isRisk = f.direction === 'increases_risk';
                const color = isRisk ? '#ff8c00' : '#00e676';
                const symbol = isRisk ? '+' : '-';
                return (
                  <li key={i} className="factor-item" style={{
                    padding: '0.6rem 0.8rem',
                    borderBottom: '1px solid rgba(255,255,255,0.05)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.25rem',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span style={{
                        color, fontFamily: "'JetBrains Mono', monospace",
                        fontWeight: 700, fontSize: '0.85rem', minWidth: '18px',
                      }}>[{symbol}]</span>
                      <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{f.factor}</span>
                      <span className="mono" style={{ marginLeft: 'auto', color, fontSize: '0.8rem' }}>
                        {typeof f.confidence === 'number' ? `${Math.round(f.confidence * 100)}%` : ''}
                      </span>
                    </div>
                    {f.evidence && (
                      <div style={{ fontSize: '0.78rem', color: '#8899aa', paddingLeft: '1.6rem', lineHeight: '1.5' }}>
                        {f.evidence}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {allCounterargs.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#6b7f99', marginBottom: '0.4rem', letterSpacing: '0.05em' }}>Counterarguments</div>
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {allCounterargs.map((c, i) => (
                  <li key={i} style={{
                    padding: '0.4rem 0.8rem',
                    fontSize: '0.82rem',
                    color: '#00e676',
                    borderBottom: '1px solid rgba(255,255,255,0.03)',
                  }}>
                    - {typeof c === 'string' ? c : JSON.stringify(c)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function TrackBreakdown({ components }) {
  if (!components || Object.keys(components).length === 0) return null;

  const items = Object.entries(components).map(([key, val]) => ({ name: key, value: val }));

  return (
    <div className="track-breakdown">
      <h4 className="section-label">Track A Component Breakdown</h4>
      <div className="breakdown-grid">
        {items.map((item, i) => {
          const name = item.name.replace(/_/g, ' ');
          const val = item.value;
          const numVal = typeof val === 'number' ? val : parseFloat(val);
          return (
            <div key={i} className="breakdown-item">
              <span className="breakdown-name">{name}</span>
              <span className="mono breakdown-val">
                {!isNaN(numVal) ? numVal.toFixed(3) : String(val)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ACLEDSummary({ acled }) {
  if (!acled) return null;

  if (typeof acled === 'object' && !Array.isArray(acled)) {
    return (
      <div className="acled-summary">
        <h4 className="section-label">ACLED Event Data (Last {acled.period_days || 30} Days)</h4>
        <div className="acled-stats">
          {Object.entries(acled).map(([key, val]) => {
            if (key === 'by_type') return null;
            return (
              <div key={key} className="acled-stat">
                <span className="label">{key.replace(/_/g, ' ')}</span>
                <span className="mono">{typeof val === 'number' ? val.toLocaleString() : String(val)}</span>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return null;
}

function KeyActors({ actors }) {
  if (!actors || (Array.isArray(actors) && actors.length === 0)) return null;
  const list = Array.isArray(actors) ? actors : [actors];
  return (
    <div className="key-actors">
      <h4 className="section-label">Key Actors</h4>
      <div className="actors-list">
        {list.map((actor, i) => {
          const name = typeof actor === 'string' ? actor : (actor.name || JSON.stringify(actor));
          return (
            <div key={i} className="actor-chip">
              <span className="actor-name">{name}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function CountryDetail() {
  const { iso3 } = useParams();
  const [detail, setDetail] = useState(null);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    const detailPromise = getCountryDetail(iso3);
    const historyPromise = getCountryHistory(iso3).catch(() => null);
    Promise.all([detailPromise, historyPromise])
      .then(([d, h]) => {
        setDetail(d);
        setHistory(h);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [iso3]);

  if (loading) return <div className="loading">Loading country intelligence...</div>;
  if (error) return <div className="error">Error: {error}</div>;
  if (!detail) return <div className="error">No data available</div>;

  // Read from correct nested paths
  const pred = detail.current_prediction || {};
  const reasoning = detail.reasoning || {};
  const probability = pred.probability ?? 0;
  const riskLevel = pred.risk_level || 'LOW';
  const riskColor = RISK_COLORS[riskLevel] || '#888';
  const confidence = reasoning.confidence || 'N/A';

  return (
    <div className="country-detail">
      <Link to="/" className="back-link">&larr; Back to Dashboard</Link>

      <div className="detail-header">
        <div>
          <h2 className="detail-country-name">{detail.name || iso3}</h2>
          <span className="detail-iso mono">{iso3}</span>
        </div>
        <span className="risk-badge large" style={{ background: riskColor }}>
          {riskLevel}
        </span>
      </div>

      <ExecutiveSummary text={reasoning.executive_summary} />

      <div className="detail-grid">
        <div className="detail-card gauge-card">
          <h4 className="section-label">Conflict Probability</h4>
          <RiskGauge probability={probability} />
          <div className="gauge-tracks">
            <div className="gauge-track-item">
              <span className="label">Track A</span>
              <span className="mono">{Math.round((pred.track_a ?? 0) * 100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Track B</span>
              <span className="mono">{Math.round((pred.track_b ?? 0) * 100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Confidence</span>
              <span className="mono">{confidence}</span>
            </div>
          </div>
        </div>

        <div className="detail-card chart-card">
          <h4 className="section-label">Prediction History</h4>
          {history && history.predictions && history.predictions.length > 0 ? (
            <TimeSeriesChart data={history.predictions} />
          ) : (
            <div className="no-data">No historical data available</div>
          )}
        </div>
      </div>

      <div className="detail-sections">
        <div className="detail-card">
          <TrackBreakdown components={reasoning.track_a_components} />
        </div>

        <div className="detail-card">
          <ReasoningChains chains={detail.reasoning_chains} narrative={reasoning.narrative} />
        </div>

        <div className="detail-card">
          <ACLEDSummary acled={detail.acled_30d} />
        </div>

        <div className="detail-card">
          <KeyActors actors={detail.key_actors} />
        </div>
      </div>
    </div>
  );
}
