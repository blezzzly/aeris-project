import { useEffect, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { SurvivorModal } from './components/SurvivorModal';
import { Dashboard } from './pages/Dashboard';
import { LiveFeedPage } from './pages/LiveFeedPage';
import { DetectionsPage } from './pages/DetectionsPage';
import { MapPage } from './pages/MapPage';
import { SettingsPage } from './pages/SettingsPage';
import { useAppStore } from './stores/appStore';
import { useWebSocket, notifyAuthChanged } from './hooks/useWebSocket';
import { useHardwareStatus, useLinkState } from './hooks/useHardwareStatus';

function App() {
  const { activeTab, modalOpen } = useAppStore();
  const [loginToken, setLoginToken] = useState('');
  const [showLogin, setShowLogin] = useState<boolean>(() => !localStorage.getItem('aeris_token'));

  const handleLogin = () => {
    const t = loginToken.trim();
    if (!t) return;
    localStorage.setItem('aeris_token', t);
    setShowLogin(false);
    // The live channel (WebSocket) needs the credential that was just
    // stored. Without this notification the socket would stay closed and
    // every hardware indicator would keep showing OFFLINE / DISCONNECTED.
    notifyAuthChanged();
  };

  useEffect(() => {
    const onAuthFailed = () => setShowLogin(true);
    window.addEventListener('aeris-auth-failed', onAuthFailed);
    return () => window.removeEventListener('aeris-auth-failed', onAuthFailed);
  }, []);

  // The connection state comes from the socket itself (useWebSocket tracks the
  // real open/close events), so it can never drift out of sync. The banner
  // additionally distinguishes a stale transport from stale hardware data:
  // neither is allowed to look like a hardware failure.
  const { connected: wsConnected } = useWebSocket();
  const linkState = useLinkState();
  const hw = useHardwareStatus();

  const linkAgeLabel = hw.linkAgeS != null ? `${Math.round(hw.linkAgeS)}s` : null;
  const reportAgeLabel = hw.reportAgeS != null ? `${Math.round(hw.reportAgeS)}s` : null;
  const banner = (() => {
    if (!wsConnected) {
      if (linkState === 'connecting') {
        return {
          tone: 'warning' as const,
          text: 'CONNECTING TO THE GROUND STATION — opening the live hardware channel…',
        };
      }
      return {
        tone: 'danger' as const,
        text: `LIVE LINK OFFLINE — showing the last measured device state${reportAgeLabel ? ` from ${reportAgeLabel} ago` : ''}; reconnecting…`,
      };
    }
    if (linkState === 'stale' || linkState === 'offline') {
      return {
        tone: 'warning' as const,
        text: `LIVE LINK STALE — no message for ${linkAgeLabel ?? 'several seconds'}; showing the last measured device state.`,
      };
    }
    if (!hw.hardwareFresh) {
      return {
        tone: 'warning' as const,
        text: `LIVE LINK ACTIVE — no fresh hardware report${reportAgeLabel ? ` (${reportAgeLabel})` : ''}; showing the last measured device state.`,
      };
    }
    return null;
  })();

  useEffect(() => {
    if (wsConnected) return;
    // The live link is down. Telemetry is NEVER simulated locally: the backend is
    // the single source of truth for flight data (see ground-station/backend/main.py,
    // which refuses to fabricate readings). The last known values simply stay on
    // screen and are replaced as soon as the real producer reports again.
  }, [wsConnected]);

  useEffect(() => {
    const missionInterval = setInterval(() => {
      useAppStore.getState().incrementMissionTime();
    }, 1000);
    return () => clearInterval(missionInterval);
  }, []);

  useEffect(() => {
    if (modalOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
  }, [modalOpen]);

  if (showLogin) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-slate-100">
        <div className="panel-card max-w-md w-full p-6">
          <h2 className="text-lg font-bold text-slate-800 mb-2">AERIS Ground Station</h2>
          <p className="text-sm text-slate-500 mb-4">Enter your access token to open the dashboard.</p>
          <input
            type="password"
            value={loginToken}
            onChange={(e) => setLoginToken(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleLogin();
            }}
            placeholder="Access token"
            className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 mb-4 focus:outline-none focus:ring-2 focus:ring-aeris"
          />
          <button
            onClick={handleLogin}
            className="w-full py-2 bg-aeris hover:bg-aeris-dark text-white font-semibold rounded-lg transition-colors"
          >
            Connect
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen w-screen flex text-sm font-sans">
      <Sidebar />
      <main className="flex-1 flex flex-col relative overflow-hidden bg-slate-100">
        <Header />
        {banner && (
          <div
            className={`${
              banner.tone === 'danger' ? 'bg-red-500' : 'bg-amber-500'
            } text-white text-xs font-semibold px-4 py-1.5 flex items-center gap-2`}
          >
            {banner.text}
          </div>
        )}
        <div className="flex-1 overflow-y-auto lg:overflow-hidden p-4 lg:p-6 scroll-smooth">
          {activeTab === 'dashboard' && <Dashboard />}
          {activeTab === 'livefeed' && <LiveFeedPage />}
          {activeTab === 'detections' && <DetectionsPage />}
          {activeTab === 'map' && <MapPage />}
          {activeTab === 'settings' && <SettingsPage />}
        </div>
      </main>
      <SurvivorModal />
    </div>
  );
}

export default App;
