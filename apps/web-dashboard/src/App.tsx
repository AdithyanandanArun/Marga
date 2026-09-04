import { Routes, Route, Navigate } from 'react-router-dom';
import { Dashboard } from './components/Dashboard';
import { DriverConsole } from './driver/DriverConsole';
import { ScenarioStudio } from './scenario/ScenarioStudio';
import { ReplayView } from './replay/ReplayView';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/driver" element={<DriverConsole />} />
      <Route path="/scenarios" element={<ScenarioStudio />} />
      <Route path="/replay/:incidentId?" element={<ReplayView />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
