import React, { useRef, useEffect } from 'react';
import * as d3 from 'd3';

export default function CalibrationChart({ data, width = 450, height = 400 }) {
  const ref = useRef();

  useEffect(() => {
    if (!ref.current || !data) return;

    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    // Normalize calibration data into [{predicted, actual, count}] array
    let points = [];
    if (Array.isArray(data)) {
      points = data.map((d) => ({
        predicted: d.predicted ?? d.bin ?? d.forecast ?? d.x ?? 0,
        actual: d.actual ?? d.observed ?? d.frequency ?? d.y ?? 0,
        count: d.count ?? d.n ?? 1,
      }));
    } else if (typeof data === 'object') {
      // Could be {bins: [...], actuals: [...]} or {predicted: [...], actual: [...]}
      const bins = data.bins || data.predicted || Object.keys(data).map(Number).filter((n) => !isNaN(n));
      const actuals = data.actuals || data.actual || data.observed || Object.values(data);
      if (Array.isArray(bins) && Array.isArray(actuals)) {
        points = bins.map((b, i) => ({
          predicted: typeof b === 'number' ? b : parseFloat(b),
          actual: typeof actuals[i] === 'number' ? actuals[i] : parseFloat(actuals[i]),
          count: 1,
        }));
      }
    }

    const margin = { top: 20, right: 30, bottom: 50, left: 55 };
    const w = width - margin.left - margin.right;
    const h = height - margin.top - margin.bottom;

    const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

    const xScale = d3.scaleLinear().domain([0, 1]).range([0, w]);
    const yScale = d3.scaleLinear().domain([0, 1]).range([h, 0]);

    // Grid
    g.append('g')
      .selectAll('line')
      .data(xScale.ticks(5))
      .enter()
      .append('line')
      .attr('x1', (d) => xScale(d))
      .attr('x2', (d) => xScale(d))
      .attr('y1', 0)
      .attr('y2', h)
      .attr('stroke', '#2a3548')
      .attr('stroke-dasharray', '2,4');

    g.append('g')
      .selectAll('line')
      .data(yScale.ticks(5))
      .enter()
      .append('line')
      .attr('x1', 0)
      .attr('x2', w)
      .attr('y1', (d) => yScale(d))
      .attr('y2', (d) => yScale(d))
      .attr('stroke', '#2a3548')
      .attr('stroke-dasharray', '2,4');

    // Perfect calibration line
    g.append('line')
      .attr('x1', xScale(0))
      .attr('y1', yScale(0))
      .attr('x2', xScale(1))
      .attr('y2', yScale(1))
      .attr('stroke', '#546e7a')
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '8,4');

    // Label for perfect calibration
    g.append('text')
      .attr('x', xScale(0.82))
      .attr('y', yScale(0.86))
      .attr('fill', '#546e7a')
      .attr('font-size', '10px')
      .attr('font-family', "'JetBrains Mono', monospace")
      .attr('transform', `rotate(-45, ${xScale(0.82)}, ${yScale(0.86)})`)
      .text('Perfect');

    // Axes
    const xAxis = d3.axisBottom(xScale).ticks(5).tickFormat(d3.format('.0%'));
    const yAxis = d3.axisLeft(yScale).ticks(5).tickFormat(d3.format('.0%'));

    g.append('g')
      .attr('transform', `translate(0,${h})`)
      .call(xAxis)
      .selectAll('text')
      .attr('fill', '#7a8a9e')
      .attr('font-family', "'JetBrains Mono', monospace")
      .attr('font-size', '10px');

    g.append('g')
      .call(yAxis)
      .selectAll('text')
      .attr('fill', '#7a8a9e')
      .attr('font-family', "'JetBrains Mono', monospace")
      .attr('font-size', '10px');

    g.selectAll('.domain').attr('stroke', '#3a4a5e');
    g.selectAll('.tick line').attr('stroke', '#3a4a5e');

    // Axis labels
    svg.append('text')
      .attr('x', margin.left + w / 2)
      .attr('y', height - 8)
      .attr('text-anchor', 'middle')
      .attr('fill', '#7a8a9e')
      .attr('font-size', '12px')
      .attr('font-family', "'JetBrains Mono', monospace")
      .text('Predicted Probability');

    svg.append('text')
      .attr('transform', `rotate(-90)`)
      .attr('x', -(margin.top + h / 2))
      .attr('y', 14)
      .attr('text-anchor', 'middle')
      .attr('fill', '#7a8a9e')
      .attr('font-size', '12px')
      .attr('font-family', "'JetBrains Mono', monospace")
      .text('Observed Frequency');

    if (points.length > 0) {
      // Calibration line
      const lineGen = d3.line()
        .x((d) => xScale(d.predicted))
        .y((d) => yScale(d.actual))
        .curve(d3.curveMonotoneX);

      g.append('path')
        .datum(points.sort((a, b) => a.predicted - b.predicted))
        .attr('fill', 'none')
        .attr('stroke', '#ffd600')
        .attr('stroke-width', 2.5)
        .attr('d', lineGen);

      // Data points
      g.selectAll('.cal-point')
        .data(points)
        .enter()
        .append('circle')
        .attr('cx', (d) => xScale(d.predicted))
        .attr('cy', (d) => yScale(d.actual))
        .attr('r', (d) => Math.max(4, Math.min(10, Math.sqrt(d.count) * 2)))
        .attr('fill', '#ffd600')
        .attr('fill-opacity', 0.8)
        .attr('stroke', '#0f1923')
        .attr('stroke-width', 1.5);
    }
  }, [data, width, height]);

  return (
    <svg ref={ref} width={width} height={height} className="cal-chart" style={{ width: '100%', height: 'auto' }} viewBox={`0 0 ${width} ${height}`} />
  );
}
