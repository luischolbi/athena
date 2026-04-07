import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Top20 from './pages/Top20';
import NewCompanies from './pages/NewCompanies';
import Pipeline from './pages/Pipeline';
import About from './pages/About';
import Toast from './components/Toast';

function App() {
  return (
    <BrowserRouter>
      <Toast />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/top20" element={<Top20 />} />
        <Route path="/new" element={<NewCompanies />} />
        <Route path="/pipeline" element={<Pipeline />} />
        <Route path="/about" element={<About />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
