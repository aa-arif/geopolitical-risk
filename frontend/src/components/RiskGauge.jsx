import React, { useRef, useEffect } from 'react';
import * as d3 from 'd3';

export default function RiskGauge({ probability, width = 240, height = 150 }) {
  const ref = useRef();

  useEffect(() => {
    if (ref.current == null) return;

    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const pct = Math.max(0, Math.min(1, probability));
    const margin = 20;
    const radius = Math.min(width / 2 - margin, height - margin - 30);
    const cx = width / 2;
    const cy = height - 20;

    const g = svg.append('g').attr('transform', `translate(${cx},${cy})`);

    // Color gradient stops
    const colorScale = d3.scaleLinear()
      .domain([0, 0.25, 0.5, 0.75, 1])
      .range(['#00e676', '#a0e600', '#ffd600', '#ff8c00', '#ff3b3b']);

    // Background arc segments
    const numSegments = 50;
    const arcGen = d3.arc().innerRadius(radius - 18).outerRadius(radius);

    for (let i = 0; i < numSegments; i++) {
      const t = i / numSegments;
      const startAngle = -Math.PI / 2 + (t * Math.PI);
      const endAngle = -Math.PI / 2 + ((i + 1) / numSegments * Math.PI);
      g.append('path')
        .attr('d', arcGen({ startAngle, endAngle }))
        .attr('fill', colorScale(t))
        .attr('opacity', 0.2);
    }

    // Filled arc segments up to probability
    const filledSegments = Math.round(pct * numSegments);
    for (let i = 0; i < filledSegments; i++) {
      const t = i / numSegments;
      const startAngle = -Math.PI / 2 + (t * Math.PI);
      const endAngle = -Math.PI / 2 + ((i + 1) / numSegments * Math.PI);
      g.append('path')
        .attr('d', arcGen({ startAngle, endAngle }))
        .attr('fill', colorScale(t))
        .attr('opacity', 1);
    }

    // Needle
    const needleAngle = -Math.PI / 2 + pct * Math.PI;
    const needleLen = radius - 25;
    const nx = Math.cos(needleAngle) * needleLen;
    const ny = Math.sin(needleAngle) * needleLen;

    g.append('line')
      .attr('x1', 0)
      .attr('y1', 0)
      .attr('x2', nx)
      .attr('y2', ny)
      .attr('stroke', '#e0e0e0')
      .attr('stroke-width', 2.5)
      .attr('stroke-linecap', 'round');

    g.append('circle')
      .attr('cx', 0)
      .attr('cy', 0)
      .attr('r', 5)
      .attr('fill', '#e0e0e0');

    // Value text
    g.append('text')
      .attr('x', 0)
      .attr('y', -radius / 3)
      .attr('text-anchor', 'middle')
      .attr('fill', colorScale(pct))
      .attr('font-size', '28px')
      .attr('font-family', "'JetBrains Mono', monospace")
      .attr('font-weight', '700')
      .text(`${Math.round(pct * 100)}%`);

    // Min/max labels
    g.append('text')
      .attr('x', -radius)
      .attr('y', 14)
      .attr('text-anchor', 'middle')
      .attr('fill', '#7a8a9e')
      .attr('font-size', '11px')
      .attr('font-family', "'JetBrains Mono', monospace")
      .text('0%');

    g.append('text')
      .attr('x', radius)
      .attr('y', 14)
      .attr('text-anchor', 'middle')
      .attr('fill', '#7a8a9e')
      .attr('font-size', '11px')
      .attr('font-family', "'JetBrains Mono', monospace")
      .text('100%');
  }, [probability, width, height]);

  return (
    <svg ref={ref} width={width} height={height} className="risk-gauge" />
  );
}
