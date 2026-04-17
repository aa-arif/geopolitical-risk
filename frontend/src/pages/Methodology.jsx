import React from 'react';
import { motion } from 'framer-motion';

const sectionStyle = {
  background: 'var(--bg-2)', border: '1px solid var(--border-0)',
  borderRadius: '10px', padding: '1.3rem', marginBottom: '1.25rem',
};
const headingStyle = {
  fontSize: '.82rem', fontWeight: 700, color: '#e2e8f0',
  marginBottom: '.65rem', letterSpacing: '-.01em',
};
const bodyStyle = { fontSize: '.78rem', lineHeight: '1.7', color: '#8b9db5' };
const codeStyle = {
  display: 'block', background: 'var(--bg-0)', border: '1px solid var(--border-0)',
  borderRadius: '6px', padding: '.75rem 1rem', margin: '.65rem 0',
  fontFamily: "'JetBrains Mono', monospace", fontSize: '.72rem',
  color: '#4fc3f7', lineHeight: '1.7', overflowX: 'auto',
};
const labelStyle = {
  display: 'inline-block', fontSize: '.6rem', fontWeight: 700,
  letterSpacing: '.08em', textTransform: 'uppercase', padding: '.15rem .45rem',
  borderRadius: '3px', marginRight: '.5rem',
};

export default function Methodology() {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: .3 }}>
      <div className="page-header">
        <div>
          <h2>Methodology</h2>
          <span className="subtitle mono">How predictions are generated, evaluated, and improved</span>
        </div>
      </div>

      {/* What We Predict */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>What We Predict</h3>
        <div style={bodyStyle}>
          <p>Each country receives a daily probability estimate answering one question:</p>
          <div style={{ ...codeStyle, color: '#e2e8f0', fontSize: '.78rem' }}>
            "What is the probability that this country experiences a significant escalation<br/>
            in political violence within the next 30 days?"
          </div>
          <p style={{ marginTop: '.5rem' }}>
            <strong>Political violence</strong> is defined as battles between armed groups, explosions or remote violence
            (bombings, shelling, IEDs), and direct violence against civilians -- events where people are killed.
            This excludes peaceful protests, strategic developments, and diplomatic events.
          </p>
          <p style={{ marginTop: '.5rem' }}>
            <strong>Significant escalation</strong> means the monthly count of fatal violent events exceeds the country's
            own 90th-percentile historical baseline, computed from the prior 12 months. The threshold is committed
            at prediction time and never retroactively adjusted.
          </p>
        </div>
      </div>

      {/* Dual-Model Architecture */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Dual-Model Architecture</h3>
        <div style={bodyStyle}>
          <p>Every prediction combines two independent analytical approaches:</p>

          <div style={{ marginTop: '.75rem' }}>
            <span style={{ ...labelStyle, background: 'rgba(56,189,248,.12)', color: '#38bdf8' }}>Structural Model</span>
            <span style={{ fontSize: '.72rem', color: '#94a3b8' }}>(formerly "Track A")</span>
          </div>
          <p style={{ marginTop: '.3rem' }}>
            A quantitative model inspired by the Political Instability Task Force (Goldstone et al., 2010).
            Uses three core inputs -- <strong>regime type</strong> (Polity V score), <strong>infant mortality</strong>
            (proxy for state capacity), and <strong>years since last instability</strong> -- combined in logit space
            with modifiers for structural vulnerability (Fearon &amp; Laitin, 2003), neighborhood conflict contagion
            (ACLED data from bordering countries), and the Caldara-Iacoviello Geopolitical Risk Index trend.
          </p>
          <p style={{ marginTop: '.35rem' }}>
            Runs in milliseconds with no API calls. Updates are driven by changes in ACLED conflict data
            and GPR index movements. Structural variables (polity, GDP, infant mortality) are updated annually.
          </p>

          <div style={{ marginTop: '.9rem' }}>
            <span style={{ ...labelStyle, background: 'rgba(167,139,250,.12)', color: '#a78bfa' }}>AI Analysis</span>
            <span style={{ fontSize: '.72rem', color: '#94a3b8' }}>(formerly "Track B")</span>
          </div>
          <p style={{ marginTop: '.3rem' }}>
            An ensemble of four AI agents, each using a different reasoning strategy from the superforecasting
            literature (Tetlock &amp; Gardner, 2015):
          </p>
          <ul style={{ paddingLeft: '1.2rem', marginTop: '.3rem' }}>
            <li><strong>Base Rate Agent</strong> -- anchors to the structural probability and adjusts based on current evidence (CHAMPS KNOW methodology)</li>
            <li><strong>Historical Analogy Agent</strong> -- identifies historical parallels from other countries and time periods</li>
            <li><strong>Decomposition Agent</strong> -- breaks the question into sub-components and estimates each independently</li>
            <li><strong>Devil's Advocate Agent</strong> -- challenges the consensus of the other three agents with contrarian arguments</li>
          </ul>
          <p style={{ marginTop: '.35rem' }}>
            A supervisor model reconciles the four agent outputs into a single AI Analysis probability
            with an executive summary and confidence assessment. All agents receive current news articles,
            ACLED conflict data, and extracted causal reasoning chains as input.
          </p>
        </div>
      </div>

      {/* Fusion */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Fusion and Calibration</h3>
        <div style={bodyStyle}>
          <p>The Structural Model and AI Analysis probabilities are combined via weighted average:</p>
          <div style={codeStyle}>
            P(combined) = w * P(structural) + (1-w) * P(ai_analysis)<br/>
            Default weight: w = 0.60 (adapts over time based on track record)
          </div>
          <p style={{ marginTop: '.35rem' }}>
            The weight automatically adjusts toward whichever model has better historical accuracy (measured by Brier score),
            with conservative updates (max 5 percentage points per evaluation cycle) and bounds of [0.30, 0.80].
          </p>
          <p style={{ marginTop: '.35rem' }}>
            After fusion, the probability passes through optional Platt scaling calibration (logistic regression
            fitted on resolved predictions). Calibration activates once 30+ predictions have resolved with known outcomes.
          </p>
        </div>
      </div>

      {/* Resolution */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>How Predictions Are Resolved</h3>
        <div style={bodyStyle}>
          <p>
            After each 30-day prediction window closes, we check whether the predicted outcome occurred:
          </p>
          <ul style={{ paddingLeft: '1.2rem', marginTop: '.35rem' }}>
            <li><strong>Primary source:</strong> ACLED armed conflict data, filtered to battles, explosions/remote violence,
              and violence against civilians with at least one fatality. These categories are deliberately separate
              from the broader ACLED data used as model input (which includes protests, riots, strategic developments).</li>
            <li><strong>Threshold:</strong> The 90th percentile of the country's own monthly violent event count
              over the prior 12 months, committed and stored at prediction time (never retroactively adjusted).</li>
            <li><strong>Fallback:</strong> When ACLED data is unavailable, GDELT conflict article volume is used
              with a separate threshold.</li>
          </ul>
          <p style={{ marginTop: '.35rem' }}>
            Accuracy is measured using the <strong>Brier score</strong> (0 = perfect, 1 = worst possible).
            A Brier score below the base rate's naive forecast indicates the model adds value beyond
            always predicting the historical average.
          </p>
        </div>
      </div>

      {/* Ingest Confidence */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Ingest Confidence Flag</h3>
        <div style={bodyStyle}>
          <p>
            Every resolution is tagged with an ingest confidence level. We compute the average daily ingest
            volume during the 30-day prediction window and compare it to the 90-day baseline preceding the
            window. If the window's volume falls within &plusmn;2 standard deviations of the baseline, the
            resolution is marked <strong>high confidence</strong>. Otherwise it is marked
            <strong> low confidence</strong> and reported separately. For countries with sparse baseline
            data (fewer than 60 non-zero days), we mark the resolution as <strong>unknown confidence</strong>.
          </p>
          <p style={{ marginTop: '.5rem' }}>
            This flag does not change the Brier score calculation itself. It lets us publish a headline
            score that excludes resolutions where our underlying data surface shifted meaningfully during
            the window, while still reporting all-in scores alongside for transparency.
          </p>
          <p style={{ marginTop: '.5rem' }}>
            The deviation is measured on <code style={{ color: '#4fc3f7' }}>pulled_at</code> (when our
            pipeline recorded the event) rather than <code style={{ color: '#4fc3f7' }}>event_date</code>
            (when it happened on the ground), so the flag detects pipeline degradation -- GDELT rate
            limiting, LLM classification drift, NewsAPI outages -- rather than real-world quiet periods.
          </p>
        </div>
      </div>

      {/* Data Sources */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Data Sources</h3>
        <div style={bodyStyle}>
          <ul style={{ paddingLeft: '1.2rem' }}>
            <li><strong>ACLED</strong> (Armed Conflict Location &amp; Event Data Project) -- real-time conflict event data, updated weekly. Used for event counts, fatality tracking, and neighborhood contagion.</li>
            <li><strong>GDELT</strong> (Global Database of Events, Language, and Tone) -- conflict-related article monitoring. Fallback for countries with limited ACLED coverage.</li>
            <li><strong>NewsAPI / RSS feeds</strong> -- current news articles from major outlets and country-specific sources, processed for causal reasoning extraction.</li>
            <li><strong>Polity V</strong> -- regime type classification (Center for Systemic Peace). Updated annually.</li>
            <li><strong>V-Dem</strong> (Varieties of Democracy) -- liberal democracy index. Updated annually.</li>
            <li><strong>GPR Index</strong> (Caldara-Iacoviello, Federal Reserve Board) -- geopolitical risk trends from newspaper analysis.</li>
            <li><strong>World Bank</strong> -- GDP per capita, infant mortality, population data.</li>
          </ul>
        </div>
      </div>

      {/* Limitations */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Limitations</h3>
        <div style={bodyStyle}>
          <ul style={{ paddingLeft: '1.2rem' }}>
            <li>The structural model uses coefficients inspired by PITF published odds ratios, rescaled for 30-day windows. This is an approximation, not the original PITF model fitted on classified data.</li>
            <li>Structural variables (polity score, GDP, infant mortality) update annually and may lag reality after sudden regime changes.</li>
            <li>AI agents can produce overconfident or poorly-calibrated outputs. The 50pp deviation limit prevents extreme hallucination but limits detection of genuine black swan events.</li>
            <li>Calibration requires 30+ resolved predictions to activate. Before that, probabilities are uncalibrated.</li>
            <li>Currently monitoring 15 countries. Coverage is focused on high-priority emerging markets and conflict-affected states.</li>
            <li>The platform predicts political violence escalation specifically. It does not directly predict economic crises, expropriation, regime change, or other peril types.</li>
          </ul>
        </div>
      </div>

      {/* References */}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>References</h3>
        <div style={{ fontSize: '.72rem', lineHeight: '1.7', color: '#6b7f99', fontStyle: 'italic' }}>
          <p>Goldstone, J. et al. (2010). "A Global Model for Forecasting Political Instability." <em>American Journal of Political Science</em>, 54(1), 190-208.</p>
          <p style={{ marginTop: '.3rem' }}>Fearon, J. &amp; Laitin, D. (2003). "Ethnicity, Insurgency, and Civil War." <em>American Political Science Review</em>, 97(1), 75-90.</p>
          <p style={{ marginTop: '.3rem' }}>Tetlock, P. &amp; Gardner, D. (2015). <em>Superforecasting: The Art and Science of Prediction.</em> Crown.</p>
          <p style={{ marginTop: '.3rem' }}>Satopaa, V. et al. (2014). "Combining Multiple Probability Predictions Using a Simple Logit Model." <em>International Journal of Forecasting</em>, 30(2), 344-356.</p>
          <p style={{ marginTop: '.3rem' }}>Caldara, D. &amp; Iacoviello, M. (2022). "Measuring Geopolitical Risk." <em>American Economic Review</em>, 112(4), 1194-1225.</p>
        </div>
      </div>
    </motion.div>
  );
}
