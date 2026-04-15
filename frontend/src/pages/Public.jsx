import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { getCountries, getEvaluation } from '../api';

const RISK_COLORS = { CRITICAL: '#ef4444', HIGH: '#f59e0b', ELEVATED: '#eab308', LOW: '#22c55e' };

function RiskBar({ value, color }) {
  const pct = Math.round(value * 100);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem', flex: 1 }}>
      <div style={{ flex: 1, height: '4px', background: 'rgba(255,255,255,.06)', borderRadius: '2px', overflow: 'hidden' }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1, ease: [.4, 0, .2, 1], delay: .1 }}
          style={{ height: '100%', borderRadius: '2px', background: `linear-gradient(90deg, ${color}66, ${color})` }}
        />
      </div>
      <span className="mono" style={{ color, fontWeight: 700, fontSize: '1rem', minWidth: '3rem', textAlign: 'right' }}>
        {pct}%
      </span>
    </div>
  );
}

export default function Public() {
  const [countries, setCountries] = useState([]);
  const [evaluation, setEvaluation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([
      getCountries(),
      getEvaluation().catch(() => null),
    ])
      .then(([countryData, evalData]) => {
        setCountries(countryData.countries || []);
        setEvaluation(evalData);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-0)' }}>
      <div className="mono" style={{ color: 'var(--text-2)', fontSize: '.85rem' }}>Loading predictions...</div>
    </div>
  );

  if (error) return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-0)' }}>
      <div style={{ color: 'var(--critical)', fontSize: '.85rem' }}>Error: {error}</div>
    </div>
  );

  const latestDate = countries.length > 0 ? countries[0].prediction_date : null;
  const resolvedCount = countries.filter(c => c.resolved).length;
  const brierScore = evaluation?.brier_aggregate?.overall;
  const nResolved = evaluation?.n_resolved || 0;
  const highCount = countries.filter(c => c.risk_level === 'HIGH' || c.risk_level === 'CRITICAL').length;

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-0)', color: 'var(--text-0)' }}>
      {/* Header */}
      <header style={{
        padding: '2rem 2rem 0', maxWidth: '960px', margin: '0 auto',
      }}>
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .5 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '.75rem', marginBottom: '.5rem' }}>
            <h1 style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: '1.3rem', fontWeight: 800,
              letterSpacing: '.12em',
            }}>
              PRECURSION
            </h1>
          </div>
          <h2 style={{
            fontSize: '1.1rem', fontWeight: 700, lineHeight: 1.4, color: 'var(--text-0)',
            marginBottom: '.6rem', letterSpacing: '-0.01em',
          }}>
            Daily Geopolitical Risk Forecasts, Measured Against Reality
          </h2>
          <p style={{
            fontSize: '.78rem', lineHeight: 1.6, color: 'var(--text-1)', maxWidth: '640px',
            marginBottom: '1.5rem',
          }}>
            Calibrated 30-day violence probability forecasts for {countries.length} countries.
            We publish our predictions openly and score our accuracy with Brier scores.
            Nobody else does this.
          </p>
        </motion.div>

        {/* Stats row */}
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: .2 }}
          style={{
            display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '2rem',
          }}
        >
          <div style={{
            padding: '.6rem 1rem', background: 'var(--bg-2)', border: '1px solid var(--border-0)',
            borderRadius: 'var(--r-sm)', minWidth: '120px',
          }}>
            <div style={{ fontSize: '.58rem', color: 'var(--text-2)', textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 600, marginBottom: '.2rem' }}>
              Countries
            </div>
            <div className="mono" style={{ fontSize: '1.3rem', fontWeight: 800, color: 'var(--cyan)' }}>
              {countries.length}
            </div>
          </div>

          {highCount > 0 && (
            <div style={{
              padding: '.6rem 1rem', background: 'rgba(245,158,11,.06)', border: '1px solid rgba(245,158,11,.15)',
              borderRadius: 'var(--r-sm)', minWidth: '120px',
            }}>
              <div style={{ fontSize: '.58rem', color: 'var(--text-2)', textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 600, marginBottom: '.2rem' }}>
                High Risk
              </div>
              <div className="mono" style={{ fontSize: '1.3rem', fontWeight: 800, color: '#f59e0b' }}>
                {highCount}
              </div>
            </div>
          )}

          <div style={{
            padding: '.6rem 1rem', background: 'var(--bg-2)', border: '1px solid var(--border-0)',
            borderRadius: 'var(--r-sm)', minWidth: '120px',
          }}>
            <div style={{ fontSize: '.58rem', color: 'var(--text-2)', textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 600, marginBottom: '.2rem' }}>
              Brier Score
            </div>
            {nResolved > 0 && brierScore != null ? (
              <>
                <div className="mono" style={{ fontSize: '1.3rem', fontWeight: 800, color: 'var(--cyan)' }}>
                  {brierScore.toFixed(3)}
                </div>
                <div className="mono" style={{ fontSize: '.55rem', color: 'var(--text-2)' }}>
                  {nResolved} resolved
                </div>
              </>
            ) : (
              <div style={{ fontSize: '.72rem', color: 'var(--text-2)', lineHeight: 1.4 }}>
                Pending &mdash; first predictions resolve May 2026
              </div>
            )}
          </div>

          {latestDate && (
            <div style={{
              padding: '.6rem 1rem', background: 'var(--bg-2)', border: '1px solid var(--border-0)',
              borderRadius: 'var(--r-sm)', minWidth: '120px',
            }}>
              <div style={{ fontSize: '.58rem', color: 'var(--text-2)', textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 600, marginBottom: '.2rem' }}>
                Last Updated
              </div>
              <div className="mono" style={{ fontSize: '.85rem', fontWeight: 700, color: 'var(--text-0)' }}>
                {latestDate}
              </div>
            </div>
          )}
        </motion.div>
      </header>

      {/* Country table */}
      <main style={{ maxWidth: '960px', margin: '0 auto', padding: '0 2rem 3rem' }}>
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .3, duration: .5 }}>
          <div style={{
            background: 'var(--bg-2)', border: '1px solid var(--border-0)',
            borderRadius: 'var(--r-md)', overflow: 'hidden',
          }}>
            {/* Table header */}
            <div style={{
              display: 'grid', gridTemplateColumns: '2fr 1fr 3fr 80px',
              padding: '.6rem 1.2rem', borderBottom: '1px solid var(--border-1)',
              fontSize: '.6rem', fontWeight: 700, color: 'var(--text-2)',
              textTransform: 'uppercase', letterSpacing: '.08em',
            }}>
              <span>Country</span>
              <span>Risk Level</span>
              <span>Violence Probability (30d)</span>
              <span style={{ textAlign: 'right' }}>Window</span>
            </div>

            {/* Rows */}
            {countries.map((c, i) => {
              const prob = c.current_probability ?? 0;
              const riskColor = RISK_COLORS[c.risk_level] || '#555';
              const daysLeft = c.window_end ? Math.max(0, Math.ceil((new Date(c.window_end) - new Date()) / 86400000)) : null;

              return (
                <motion.div
                  key={c.iso3}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: .35 + i * 0.03 }}
                  style={{
                    display: 'grid', gridTemplateColumns: '2fr 1fr 3fr 80px',
                    padding: '.7rem 1.2rem', alignItems: 'center',
                    borderBottom: i < countries.length - 1 ? '1px solid var(--border-0)' : 'none',
                    transition: 'background .15s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,.02)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '.82rem' }}>{c.name}</div>
                    <div className="mono" style={{ fontSize: '.6rem', color: 'var(--text-2)' }}>{c.iso3}</div>
                  </div>

                  <div>
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: '.55rem', fontWeight: 700, letterSpacing: '.1em',
                      padding: '.15rem .5rem', borderRadius: '4px',
                      background: riskColor, color: '#03060d',
                    }}>
                      {c.risk_level || 'N/A'}
                    </span>
                  </div>

                  <RiskBar value={prob} color={riskColor} />

                  <div className="mono" style={{ fontSize: '.6rem', color: 'var(--text-2)', textAlign: 'right' }}>
                    {c.resolved
                      ? (c.actual_outcome === 1 ? 'ESCALATED' : 'RESOLVED')
                      : daysLeft != null ? `${daysLeft}d left` : '--'}
                  </div>
                </motion.div>
              );
            })}
          </div>
        </motion.div>

        {/* How We're Different */}
        <motion.div
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .5, duration: .5 }}
          style={{ marginTop: '2.5rem', marginBottom: '2rem' }}
        >
          <h3 style={{
            fontSize: '.65rem', fontWeight: 700, color: 'var(--text-2)',
            textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: '1rem', textAlign: 'center',
          }}>
            How We're Different
          </h3>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '.8rem',
          }}>
            <div style={{
              padding: '1.1rem', background: 'var(--bg-2)', border: '1px solid var(--border-0)',
              borderRadius: 'var(--r-sm)',
            }}>
              <div style={{ fontSize: '.8rem', fontWeight: 700, color: 'var(--text-0)', marginBottom: '.4rem' }}>
                Probabilistic, not qualitative
              </div>
              <p style={{ fontSize: '.72rem', lineHeight: 1.6, color: 'var(--text-1)', margin: 0 }}>
                We assign actual numbers, not vague risk levels. 37% means 37%.
              </p>
            </div>
            <div style={{
              padding: '1.1rem', background: 'var(--bg-2)', border: '1px solid var(--border-0)',
              borderRadius: 'var(--r-sm)',
            }}>
              <div style={{ fontSize: '.8rem', fontWeight: 700, color: 'var(--text-0)', marginBottom: '.4rem' }}>
                Publicly scored
              </div>
              <p style={{ fontSize: '.72rem', lineHeight: 1.6, color: 'var(--text-1)', margin: 0 }}>
                When our 30-day windows close, we publish Brier scores. If we're wrong, you'll see it.
              </p>
            </div>
            <div style={{
              padding: '1.1rem', background: 'var(--bg-2)', border: '1px solid var(--border-0)',
              borderRadius: 'var(--r-sm)',
            }}>
              <div style={{ fontSize: '.8rem', fontWeight: 700, color: 'var(--text-0)', marginBottom: '.4rem' }}>
                Two independent models
              </div>
              <p style={{ fontSize: '.72rem', lineHeight: 1.6, color: 'var(--text-1)', margin: 0 }}>
                A structural model built on political science research and an AI reasoning engine that reads today's news. They check each other.
              </p>
            </div>
          </div>
        </motion.div>

        {/* Footer */}
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: .6 }}
          style={{
            padding: '1.2rem', textAlign: 'center',
            borderTop: '1px solid var(--border-0)',
          }}
        >
          <div style={{ display: 'flex', gap: '1.5rem', justifyContent: 'center', alignItems: 'center', marginBottom: '.8rem' }}>
            <a
              href="https://precursion.substack.com"
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: '.4rem',
                padding: '.5rem 1.2rem', background: 'var(--cyan-dim)',
                border: '1px solid rgba(56,189,248,.2)', borderRadius: 'var(--r-sm)',
                color: 'var(--cyan)', fontSize: '.75rem', fontWeight: 600,
                transition: 'all .2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(56,189,248,.15)'; e.currentTarget.style.borderColor = 'rgba(56,189,248,.4)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'var(--cyan-dim)'; e.currentTarget.style.borderColor = 'rgba(56,189,248,.2)'; }}
            >
              Follow the Instability Monitor &rarr;
            </a>
            <Link to="/" style={{ fontSize: '.72rem', color: 'var(--text-2)' }}>
              Full Dashboard
            </Link>
          </div>
          <p style={{ fontSize: '.6rem', color: 'var(--text-2)' }}>
            Updated daily. Structural model + AI ensemble with 4 independent forecasting agents.
          </p>
        </motion.div>
      </main>
    </div>
  );
}
