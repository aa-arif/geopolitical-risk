import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { getCountryDetail, getCountryHistory, getCountryEvents } from '../api';
import RiskGauge from '../components/RiskGauge';
import TimeSeriesChart from '../components/TimeSeriesChart';

const RISK_COLORS = { CRITICAL: '#ef4444', HIGH: '#f59e0b', ELEVATED: '#eab308', LOW: '#22c55e' };
const AGENT_LABELS = { baserate: 'Base Rate', analogy: 'Historical Analogy', decomposition: 'Decomposition', devil: "Devil's Advocate" };
const AGENT_COLORS = { baserate: '#38bdf8', analogy: '#a78bfa', decomposition: '#22c55e', devil: '#f59e0b' };
const ET_NAMES = { ACE: 'Armed Conflict', MCU: 'Civil Unrest', REC: 'Regime Change', PSS: 'Policy/Sanctions', CID: 'Infrastructure' };
const ET_COLORS = { ACE: '#ef4444', MCU: '#f59e0b', REC: '#a78bfa', PSS: '#38bdf8', CID: '#64748b' };

function Card({ children, delay = 0 }) {
  return (
    <motion.div className="detail-card"
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: .35, delay, ease: [.4,0,.2,1] }}>
      {children}
    </motion.div>
  );
}

function ExecutiveSummary({ text }) {
  if (!text) return null;
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: .35, delay: .1 }}
      style={{
        background: 'linear-gradient(135deg, rgba(56,189,248,.05), rgba(167,139,250,.03))',
        border: '1px solid rgba(56,189,248,.12)', borderRadius: '12px',
        padding: '1.1rem 1.3rem', marginBottom: '1.25rem',
      }}>
      <span style={{ color: '#38bdf8', fontWeight: 700, fontSize: '.6rem', letterSpacing: '.1em', textTransform: 'uppercase' }}>
        EXECUTIVE SUMMARY
      </span>
      <div style={{ marginTop: '.4rem', fontSize: '.85rem', lineHeight: '1.65', color: '#94a3b8' }}>{text}</div>
    </motion.div>
  );
}

function AgentOutputs({ agents }) {
  if (!agents || agents.length === 0) return null;
  return (
    <div>
      <h4 className="section-label">AI Analysis Agents</h4>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '.65rem' }}>
        {agents.map((agent, i) => {
          const label = AGENT_LABELS[agent.agent_type] || agent.agent_type;
          const color = AGENT_COLORS[agent.agent_type] || '#888';
          const r = agent.reasoning || {};
          let details = [];
          if (agent.agent_type === 'baserate')
            details = [...(r.upward_adjustments||[]).slice(0,2).map(a=>`+ ${a.factor||''}`), ...(r.downward_adjustments||[]).slice(0,2).map(a=>`- ${a.factor||''}`)];
          else if (agent.agent_type === 'analogy')
            details = (r.analogies||[]).slice(0,2).map(a=>`${a.country||'?'} ${a.year||'?'}`);
          else if (agent.agent_type === 'decomposition')
            details = (r.sub_questions||[]).slice(0,2).map(q=>q.question?q.question.slice(0,40)+'...':'');
          else if (agent.agent_type === 'devil')
            details = (r.contrarian_arguments||[]).slice(0,2).map(a=>a.argument?a.argument.slice(0,50)+'...':'');

          return (
            <motion.div key={i}
              initial={{ opacity: 0, scale: .96 }} animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: .3, delay: i * .05 }}
              style={{
                background: `linear-gradient(160deg, ${color}06, ${color}03)`,
                border: `1px solid ${color}18`, borderRadius: '10px',
                padding: '.85rem', position: 'relative', overflow: 'hidden',
              }}>
              <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '1px', background: `linear-gradient(90deg, transparent, ${color}50, transparent)` }} />
              <div style={{ fontSize: '.58rem', textTransform: 'uppercase', color, letterSpacing: '.08em', fontWeight: 700, marginBottom: '.3rem' }}>{label}</div>
              <div className="mono" style={{ fontSize: '1.5rem', fontWeight: 800, color, letterSpacing: '-.02em' }}>
                {agent.probability != null ? `${Math.round(agent.probability * 100)}%` : '--'}
              </div>
              {details.length > 0 && (
                <ul style={{ margin: '.4rem 0 0', padding: '0 0 0 .8rem', fontSize: '.65rem', color: '#475569', lineHeight: '1.55' }}>
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
  const allFactors = [], allCounterargs = [];
  if (Array.isArray(chains)) {
    for (const chain of chains) {
      for (const f of (chain.causal_factors || [])) allFactors.push(f);
      for (const c of (chain.counterarguments || [])) allCounterargs.push(c);
    }
  }
  if (allFactors.length === 0 && !narrative) return null;

  return (
    <div className="reasoning-chain">
      <h4 className="section-label" onClick={() => setExpanded(!expanded)}>
        Causal Analysis ({allFactors.length} factors) {expanded ? '[-]' : '[+]'}
      </h4>
      <AnimatePresence>
        {expanded && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={{ duration: .2 }}>
            {narrative && (
              <div style={{ marginBottom: '.85rem', padding: '.75rem', background: 'rgba(255,255,255,.015)', borderRadius: '8px', border: '1px solid rgba(255,255,255,.03)' }}>
                <div style={{ fontSize: '.58rem', textTransform: 'uppercase', color: '#475569', marginBottom: '.35rem', letterSpacing: '.08em', fontWeight: 700 }}>Supervisor Narrative</div>
                <div style={{ fontSize: '.78rem', lineHeight: '1.6', color: '#94a3b8' }}>{narrative}</div>
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '.3rem' }}>
              {allFactors.slice(0, 12).map((f, i) => {
                const isRisk = f.direction === 'increases_risk';
                const color = isRisk ? '#f59e0b' : '#22c55e';
                return (
                  <div key={i} style={{ padding: '.55rem .75rem', borderLeft: `2px solid ${color}`, background: `${color}05`, borderRadius: '0 6px 6px 0' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                      <span className="mono" style={{ color, fontWeight: 700, fontSize: '.75rem' }}>[{isRisk?'+':'-'}]</span>
                      <span style={{ fontWeight: 600, fontSize: '.78rem', flex: 1 }}>{f.factor}</span>
                      <span className="mono" style={{ color, fontSize: '.72rem' }}>{typeof f.confidence==='number'?`${Math.round(f.confidence*100)}%`:''}</span>
                    </div>
                    {f.mechanism && <div style={{ fontSize: '.68rem', color: '#94a3b8', paddingLeft: '1.4rem', fontStyle: 'italic', marginTop: '.15rem' }}>{f.mechanism}</div>}
                    {f.evidence && <div style={{ fontSize: '.65rem', color: '#475569', paddingLeft: '1.4rem', marginTop: '.1rem' }}>"{f.evidence}"</div>}
                  </div>
                );
              })}
            </div>
            {allCounterargs.length > 0 && (
              <div style={{ marginTop: '.85rem' }}>
                <div style={{ fontSize: '.58rem', textTransform: 'uppercase', color: '#475569', marginBottom: '.4rem', letterSpacing: '.08em', fontWeight: 700 }}>Counterarguments</div>
                {allCounterargs.slice(0, 5).map((c, i) => {
                  const arg = typeof c === 'string' ? c : (c.argument || '');
                  const strength = typeof c === 'object' ? c.strength : null;
                  const evidence = typeof c === 'object' ? c.evidence : null;
                  const sColor = strength === 'strong' ? '#22c55e' : strength === 'weak' ? '#eab308' : '#38bdf8';
                  return (
                    <div key={i} style={{ padding: '.35rem .7rem', borderBottom: '1px solid rgba(255,255,255,.02)' }}>
                      <div style={{ display: 'flex', gap: '.4rem', alignItems: 'baseline' }}>
                        {strength && <span className="mono" style={{ fontSize: '.55rem', color: sColor, textTransform: 'uppercase', fontWeight: 700 }}>[{strength}]</span>}
                        <span style={{ color: '#22c55e', fontSize: '.75rem' }}>{arg}</span>
                      </div>
                      {evidence && <div style={{ fontSize: '.65rem', color: '#475569', paddingLeft: strength?'2.5rem':'0', marginTop: '.1rem' }}>"{evidence}"</div>}
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
  return (
    <div>
      <h4 className="section-label">Structural Model Components</h4>
      <div className="breakdown-grid">
        {Object.entries(components).map(([key, val], i) => {
          const numVal = typeof val === 'number' ? val : parseFloat(val);
          return (
            <div key={i} className="breakdown-item">
              <span className="breakdown-name">{key.replace(/_/g, ' ')}</span>
              <span className="mono breakdown-val">{!isNaN(numVal) ? numVal.toFixed(3) : String(val)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EventTypeBreakdown({ scores }) {
  if (!scores || Object.keys(scores).length === 0) return null;
  const types = ['ACE', 'MCU', 'REC', 'PSS', 'CID'];
  return (
    <div>
      <h4 className="section-label">Event Type Breakdown</h4>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '.45rem' }}>
        {types.map((et) => {
          const data = scores[et];
          if (!data) return null;
          const prob = typeof data === 'object' ? data.probability : data;
          const pct = Math.round((prob || 0) * 100);
          const color = ET_COLORS[et] || '#888';
          const level = pct >= 60 ? 'CRITICAL' : pct >= 40 ? 'HIGH' : pct >= 20 ? 'ELEVATED' : 'LOW';
          return (
            <div key={et} style={{ display: 'flex', alignItems: 'center', gap: '.6rem' }}>
              <span style={{ width: '105px', fontSize: '.7rem', fontWeight: 600, color: '#94a3b8' }}>
                {ET_NAMES[et] || et}
              </span>
              <div style={{ flex: 1, height: '18px', background: 'rgba(255,255,255,.03)', borderRadius: '4px', overflow: 'hidden', position: 'relative' }}>
                <motion.div
                  initial={{ width: 0 }} animate={{ width: `${pct}%` }}
                  transition={{ duration: 1, ease: [.4,0,.2,1], delay: .1 }}
                  style={{ height: '100%', background: `linear-gradient(90deg, ${color}66, ${color})`, borderRadius: '4px' }} />
              </div>
              <span className="mono" style={{ width: '36px', textAlign: 'right', fontSize: '.78rem', fontWeight: 700, color }}>{pct}%</span>
              <span style={{ fontSize: '.55rem', fontWeight: 700, color: RISK_COLORS[level], width: '55px', textAlign: 'right' }}>{level}</span>
            </div>
          );
        })}
      </div>
      {scores.ACE?.driver && (
        <div style={{ marginTop: '.6rem', fontSize: '.68rem', color: '#64748b', lineHeight: '1.5' }}>
          {Object.entries(scores).filter(([, v]) => typeof v === 'object' && v.driver).map(([et, v]) => (
            <div key={et}><span style={{ color: ET_COLORS[et], fontWeight: 600 }}>{ET_NAMES[et]}:</span> {v.driver}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function DailyBrief({ brief }) {
  if (!brief) return null;
  return (
    <div>
      <h4 className="section-label">Today's Brief</h4>
      <div style={{
        padding: '.85rem 1rem', background: 'rgba(56,189,248,.03)',
        border: '1px solid rgba(56,189,248,.08)', borderRadius: '10px',
      }}>
        <div style={{ fontWeight: 700, fontSize: '.82rem', color: '#e2e8f0', marginBottom: '.4rem' }}>
          {brief.headline}
        </div>
        <div style={{ fontSize: '.75rem', lineHeight: '1.65', color: '#94a3b8', whiteSpace: 'pre-wrap' }}>
          {brief.body}
        </div>
      </div>
    </div>
  );
}

function RecentAlerts({ alerts }) {
  if (!alerts || alerts.length === 0) return null;
  return (
    <div>
      <h4 className="section-label">Alerts (Last 7 Days)</h4>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '.25rem' }}>
        {alerts.map((a, i) => {
          const rising = a.delta > 0;
          const color = rising ? '#ef4444' : '#22c55e';
          return (
            <div key={i} style={{
              padding: '.45rem .7rem', display: 'flex', alignItems: 'center', gap: '.5rem',
              background: `${color}08`, border: `1px solid ${color}15`, borderRadius: '6px',
            }}>
              <span className="mono" style={{ color, fontWeight: 700, fontSize: '.7rem' }}>
                {rising ? '\u2191' : '\u2193'} {(a.delta * 100).toFixed(1)}pp
              </span>
              <span style={{ fontSize: '.55rem', fontWeight: 600, color: ET_COLORS[a.event_type] || '#888', textTransform: 'uppercase' }}>
                {a.event_type}
              </span>
              <span style={{ flex: 1, fontSize: '.7rem', color: '#94a3b8' }}>{a.brief}</span>
              <span className="mono" style={{ fontSize: '.55rem', color: '#475569' }}>{a.date}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConflictEventSummary({ data }) {
  if (!data || typeof data !== 'object') return null;
  return (
    <div>
      <h4 className="section-label">Conflict Events (Last {data.period_days || 30} Days)</h4>
      <div className="acled-stats">
        {Object.entries(data).filter(([k]) => !['by_type', 'by_category'].includes(k)).map(([key, val]) => (
          <div key={key} className="acled-stat">
            <span className="label">{key.replace(/_/g, ' ')}</span>
            <span className="mono">{typeof val === 'number' ? val.toLocaleString() : String(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function KeyActors({ actors }) {
  if (!actors || actors.length === 0) return null;
  return (
    <div>
      <h4 className="section-label">Key Actors</h4>
      <div className="actors-list">
        {actors.map((a, i) => <div key={i} className="actor-chip"><span className="actor-name">{typeof a === 'string' ? a : a.name}</span></div>)}
      </div>
    </div>
  );
}

export default function CountryDetail() {
  const { iso3 } = useParams();
  const [detail, setDetail] = useState(null);
  const [history, setHistory] = useState(null);
  const [events, setEvents] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getCountryDetail(iso3),
      getCountryHistory(iso3).catch(() => null),
      getCountryEvents(iso3, 60).catch(() => null),
    ])
      .then(([d, h, ev]) => { setDetail(d); setHistory(h); setEvents(ev?.timeline || null); })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [iso3]);

  if (loading) return <div className="loading">Loading intelligence...</div>;
  if (error) return <div className="error">Error: {error}</div>;
  if (!detail) return <div className="error">No data</div>;

  const pred = detail.current_prediction || {};
  const reasoning = detail.reasoning || {};
  const probability = pred.probability ?? 0;
  const riskLevel = pred.risk_level || 'LOW';
  const riskColor = RISK_COLORS[riskLevel] || '#888';

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: .3 }}>
      <Link to="/" className="back-link">&larr; Dashboard</Link>

      <motion.div className="detail-header"
        initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .35 }}>
        <div>
          <h2 className="detail-country-name">{detail.name || iso3}<span className="detail-iso">{iso3}</span></h2>
        </div>
        <span className="risk-badge large" style={{ background: riskColor }}>{riskLevel}</span>
      </motion.div>

      <ExecutiveSummary text={reasoning.executive_summary} />

      {detail.risk_context && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: .35, delay: .05 }}
          style={{
            padding: '.85rem 1.1rem', marginBottom: '1.25rem',
            background: 'rgba(255,255,255,.02)', border: '1px solid rgba(255,255,255,.04)',
            borderRadius: '10px', fontSize: '.8rem', lineHeight: '1.65', color: '#7b8fa3',
          }}>
          <span style={{ fontWeight: 700, fontSize: '.58rem', letterSpacing: '.08em', textTransform: 'uppercase', color: '#475569', display: 'block', marginBottom: '.3rem' }}>Country Risk Context</span>
          {detail.risk_context}
        </motion.div>
      )}

      <div className="detail-grid">
        <Card delay={.08}>
          <h4 className="section-label">Political Violence Probability</h4>
          <div style={{ fontSize: '.62rem', color: '#475569', marginBottom: '.5rem', lineHeight: '1.5' }}>
            Probability of significant escalation in battles, armed attacks, or mass civilian casualties within 30 days.
          </div>
          <RiskGauge probability={probability} width={280} height={175} />
          <div className="gauge-tracks">
            <div className="gauge-track-item">
              <span className="label" title="PITF-based structural model using regime type, development indicators, and conflict history">Structural</span>
              <span className="mono" style={{ color: 'var(--track-a)' }}>{Math.round((pred.track_a??0)*100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label" title="AI ensemble analyzing current news, ACLED events, and causal reasoning chains">AI Analysis</span>
              <span className="mono" style={{ color: 'var(--track-b)' }}>{Math.round((pred.track_b??0)*100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Confidence</span>
              <span className="mono">{reasoning.confidence || 'N/A'}</span>
            </div>
          </div>
        </Card>
        <Card delay={.12}>
          <h4 className="section-label">Prediction History</h4>
          {history?.predictions?.length > 0
            ? <TimeSeriesChart data={history.predictions} events={events} />
            : <div className="no-data">No historical data</div>}
        </Card>
      </div>

      <div className="detail-sections">
        <Card delay={.14}><EventTypeBreakdown scores={detail.event_type_scores} /></Card>
        <Card delay={.16}><DailyBrief brief={detail.daily_brief} /></Card>
        <Card delay={.18}><RecentAlerts alerts={detail.alerts_7d} /></Card>
        <Card delay={.20}><TrackBreakdown components={reasoning.track_a_components} /></Card>
        <Card delay={.22}><AgentOutputs agents={detail.agent_outputs} /></Card>
        <Card delay={.24}><ReasoningChains chains={detail.reasoning_chains} narrative={reasoning.narrative} /></Card>
        <Card delay={.26}><ConflictEventSummary data={detail.conflict_events_30d} /></Card>
        <Card delay={.28}><KeyActors actors={detail.key_actors} /></Card>
      </div>
    </motion.div>
  );
}
