import React, { useEffect, useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import { getPredictionSnapshot, getEvaluation, getTrackComparison } from '../api';
import CalibrationChart from '../components/CalibrationChart';

const RISK_COLORS = { CRITICAL: '#ef4444', HIGH: '#f59e0b', ELEVATED: '#eab308', LOW: '#22c55e' };

function AgentSpread({ agents }) {
  if (!agents || Object.keys(agents).length < 2) return <span className="mono" style={{color:'var(--text-2)', fontSize:'.72rem'}}>--</span>;
  const vals = Object.values(agents);
  const spread = (Math.max(...vals) - Math.min(...vals)) * 100;
  const color = spread > 15 ? 'var(--high)' : spread > 8 ? 'var(--elevated)' : 'var(--low)';
  return (
    <div style={{display:'flex', alignItems:'center', gap:'.4rem'}}>
      <div style={{width:'50px', height:'4px', background:'rgba(255,255,255,.04)', borderRadius:'2px', overflow:'hidden'}}>
        <div style={{width:`${Math.min(100, spread*3)}%`, height:'100%', background:color, borderRadius:'2px'}} />
      </div>
      <span className="mono" style={{fontSize:'.68rem', color}}>{spread.toFixed(0)}pp</span>
    </div>
  );
}

export default function Evaluation() {
  const [snapshot, setSnapshot] = useState(null);
  const [selectedDate, setSelectedDate] = useState(null);
  const [evalData, setEvalData] = useState(null);
  const [trackData, setTrackData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sortCol, setSortCol] = useState('calibrated');
  const [sortDir, setSortDir] = useState(-1);
  const [tab, setTab] = useState('live');
  const [sliderLoading, setSliderLoading] = useState(false);

  useEffect(() => {
    Promise.all([
      getPredictionSnapshot(),
      getEvaluation().catch(() => ({n_resolved: 0})),
      getTrackComparison().catch(() => null),
    ]).then(([snap, ev, tr]) => {
      setSnapshot(snap);
      setSelectedDate(snap.date);
      setEvalData(ev);
      setTrackData(tr);
    }).catch(() => {})
    .finally(() => setLoading(false));
  }, []);

  const handleDateChange = (date) => {
    setSelectedDate(date);
    setSliderLoading(true);
    getPredictionSnapshot(date)
        .then(setSnapshot)
        .catch(() => {})
        .finally(() => setSliderLoading(false));
  };

  const sorted = useMemo(() => {
    if (!snapshot?.predictions) return [];
    const preds = [...snapshot.predictions];
    preds.sort((a, b) => {
      const av = a[sortCol] ?? 0, bv = b[sortCol] ?? 0;
      return (av - bv) * sortDir;
    });
    return tab === 'resolved' ? preds.filter(p => p.resolved) : preds;
  }, [snapshot, sortCol, sortDir, tab]);

  const nResolved = evalData?.n_resolved || 0;
  const dates = snapshot?.available_dates || [];

  if (loading) return <div className="loading">Loading predictions...</div>;

  const avgProb = sorted.length > 0 ? sorted.reduce((s, p) => s + (p.calibrated || 0), 0) / sorted.length : 0;
  const maxSpread = sorted.reduce((best, p) => {
    if (!p.agents || Object.keys(p.agents).length < 2) return best;
    const vals = Object.values(p.agents);
    const sp = Math.max(...vals) - Math.min(...vals);
    return sp > best.spread ? {spread: sp, name: p.name} : best;
  }, {spread: 0, name: ''});

  const handleSort = (col) => {
    if (sortCol === col) setSortDir(d => d * -1);
    else { setSortCol(col); setSortDir(-1); }
  };

  const thStyle = (col) => ({
    cursor:'pointer', userSelect:'none',
    color: sortCol === col ? 'var(--cyan)' : undefined,
  });

  return (
    <motion.div initial={{opacity:0}} animate={{opacity:1}} transition={{duration:.3}}>
      <div className="page-header">
        <div>
          <h2>Predictions</h2>
          <span className="subtitle mono">Track, compare, and validate forecasts</span>
        </div>
        {nResolved > 0 && (
          <div className="view-toggle">
            <button className={tab==='live'?'active':''} onClick={()=>setTab('live')}>Live</button>
            <button className={tab==='resolved'?'active':''} onClick={()=>setTab('resolved')}>Resolved</button>
            <button className={tab==='accuracy'?'active':''} onClick={()=>setTab('accuracy')}>Accuracy</button>
          </div>
        )}
      </div>

      {tab !== 'accuracy' && (
        <>
          {/* Date slider */}
          {dates.length > 1 && (
            <div style={{display:'flex', alignItems:'center', gap:'1rem', marginBottom:'1.25rem', padding:'.6rem .8rem', background:'var(--bg-2)', borderRadius:'var(--r-sm)', border:'1px solid var(--border-0)'}}>
              <span className="label">DATE</span>
              <input type="range" min={0} max={dates.length-1}
                value={dates.indexOf(selectedDate) >= 0 ? dates.indexOf(selectedDate) : dates.length-1}
                onChange={e => handleDateChange(dates[parseInt(e.target.value)])}
                style={{flex:1, accentColor:'var(--cyan)'}} />
              <span className="mono" style={{fontSize:'.82rem', fontWeight:700, minWidth:'90px', color:'var(--text-0)'}}>{selectedDate}</span>
            </div>
          )}

          {/* Stats bar */}
          <div style={{display:'flex', gap:'1rem', marginBottom:'1rem', flexWrap:'wrap'}}>
            <span className="stat-pill"><span className="mono" style={{color:'var(--cyan)'}}>{sorted.length}</span> countries</span>
            <span className="stat-pill">Avg <span className="mono" style={{color:'var(--text-0)'}}>{(avgProb*100).toFixed(1)}%</span></span>
            {maxSpread.name && <span className="stat-pill">Max spread <span className="mono" style={{color:'var(--high)'}}>{(maxSpread.spread*100).toFixed(0)}%</span> ({maxSpread.name})</span>}
          </div>

          {/* Prediction table */}
          <div style={{overflowX:'auto', opacity: sliderLoading ? 0.5 : 1, transition: 'opacity 0.2s'}}>
            <table className="eval-table" style={{minWidth:'700px'}}>
              <thead>
                <tr>
                  <th>Country</th>
                  <th style={thStyle('calibrated')} onClick={()=>handleSort('calibrated')}>Calibrated {sortCol==='calibrated'?(sortDir>0?'\u25B2':'\u25BC'):''}</th>
                  <th style={thStyle('track_a')} onClick={()=>handleSort('track_a')}>Track A</th>
                  <th style={thStyle('track_b')} onClick={()=>handleSort('track_b')}>Track B</th>
                  <th>Agent Spread</th>
                  <th>Confidence</th>
                  <th>Window</th>
                  {nResolved > 0 && <th>Outcome</th>}
                  {nResolved > 0 && <th>Brier</th>}
                </tr>
              </thead>
              <tbody>
                {sorted.map(p => {
                  const riskColor = RISK_COLORS[p.risk_level] || '#555';
                  const daysLeft = p.window_end_date ? Math.max(0, Math.ceil((new Date(p.window_end_date) - new Date()) / 86400000)) : null;
                  return (
                    <tr key={p.iso3}>
                      <td style={{fontWeight:600}}>{p.name}</td>
                      <td className="mono" style={{fontWeight:800, fontSize:'.9rem', color:riskColor}}>{p.calibrated!=null?(p.calibrated*100).toFixed(1)+'%':'--'}</td>
                      <td className="mono" style={{color:'var(--cyan)'}}>{p.track_a!=null?(p.track_a*100).toFixed(1)+'%':'--'}</td>
                      <td className="mono" style={{color:'var(--purple)'}}>{p.track_b!=null?(p.track_b*100).toFixed(1)+'%':'--'}</td>
                      <td><AgentSpread agents={p.agents} /></td>
                      <td style={{fontSize:'.72rem', color:'var(--text-1)'}}>{p.confidence||'--'}</td>
                      <td className="mono" style={{fontSize:'.68rem', color:'var(--text-2)'}}>{p.resolved ? 'Closed' : daysLeft!=null ? `${daysLeft}d` : '--'}</td>
                      {nResolved > 0 && <td style={{textAlign:'center'}}>{p.resolved ? (p.actual_outcome===1 ? '\u2718' : '\u2714') : '\u2014'}</td>}
                      {nResolved > 0 && <td className="mono" style={{color: p.brier_score!=null ? (p.brier_score<0.1?'var(--low)':p.brier_score<0.25?'var(--high)':'var(--critical)') : 'var(--text-2)'}}>{p.brier_score!=null?p.brier_score.toFixed(4):'--'}</td>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === 'accuracy' && (
        <div style={{marginTop:'1rem'}}>
          <div className="scores-grid">
            <div className="score-card"><span className="score-label">Resolved</span><span className="score-value mono">{evalData?.n_resolved??0}</span></div>
            <div className="score-card"><span className="score-label">Brier Score</span><span className="score-value mono">{evalData?.brier_aggregate!=null?evalData.brier_aggregate.toFixed(4):'--'}</span></div>
            <div className="score-card"><span className="score-label">Base Rate</span><span className="score-value mono">{evalData?.base_rate!=null?(evalData.base_rate*100).toFixed(1)+'%':'--'}</span></div>
          </div>
          <div className="detail-card" style={{marginBottom:'1rem'}}>
            <h3 style={{fontSize:'.72rem', fontWeight:700, color:'var(--text-2)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:'.7rem'}}>Calibration Curve</h3>
            {evalData?.calibration ? <CalibrationChart data={evalData.calibration} /> : <div className="no-data">No calibration data yet</div>}
          </div>
          {trackData && trackData.n_resolved > 0 && (
            <div className="detail-card">
              <h3 style={{fontSize:'.72rem', fontWeight:700, color:'var(--text-2)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:'.7rem'}}>Track A vs Track B</h3>
              <div className="scores-grid">
                <div className="score-card">
                  <span className="score-label">Track A Brier</span>
                  <span className="score-value mono" style={{color:'var(--cyan)'}}>{trackData.track_a_brier?.toFixed(4) ?? '--'}</span>
                </div>
                <div className="score-card">
                  <span className="score-label">Track B Brier</span>
                  <span className="score-value mono" style={{color:'var(--purple)'}}>{trackData.track_b_brier?.toFixed(4) ?? '--'}</span>
                </div>
                <div className="score-card">
                  <span className="score-label">Fused Brier</span>
                  <span className="score-value mono" style={{color:'#ffd600'}}>{trackData.fused_brier?.toFixed(4) ?? '--'}</span>
                </div>
                <div className="score-card">
                  <span className="score-label">Best Track</span>
                  <span className="score-value mono" style={{color: trackData.best_track === 'track_b' ? 'var(--purple)' : 'var(--cyan)'}}>
                    {trackData.best_track === 'track_a' ? 'Track A' : trackData.best_track === 'track_b' ? 'Track B' : trackData.best_track === 'fused' ? 'Fused' : '--'}
                  </span>
                </div>
              </div>
              {trackData.per_prediction && trackData.per_prediction.length > 0 && (
                <table className="eval-table" style={{marginTop:'.8rem', fontSize:'.75rem'}}>
                  <thead>
                    <tr><th>Country</th><th>Date</th><th>Brier A</th><th>Brier B</th><th>Brier Fused</th><th>Outcome</th></tr>
                  </thead>
                  <tbody>
                    {trackData.per_prediction.map((p, i) => (
                      <tr key={i}>
                        <td>{p.country}</td>
                        <td className="mono">{p.date}</td>
                        <td className="mono" style={{color:'var(--cyan)'}}>{p.brier_a?.toFixed(4)}</td>
                        <td className="mono" style={{color:'var(--purple)'}}>{p.brier_b?.toFixed(4)}</td>
                        <td className="mono" style={{color:'#ffd600'}}>{p.brier_fused?.toFixed(4)}</td>
                        <td style={{textAlign:'center'}}>{p.actual === 1 ? '\u2718' : '\u2714'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}
    </motion.div>
  );
}
