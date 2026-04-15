const BASE = '/api';

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function getCountries() {
  return fetchJSON(`${BASE}/countries`);
}

export function getCountryDetail(iso3) {
  return fetchJSON(`${BASE}/countries/${iso3}`);
}

export function getCountryHistory(iso3) {
  return fetchJSON(`${BASE}/countries/${iso3}/history`);
}

export function getCountryEvents(iso3, days = 60) {
  return fetchJSON(`${BASE}/countries/${iso3}/events?days=${days}`);
}

export function getPredictionSnapshot(date) {
  const params = date ? `?date=${date}` : '';
  return fetchJSON(`${BASE}/predictions/snapshot${params}`);
}

export function getEvaluation() {
  return fetchJSON(`${BASE}/evaluation`);
}

export function getTrackComparison() {
  return fetchJSON(`${BASE}/evaluation/track-comparison`);
}

export function getAlerts(days = 30) {
  return fetchJSON(`${BASE}/alerts?days=${days}`);
}

export function getBriefs() {
  return fetchJSON(`${BASE}/briefs/latest`);
}

export function getVertical(name) {
  return fetchJSON(`${BASE}/verticals/${name}`);
}
