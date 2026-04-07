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

export function getEvaluation() {
  return fetchJSON(`${BASE}/evaluation`);
}

export function getTrackComparison() {
  return fetchJSON(`${BASE}/evaluation/track-comparison`);
}
