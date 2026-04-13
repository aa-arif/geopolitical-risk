import React, { useRef, useEffect } from 'react';
import * as d3 from 'd3';

export default function TimeSeriesChart({ data, events, width = 600, height = 300 }) {
  const ref = useRef();

  useEffect(() => {
    if (!ref.current || !data || data.length === 0) return;

    const svg = d3.select(ref.current);
    svg.selectAll('*').remove();

    const margin = { top: 20, right: events && events.length > 0 ? 50 : 30, bottom: 40, left: 50 };
    const w = width - margin.left - margin.right;
    const h = height - margin.top - margin.bottom;

    const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

    // Parse data
    const parsed = data.map((d) => ({
      date: new Date(d.date),
      track_a: d.track_a,
      track_b: d.track_b,
      fused: d.fused ?? d.probability,
      actual: d.actual,
      resolved: d.resolved,
    }));

    parsed.sort((a, b) => a.date - b.date);

    // Combined x domain from predictions and events
    let xDomainDates = parsed.map(d => d.date);
    if (events && events.length > 0) {
      xDomainDates = xDomainDates.concat(events.map(e => new Date(e.date)));
    }

    const xScale = d3.scaleTime()
      .domain(d3.extent(xDomainDates))
      .range([0, w]);

    const yScale = d3.scaleLinear()
      .domain([0, 1])
      .range([h, 0]);

    // Grid lines
    g.append('g')
      .attr('class', 'grid')
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

    // Axes
    const xAxis = d3.axisBottom(xScale).ticks(6).tickFormat(d3.timeFormat('%b %d'));
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

    // Event overlay bars (drawn first so lines appear on top)
    if (events && events.length > 0) {
      const eventMax = d3.max(events, d => d.events) || 1;
      const yEvent = d3.scaleLinear().domain([0, eventMax]).range([h, 0]);

      // Right axis
      const rightAxis = d3.axisRight(yEvent).ticks(3).tickFormat(d3.format('d'));
      g.append('g').attr('transform', `translate(${w}, 0)`)
        .call(rightAxis)
        .selectAll('text').attr('fill', '#6b7280').attr('font-size', '9px');

      g.append('g').attr('transform', `translate(${w}, 0)`)
        .selectAll('.domain').attr('stroke', '#3a4a5e');

      // Bars
      const barWidth = Math.max(1, w / events.length - 1);
      g.selectAll('.event-bar')
        .data(events)
        .join('rect')
        .attr('class', 'event-bar')
        .attr('x', d => xScale(new Date(d.date)) - barWidth/2)
        .attr('y', d => yEvent(d.events))
        .attr('width', barWidth)
        .attr('height', d => h - yEvent(d.events))
        .attr('fill', d => d.fatalities > 0 ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.12)')
        .attr('rx', 1);
    }

    // Line generators
    const lineGen = (accessor) =>
      d3.line()
        .defined((d) => accessor(d) != null)
        .x((d) => xScale(d.date))
        .y((d) => yScale(accessor(d)))
        .curve(d3.curveMonotoneX);

    // Track A line
    if (parsed.some((d) => d.track_a != null)) {
      g.append('path')
        .datum(parsed)
        .attr('fill', 'none')
        .attr('stroke', '#4fc3f7')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '6,3')
        .attr('d', lineGen((d) => d.track_a));
    }

    // Track B line
    if (parsed.some((d) => d.track_b != null)) {
      g.append('path')
        .datum(parsed)
        .attr('fill', 'none')
        .attr('stroke', '#ce93d8')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '6,3')
        .attr('d', lineGen((d) => d.track_b));
    }

    // Fused line
    if (parsed.some((d) => d.fused != null)) {
      g.append('path')
        .datum(parsed)
        .attr('fill', 'none')
        .attr('stroke', '#ffd600')
        .attr('stroke-width', 2.5)
        .attr('d', lineGen((d) => d.fused));
    }

    // Actual outcome markers
    parsed.filter((d) => d.resolved && d.actual != null).forEach((d) => {
      g.append('circle')
        .attr('cx', xScale(d.date))
        .attr('cy', yScale(d.actual))
        .attr('r', 5)
        .attr('fill', d.actual >= 0.5 ? '#ff3b3b' : '#00e676')
        .attr('stroke', '#0f1923')
        .attr('stroke-width', 2);
    });

    // Legend
    const legend = svg.append('g').attr('transform', `translate(${margin.left + 10}, 10)`);
    const items = [
      { label: 'Track A', color: '#4fc3f7', dash: '6,3' },
      { label: 'Track B', color: '#ce93d8', dash: '6,3' },
      { label: 'Fused', color: '#ffd600', dash: null },
    ];

    if (events && events.length > 0) {
      items.push({ label: 'Events', color: 'rgba(239, 68, 68, 0.5)', dash: null, isRect: true });
    }

    items.forEach((item, i) => {
      const lg = legend.append('g').attr('transform', `translate(${i * 100}, 0)`);
      if (item.isRect) {
        lg.append('rect')
          .attr('x', 0).attr('y', -5).attr('width', 20).attr('height', 10)
          .attr('fill', item.color)
          .attr('rx', 2);
      } else {
        lg.append('line')
          .attr('x1', 0).attr('x2', 20).attr('y1', 0).attr('y2', 0)
          .attr('stroke', item.color)
          .attr('stroke-width', 2)
          .attr('stroke-dasharray', item.dash);
      }
      lg.append('text')
        .attr('x', 24).attr('y', 4)
        .attr('fill', '#b0bec5')
        .attr('font-size', '11px')
        .attr('font-family', "'JetBrains Mono', monospace")
        .text(item.label);
    });
  }, [data, events, width, height]);

  return (
    <svg ref={ref} width={width} height={height} className="ts-chart" style={{ width: '100%', height: 'auto' }} viewBox={`0 0 ${width} ${height}`} />
  );
}
