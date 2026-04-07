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

function ReasoningChain({ chain }) {
  const [expanded, setExpanded] = useState(false);
  if (!chain) return null;

  const factors = Array.isArray(chain) ? chain : (chain.factors || chain.causal_factors || []);
  if (factors.length === 0 && typeof chain === 'object' && !Array.isArray(chain)) {
    return (
      <div className="reasoning-chain">
        <h4 className="section-label" onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
          Reasoning Chain {expanded ? '[-]' : '[+]'}
        </h4>
        {expanded && (
          <pre className="reasoning-raw mono">{JSON.stringify(chain, null, 2)}</pre>
        )}
      </div>
    );
  }

  return (
    <div className="reasoning-chain">
      <h4 className="section-label" onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        Reasoning Chain ({factors.length} factors) {expanded ? '[-]' : '[+]'}
      </h4>
      {expanded && (
        <ul className="factor-list">
          {factors.map((f, i) => {
            const label = typeof f === 'string' ? f : (f.factor || f.name || f.description || JSON.stringify(f));
            const weight = typeof f === 'object' ? (f.weight || f.impact || f.score) : null;
            return (
              <li key={i} className="factor-item">
                <span className="factor-label">{label}</span>
                {weight != null && <span className="mono factor-weight">{typeof weight === 'number' ? weight.toFixed(2) : weight}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function TrackBreakdown({ detail }) {
  const trackA = detail.track_a_components || detail.structural_vars || detail.track_a_detail || null;
  if (!trackA) return null;

  const items = Array.isArray(trackA)
    ? trackA
    : Object.entries(trackA).map(([key, val]) => ({ name: key, value: val }));

  return (
    <div className="track-breakdown">
      <h4 className="section-label">Track A Component Breakdown</h4>
      <div className="breakdown-grid">
        {items.map((item, i) => {
          const name = item.name || item.variable || item.label || `Var ${i + 1}`;
          const val = item.value ?? item.score ?? item.weight ?? '';
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
  const entries = Array.isArray(acled) ? acled : (acled.events || acled.summary || []);

  if (typeof acled === 'object' && !Array.isArray(acled) && !acled.events && !acled.summary) {
    return (
      <div className="acled-summary">
        <h4 className="section-label">ACLED Event Data</h4>
        <div className="acled-stats">
          {Object.entries(acled).map(([key, val]) => (
            <div key={key} className="acled-stat">
              <span className="label">{key.replace(/_/g, ' ')}</span>
              <span className="mono">{typeof val === 'number' ? val.toLocaleString() : String(val)}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="acled-summary">
      <h4 className="section-label">ACLED Events ({entries.length})</h4>
      <div className="acled-list">
        {entries.slice(0, 10).map((ev, i) => (
          <div key={i} className="acled-event">
            <span className="mono acled-date">{ev.date || ev.event_date || ''}</span>
            <span className="acled-type">{ev.type || ev.event_type || ev.sub_event_type || ''}</span>
            <span className="acled-desc">{ev.description || ev.notes || ''}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function KeyActors({ actors }) {
  if (!actors || (Array.isArray(actors) && actors.length === 0)) return null;
  const list = Array.isArray(actors) ? actors : [actors];
  return (
    <div className="key-actors">
      <h4 className="section-label">Key Actors</h4>
      <div className="actors-list">
        {list.map((actor, i) => {
          const name = typeof actor === 'string' ? actor : (actor.name || actor.actor || JSON.stringify(actor));
          const role = typeof actor === 'object' ? (actor.role || actor.type || '') : '';
          return (
            <div key={i} className="actor-chip">
              <span className="actor-name">{name}</span>
              {role && <span className="actor-role">{role}</span>}
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
    Promise.all([getCountryDetail(iso3), getCountryHistory(iso3)])
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

  const riskColor = RISK_COLORS[detail.risk_level] || '#888';
  const probability = detail.current_probability ?? detail.probability ?? 0;

  return (
    <div className="country-detail">
      <Link to="/" className="back-link">&larr; Back to Dashboard</Link>

      <div className="detail-header">
        <div>
          <h2 className="detail-country-name">{detail.name || iso3}</h2>
          <span className="detail-iso mono">{iso3}</span>
        </div>
        <span className="risk-badge large" style={{ background: riskColor }}>
          {detail.risk_level}
        </span>
      </div>

      <div className="detail-grid">
        <div className="detail-card gauge-card">
          <h4 className="section-label">Conflict Probability</h4>
          <RiskGauge probability={probability} />
          <div className="gauge-tracks">
            <div className="gauge-track-item">
              <span className="label">Track A</span>
              <span className="mono">{Math.round((detail.track_a ?? 0) * 100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Track B</span>
              <span className="mono">{Math.round((detail.track_b ?? 0) * 100)}%</span>
            </div>
            <div className="gauge-track-item">
              <span className="label">Confidence</span>
              <span className="mono">{Math.round((detail.confidence ?? 0) * 100)}%</span>
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
          <TrackBreakdown detail={detail} />
        </div>

        <div className="detail-card">
          <ReasoningChain chain={detail.reasoning_chain || detail.reasoning_chains || detail.reasoning} />
        </div>

        <div className="detail-card">
          <ACLEDSummary acled={detail.acled || detail.acled_data || detail.acled_summary} />
        </div>

        <div className="detail-card">
          <KeyActors actors={detail.key_actors || detail.actors} />
        </div>
      </div>
    </div>
  );
}
