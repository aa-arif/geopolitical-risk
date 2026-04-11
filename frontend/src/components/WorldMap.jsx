import React, { useRef, useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import * as d3 from 'd3';
import { motion } from 'framer-motion';
import * as topojson from 'topojson-client';

// ISO numeric to ISO3 mapping for all 30 tracked countries
const ISO_NUM_TO_ISO3 = {
  '566': 'NGA', '050': 'BGD', '586': 'PAK', '608': 'PHL', '792': 'TUR',
  '231': 'ETH', '104': 'MMR', '368': 'IRQ', '170': 'COL', '736': 'SDN',
  '180': 'COD', '818': 'EGY', '764': 'THA', '404': 'KEN', '804': 'UKR',
  '706': 'SOM', '887': 'YEM', '004': 'AFG', '434': 'LBY', '466': 'MLI',
  '508': 'MOZ', '862': 'VEN', '332': 'HTI', '422': 'LBN', '710': 'ZAF',
  '356': 'IND', '484': 'MEX', '562': 'NER', '120': 'CMR', '148': 'TCD',
};

const RISK_COLORS = {
  CRITICAL: '#ef4444',
  HIGH: '#f59e0b',
  ELEVATED: '#eab308',
  LOW: '#22c55e',
};

const RISK_LEVEL_ORDER = ['CRITICAL', 'HIGH', 'ELEVATED', 'LOW'];

const UNMONITORED_FILL = 'rgba(255,255,255,0.03)';
const BORDER_COLOR = 'rgba(255,255,255,0.08)';

const WORLD_ATLAS_URL =
  'https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json';

export default function WorldMap({ countries }) {
  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const tooltipRef = useRef(null);
  const navigate = useNavigate();

  const [worldData, setWorldData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dimensions, setDimensions] = useState({ width: 960, height: 500 });

  // Build a lookup from iso3 -> country data
  const countryLookup = React.useMemo(() => {
    const map = {};
    if (countries) {
      countries.forEach((c) => {
        map[c.iso3] = c;
      });
    }
    return map;
  }, [countries]);

  // Fetch world topojson
  useEffect(() => {
    let cancelled = false;
    fetch(WORLD_ATLAS_URL)
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) {
          setWorldData(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load world atlas:', err);
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Responsive sizing
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const measure = () => {
      const w = container.clientWidth;
      const h = Math.max(300, w * 0.52);
      setDimensions({ width: w, height: h });
    };

    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // Render map with D3
  useEffect(() => {
    if (!worldData || !svgRef.current) return;

    const { width, height } = dimensions;
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const geojsonFeatures = topojson.feature(
      worldData,
      worldData.objects.countries
    );

    const projection = d3
      .geoNaturalEarth1()
      .fitSize([width, height], geojsonFeatures);

    const pathGenerator = d3.geoPath().projection(projection);

    // Defs for glow filter
    const defs = svg.append('defs');
    const glowFilter = defs
      .append('filter')
      .attr('id', 'country-glow')
      .attr('x', '-50%')
      .attr('y', '-50%')
      .attr('width', '200%')
      .attr('height', '200%');
    glowFilter
      .append('feGaussianBlur')
      .attr('stdDeviation', '4')
      .attr('result', 'blur');
    const feMerge = glowFilter.append('feMerge');
    feMerge.append('feMergeNode').attr('in', 'blur');
    feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    // Render countries
    svg
      .append('g')
      .selectAll('path')
      .data(geojsonFeatures.features)
      .join('path')
      .attr('d', pathGenerator)
      .attr('fill', (d) => {
        const numId = String(d.id).padStart(3, '0');
        const iso3 = ISO_NUM_TO_ISO3[numId];
        const countryData = iso3 ? countryLookup[iso3] : null;
        if (countryData && countryData.risk_level) {
          return RISK_COLORS[countryData.risk_level] || UNMONITORED_FILL;
        }
        return UNMONITORED_FILL;
      })
      .attr('stroke', BORDER_COLOR)
      .attr('stroke-width', 0.5)
      .attr('cursor', (d) => {
        const numId = String(d.id).padStart(3, '0');
        const iso3 = ISO_NUM_TO_ISO3[numId];
        return iso3 && countryLookup[iso3] ? 'pointer' : 'default';
      })
      .on('mouseenter', function (event, d) {
        const numId = String(d.id).padStart(3, '0');
        const iso3 = ISO_NUM_TO_ISO3[numId];
        const countryData = iso3 ? countryLookup[iso3] : null;

        if (countryData) {
          d3.select(this)
            .attr('filter', 'url(#country-glow)')
            .attr('stroke', 'rgba(56, 189, 248, 0.4)')
            .attr('stroke-width', 1.5)
            .raise();

          showTooltip(event, countryData);
        }
      })
      .on('mousemove', function (event, d) {
        const numId = String(d.id).padStart(3, '0');
        const iso3 = ISO_NUM_TO_ISO3[numId];
        const countryData = iso3 ? countryLookup[iso3] : null;
        if (countryData) {
          moveTooltip(event);
        }
      })
      .on('mouseleave', function () {
        d3.select(this)
          .attr('filter', null)
          .attr('stroke', BORDER_COLOR)
          .attr('stroke-width', 0.5);

        hideTooltip();
      })
      .on('click', function (event, d) {
        const numId = String(d.id).padStart(3, '0');
        const iso3 = ISO_NUM_TO_ISO3[numId];
        const countryData = iso3 ? countryLookup[iso3] : null;
        if (countryData) {
          navigate(`/country/${iso3}`);
        }
      });
  }, [worldData, dimensions, countryLookup, navigate]);

  // Tooltip helpers
  const showTooltip = useCallback((event, countryData) => {
    const tooltip = tooltipRef.current;
    if (!tooltip) return;

    const prob = countryData.current_probability;
    const probPct =
      prob != null ? (prob * 100).toFixed(1) + '%' : 'N/A';
    const riskLevel = countryData.risk_level || 'UNKNOWN';
    const riskColor = RISK_COLORS[riskLevel] || '#6b7280';

    tooltip.innerHTML = `
      <div style="margin-bottom:6px;font-family:'Inter',sans-serif;font-weight:600;font-size:14px;color:#f1f5f9;">
        ${countryData.name}
      </div>
      <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px;">
        <span style="font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;color:#f8fafc;">
          ${probPct}
        </span>
        <span style="font-family:'Inter',sans-serif;font-size:11px;color:#94a3b8;">probability</span>
      </div>
      <div style="display:inline-block;padding:2px 10px;border-radius:9999px;font-family:'Inter',sans-serif;font-size:11px;font-weight:600;letter-spacing:0.05em;color:#fff;background:${riskColor};">
        ${riskLevel}
      </div>
    `;

    tooltip.style.opacity = '1';
    tooltip.style.pointerEvents = 'none';
    moveTooltip(event);
  }, []);

  const moveTooltip = useCallback((event) => {
    const tooltip = tooltipRef.current;
    const container = containerRef.current;
    if (!tooltip || !container) return;

    const containerRect = container.getBoundingClientRect();
    let x = event.clientX - containerRect.left + 16;
    let y = event.clientY - containerRect.top - 10;

    // Prevent overflow right
    if (x + 200 > containerRect.width) {
      x = event.clientX - containerRect.left - 216;
    }
    // Prevent overflow bottom
    if (y + 120 > containerRect.height) {
      y = event.clientY - containerRect.top - 120;
    }

    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }, []);

  const hideTooltip = useCallback(() => {
    const tooltip = tooltipRef.current;
    if (tooltip) {
      tooltip.style.opacity = '0';
    }
  }, []);

  return (
    <motion.div
      ref={containerRef}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.8, ease: 'easeOut' }}
      style={{ position: 'relative', width: '100%' }}
    >
      {loading ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: 400,
            color: '#64748b',
            fontFamily: "'Inter', sans-serif",
            fontSize: 14,
          }}
        >
          <div style={{ textAlign: 'center' }}>
            <div
              style={{
                width: 32,
                height: 32,
                border: '3px solid rgba(56,189,248,0.2)',
                borderTopColor: '#38bdf8',
                borderRadius: '50%',
                animation: 'spin 1s linear infinite',
                margin: '0 auto 12px',
              }}
            />
            Loading world map...
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
          </div>
        </div>
      ) : (
        <>
          <svg
            ref={svgRef}
            width={dimensions.width}
            height={dimensions.height}
            viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
            style={{ display: 'block', overflow: 'visible' }}
          />

          {/* Tooltip */}
          <div
            ref={tooltipRef}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              opacity: 0,
              transition: 'opacity 0.15s ease',
              background: 'rgba(7, 13, 24, 0.95)',
              border: '1px solid rgba(56, 189, 248, 0.3)',
              borderRadius: 8,
              padding: '12px 16px',
              pointerEvents: 'none',
              zIndex: 50,
              minWidth: 180,
              boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
            }}
          />

          {/* Legend */}
          <div
            style={{
              position: 'absolute',
              bottom: 12,
              left: 12,
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              background: 'rgba(7, 13, 24, 0.7)',
              border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: 8,
              padding: '10px 14px',
              fontFamily: "'Inter', sans-serif",
              fontSize: 11,
              color: '#94a3b8',
            }}
          >
            {RISK_LEVEL_ORDER.map((level) => (
              <div
                key={level}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <div
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: RISK_COLORS[level],
                    flexShrink: 0,
                  }}
                />
                <span>{level.charAt(0) + level.slice(1).toLowerCase()}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </motion.div>
  );
}
