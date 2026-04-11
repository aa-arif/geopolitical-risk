import React from 'react';
import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { AnimatePresence } from 'framer-motion';
import Dashboard from './pages/Dashboard';
import CountryDetail from './pages/CountryDetail';
import Evaluation from './pages/Evaluation';

export default function App() {
  const location = useLocation();
  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <h1 className="logo">GEO<span className="logo-accent">RISK</span></h1>
          <span className="logo-sub">Intelligence Platform</span>
        </div>
        <nav className="main-nav">
          <NavLink to="/" end className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>Dashboard</NavLink>
          <NavLink to="/evaluation" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>Evaluation</NavLink>
        </nav>
      </header>
      <main className="app-main">
        <AnimatePresence mode="wait">
          <Routes location={location} key={location.pathname}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/country/:iso3" element={<CountryDetail />} />
            <Route path="/evaluation" element={<Evaluation />} />
          </Routes>
        </AnimatePresence>
      </main>
    </div>
  );
}
