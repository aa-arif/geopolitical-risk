import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { getCountryDetail, getCountryHistory } from '../api';
import RiskGauge from '../components/RiskGauge';
import TimeSeriesChart from '../components/TimeSeriesChart';

const RISK_COLORS = {
  CRITICAL: '#ef4444',
  HIGH: '#f59e0b',
  ELEVATED: '#eab308',
  LOW: '#22c55e',
};

const fadeUp = {
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4, ease: [0.4, 0, 0.2, 1] },
};

function Section({ children, delay = 0 }) {
  return (
    <motion.div
      className="detail-card"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: [0.4, 0, 0.2, 1] }}
    >
      {children}
    </motion.div>
  );
}

function ExecutiveSummary({ text }) {
  if (!text) return null;
  return (
    <motion.div {...fadeUp} transition={{ ...fadeUp.transition, delay: 0.15 }} style={{
      background: 'linear-gradient(135deg, rgba(56, 189, 248, 0.06) 0%, rgba(192, 132, 252, 0.04) 100%)',
      border: '1px solid rgba(56, 189, 248, 0.15)',
      borderRadius: '12px',
      padding: '1.25rem 1.5rem',
      marginBottom: '1.75rem',
      lineHeight: '1.7',
      fontSize: '0.9rem',
    }}>
      <span style={{ color: '#38bdf8', fontWeight: 700, fontSize: '0.68rem', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
        EXECUTIVE SUMMARY
      </span>
      <div style={{ marginTop: '0.5rem', color: '#c8d6e5' }}>{text}</div>
    </motion.div>
  );
}

const AGENT_LABELS = {
  baserate: 'Base Rate',
  analogy: 'Historical Analogy',
  decomposition: 'Decomposition',
  devil: "Devil's Advocate",
};
const AGENT_COLORS = {
  baserate: '#38bdf8',
  analogy: '#c084fc',
  decomposition: '#22c55e',
  devil: '#f59e0b',
};

function AgentOutputs({ agents }) {
  if (!agents || agents.length === 0) return null;

  return (
    <div>
      <h4 className="section-label">Forecasting Agents</h4>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.75rem' }}>
        {agents.map((agent, i) => {
          const label = AGENT_LABELS[agent.agent_type] || agent.agent_type;
          const color = AGENT_COLORS[agent.agent_type] || '#888';
          const r = agent.reasoning || {};
          let details = [];

          if (agent.agent_type === 'baserate') {
            details = [...(r.upward_adjustments || []).slice(0, 2).map(a => `+ ${a.factor || ''}`),
                        ...(r.downward_adjustments || []).slice(0, 2).map(a => `- ${a.factor || ''}`)];
          } else if (agent.agent_type === 'analogy') {
            details = (r.analogies || []).slice(0, 2).map(a => `${a.country || '?'} ${a.year || '?'}`);
          } else if (agent.agent_type === 'decomposition') {
            details = (r.sub_questions || []).slice(0, 3).map(q => q.question ? q.question.slice(0, 45) + '...' : '');
          } else if (agent.agent_type === 'devil') {
            details = (r.contrarian_arguments || []).slice(0, 2).map(a => a.argument ? a.argument.slice(0, 55) + '...' : '');
          }

          return (
            <motion.div
              key={i}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.3, delay: i * 0.06 }}
              style={{
                background: `linear-gradient(135deg, ${color}08, ${color}04)`,
                border: `1px solid ${color}25`,
                borderRadius: '10px',
                padding: '1rem',
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <div style={{
                position: 'absolute', top: 0, left: 0, right: 0, height: '2px',
                background: `linear-gradient(90deg, ${color}00, ${color}80, ${color}00)`,
              }} />
              <div style={{
                fontSize: '0.62rem', textTransform: 'uppercase', color,
                letterSpacing: '0.08em', fontWeight: 600, marginBottom: '0.4rem',
              }}>{label}</div>
              <div className="mono" style={{
                fontSize: '1.6rem', fontWeight: 800, color, letterSpacing: '-0.02em',
              }}>
                {agent.probability != null ? `${Math.round(agent.probability * 100)}%` : '--'}
              </div>
              {details.length > 0 && (
                <ul style={{ margin: '0.5rem 0 0', padding: '0 0 0 0.9rem', fontSize: '0.7rem', color: '#6b7f99', lineHeight: '1.6' }}>
                  {details.map((d, j) => <li key={j}>{d}</li>)}
                </ul>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

function ReasoningChains({ chains, narrative }) {
  const [expanded, setExpanded] = useState(true);

  const allFactors = [];
  const allCounterargs = [];

  if (Array.isArray(chains)) {
    for (const chain of chains) {
      for (const f of (chain.causal_factors || [])) allFactors.push(f);
      for (const c of (chain.counterarguments || [])) allCounterargs.push(c);
    }
  }

  if (allFactors.length === 0 && !narrative) return null;

  return (
    <div className="reasoning-chain">
      <h4 className="section-label" onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        Reasoning Chains ({allFactors.length} causal factors) {expanded ? '[-]' : '[+]'}
      </h4>

      <AnimatePresence>
        {expanded && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.25 }}>
            {narrative && (
              <div style={{ marginBottom: '1rem', padding: '0.85rem', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ fontSize: '0.65rem', textTransform: 'uppercase', color: '#4a5f7a', marginBottom: '0.4rem', letterSpacing: '0.08em', fontWeight: 600 }}>Supervisor Narrative</div>
                <div style={{ fontSize: '0.83rem', lineHeight: '1.65', color: '#8a9bb5' }}>{narrative}</div>
              </div>
            )}

            {allFactors.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                {allFactors.map((f, i) => {
                  const isRisk = f.direction === 'increases_risk';
                  const color = isRisk ? '#f59e0b' : '#22c55e';
                  const symbol = isRisk ? '+' : '-';
                  return (
                    <div key={i} style={{
                      padding: '0.65rem 0.85rem',
                      borderLeft: `3px solid ${color}`,
                      background: `${color}06`,
                      borderRadius: '0 8px 8px 0',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span className="mono" style={{ color, fontWeight: 700, fontSize: '0.82rem', minWidth: '18px' }}>[{symbol}]</span>
                        <span style={{ fontWeight: 600, fontSize: '0.85rem', flex: 1 }}>{f.factor}</span>
                        <span className="mono" style={{ color, fontSize: '0.78rem' }}>
                          {typeof f.confidence === 'number' ? `${Math.round(f.confidence * 100)}%` : ''}
                        </span>
                      </div>
                      {f.mechanism && (
                        <div style={{ fontSize: '0.74rem', color: '#8a9bb5', paddingLeft: '1.6rem', fontStyle: 'italic', marginTop: '0.2rem' }}>{f.mechanism}</div>
                      )}
                      {f.evidence && (
                        <div style={{ fontSize: '0.72rem', color: '#4a5f7a', paddingLeft: '1.6rem', marginTop: '0.15rem' }}>"{f.evidence}"</div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {allCounterargs.length > 0 && (
              <div style={{ marginTop: '1rem' }}>
                <div style={{ fontSize: '0.65rem', textTransform: 'uppercase', color: '#4a5f7a', marginBottom: '0.5rem', letterSpacing: '0.08em', fontWeight: 600 }}>Counterarguments</div>
                {allCounterargs.map((c, i) => {
                  const arg = typeof c === 'string' ? c : (c.argument || '');
                  const strength = typeof c === 'object' ? c.strength : null;
                  const evidence = typeof c === 'object' ? c.evidence : null;
                  const sColor = strength === 'strong' ? '#22c55e' : strength === 'weak' ? '#eab308' : '#38bdf8';
                  return (
                    <div key={i} style={{ padding: '0.45rem 0.8rem', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'baseline' }}>
                        {strength && <span className="mono" style={{ fontSize: '0.6rem', color: sColor, textTransform: 'uppercase', fontWeight: 700 }}>[{strength}]</span>}
                        <span style={{ color: '#22c55e', fontSize: '0.82rem' }}>{arg}</span>
                      </div>
                      {evidence && <div style={{ fontSize: '0.7rem', color: '#4a5f7a', paddingLeft: strength ? '3rem' : '0', marginTop: '0.15rem' }}>"{evidence}"</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function TrackBreakdown({ components }) {
  if (!components || Object.keys(components).length === 0) return null;
  const items = Object.entries(components).map(([key, val]) => ({ name: key, value: val }));

  return (
    <div className="track-breakdown">
      <h4 className="section-label">Track A Components</h4>
      <div className="breakdown-grid">
        {items.map((item, i) => {
          const name = item.name.replace(/_/g, ' ');
          const val = item.value;
          const numVal = typeof val === 'number' ? val : parseFloat(val);
          return (
            <div key={i} className="breakdown-item">
              <span className="breakdown-name">{name}</span>
              <span className="mono breakdown-val">{!isNaN(numVal) ? numVal.toFixed(3) : String(val)}</span>
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
        <h4 className="section-label">ACLED Events (Last {acled.period_days || 30} Days)</h4>
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
          return <div key={i} className="actor-chip"><span className="actor-name">{name}</span></div>;
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
      .then(([d, h]) => { setDetail(d); setHistory(h); })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [iso3]);

  if (loading) return <div className="loading">Loading country intelligence...</div>;
  if (error) return <div className="error">Error: {error}</div>;
  if (!detail) return <div className="error">No data available</div>;

  const pred = detail.current_prediction || {};
  const reasoning = detail.reasoning || {};
  const probability = pred.probability ?? 0;
  const riskLevel = pred.risk_level || 'LOW';
  const riskColor = RISK_COLORS[riskLevel] || '#888';
  const confidence = reasoning.confidence || 'N/A';

  return (
    <motion.div
      className="country-detail"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
    >
      <Link to="/" className="back-link">&larr; Back to Dashboard</Link>

      <motion.div className="detail-header" {...fadeUp}>
        <div>
          <h2 className="detail-country-name">
            {detail.name || iso3}
            <span className="detail-iso">{iso3}</span>
          </h2>
        </div>
        <span className="risk-badge large" style={{ background: riskColor }}>
          {riskLevel}
        </span>
      </motion.div>

      <ExecutiveSummary text={reasoning.executive_summary} />

      <div className="detail-grid">
        <Section delay={0.1}>
          <h4 className="section-label">Instability Probability</h4>
          <RiskGauge probability={probability} width={300} height={190} />
          <div className="gauge-tracks">
            <div className="gauge-track-item">
              <span className="label">Track A</span>
              <span className="mono" style={{ color: 'var(--track-a)' }}>{Math.round((pred.track_a ?? 0) * 100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Track B</span>
              <span className="mono" style={{ color: 'var(--track-b)' }}>{Math.round((pred.track_b ?? 0) * 100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Confidence</span>
              <span className="mono">{confidence}</span>
            </div>
          </div>
        </Section>

        <Section delay={0.15}>
          <h4 className="section-label">Prediction History</h4>
          {history && history.predictions && history.predictions.length > 0 ? (
            <TimeSeriesChart data={history.predictions} />
          ) : (
            <div className="no-data">No historical data available</div>
          )}
        </Section>
      </div>

      <div className="detail-sections">
        <Section delay={0.2}>
          <TrackBreakdown components={reasoning.track_a_components} />
        </Section>

        <Section delay={0.25}>
          <AgentOutputs agents={detail.agent_outputs} />
        </Section>

        <Section delay={0.3}>
          <ReasoningChains chains={detail.reasoning_chains} narrative={reasoning.narrative} />
        </Section>

        <Section delay={0.35}>
          <ACLEDSummary acled={detail.acled_30d} />
        </Section>

        <Section delay={0.4}>
          <KeyActors actors={detail.key_actors} />
        </Section>
      </div>
    </motion.div>
  );
}
