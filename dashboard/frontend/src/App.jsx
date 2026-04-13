import React from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import Overview from './pages/Overview.jsx'
import Projects from './pages/Projects.jsx'
import Tickets from './pages/Tickets.jsx'
import Agents from './pages/Agents.jsx'
import Security from './pages/Security.jsx'
import Settings from './pages/Settings.jsx'
import Logs from './pages/Logs.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="projects" element={<Projects />} />
          <Route path="tickets" element={<Tickets />} />
          <Route path="agents" element={<Agents />} />
          <Route path="security" element={<Security />} />
          <Route path="settings" element={<Settings />} />
          <Route path="logs" element={<Logs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
